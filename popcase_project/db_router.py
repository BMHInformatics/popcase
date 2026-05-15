class PopcaseRouter:
    """
    Database router for PopCASE.

    Purpose
    -------
    1. Keep Django's built-in application tables on the default database:
       auth_user, django_session, django_admin_log, django_content_type, etc.
    2. Keep PopCASE's existing registry/community-data tables read-only from
       Django's migration system. Those tables are represented by unmanaged
       models in popcase/models.py using managed = False.
    3. Route selected manually loaded ETL tables to the popcase_manual_etl
       database when needed.

    Important
    ---------
    Returning False for every allow_migrate() blocks Django from creating
    auth_user and other required built-in tables. That was the reason
    createsuperuser failed with:

        relation "auth_user" does not exist
    """

    DJANGO_CORE_APPS = {
        "admin",
        "auth",
        "contenttypes",
        "sessions",
    }

    # Tables that live in the popcase_manual_etl database.
    # Add more manually loaded ETL table names here if needed.
    MANUAL_ETL_TABLES = {
        "cdc_places_tract_data_2024",
    }

    def _table_name(self, model):
        return getattr(getattr(model, "_meta", None), "db_table", None)

    def db_for_read(self, model, **hints):
        """
        Route read operations.

        Django core models should use default.
        Selected manually loaded ETL tables should use popcase_manual_etl.
        Everything else defaults to default.
        """
        if model._meta.app_label in self.DJANGO_CORE_APPS:
            return "default"

        if self._table_name(model) in self.MANUAL_ETL_TABLES:
            return "popcase_manual_etl"

        return "default"

    def db_for_write(self, model, **hints):
        """
        Route write operations.

        Django core models must be writable on default so login, sessions,
        admin, and createsuperuser work.

        PopCASE app models are intentionally not assigned a write database here.
        Because those models are managed = False, Django migrations will not
        create or alter their existing source tables. Returning None lets Django
        use normal behavior if a future managed PopCASE model is added.
        """
        if model._meta.app_label in self.DJANGO_CORE_APPS:
            return "default"

        return None

    def allow_relation(self, obj1, obj2, **hints):
        """
        Allow relations. This keeps Django admin/auth/session behavior simple
        and avoids blocking harmless relations.
        """
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        """
        Control which apps Django may migrate.

        Allow Django's built-in apps to migrate only on default.
        Do not migrate the PopCASE app's existing data models. They are
        unmanaged models mapped to existing tables.
        """
        if app_label in self.DJANGO_CORE_APPS:
            return db == "default"

        if app_label == "popcase":
            return False

        # For any future third-party app, allow migrations only on default.
        return db == "default"
