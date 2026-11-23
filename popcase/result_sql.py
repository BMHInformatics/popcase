"""Capture the PostgreSQL statements behind an authorized results page."""

from contextlib import ExitStack
from contextvars import ContextVar
from functools import lru_cache, wraps
from json import dumps

from django.conf import settings
from django.db import connections


_active_trace = ContextVar("result_sql_trace", default=None)


class ResultSQLTrace:
    def __init__(self):
        self.statements = []
        self.lookup_cache = {}
        self.complete = True

    def execute(self, execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        query = getattr(context["cursor"].cursor, "query", None)
        if many or not query:
            self.complete = False
        else:
            statement = query.decode("utf-8") if isinstance(query, bytes) else query
            self.statements.append((context["connection"].alias, statement))
        return result

    def as_sql(self):
        if not self.complete or not self.statements:
            return ""
        metadata = {}
        lines = []
        for alias, statement in self.statements:
            if alias not in metadata:
                # Use the same PostgreSQL session without recording this metadata lookup.
                with connections[alias].connection.cursor() as cursor:
                    cursor.execute("SELECT current_database(), current_schema(), current_schemas(false)")
                    database, schema, search_path = cursor.fetchone()
                metadata[alias] = (
                    f"-- Database: {dumps(database)} | Current schema: {dumps(schema)}"
                    f" | Schema search path: {dumps(search_path)}"
                )
            lines.append(f"{metadata[alias]}\n{statement.rstrip().removesuffix(';')};")
        return "\n\n".join(lines) + "\n"


def capture_results_sql(view):
    @wraps(view)
    def capture(request, *args, **kwargs):
        user = request.user
        if not (getattr(user, "is_active", False) and getattr(user, "is_staff", False)):
            return view(request, *args, **kwargs)
        if user.get_username() == "popcase-demo" and not settings.POPCASE_DEMO_LOGIN_ENABLED:
            return view(request, *args, **kwargs)

        trace = ResultSQLTrace()
        token = _active_trace.set(trace)
        try:
            with ExitStack() as stack:
                for connection in connections.all():
                    stack.enter_context(connection.execute_wrapper(trace.execute))
                response = view(request, *args, **kwargs)
        finally:
            _active_trace.reset(token)
        response["Cache-Control"] = "private, no-store"
        return response

    return capture


def current_results_sql():
    trace = _active_trace.get()
    return trace.as_sql() if trace is not None else ""


def result_lookup_cache(maxsize):
    """Keep report lookups local to a capture so cached data has SQL provenance."""
    def decorate(function):
        cached = lru_cache(maxsize=maxsize)(function)

        @wraps(function)
        def lookup(*args, **kwargs):
            trace = _active_trace.get()
            if trace is None:
                return cached(*args, **kwargs)
            key = (function, args, tuple(kwargs.items()))
            if key not in trace.lookup_cache:
                trace.lookup_cache[key] = function(*args, **kwargs)
            return trace.lookup_cache[key]

        lookup.cache_clear = cached.cache_clear
        lookup.cache_info = cached.cache_info
        return lookup

    return decorate
