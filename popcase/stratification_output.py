"""Arrange calculated subgroup rows without recomputing or averaging measures."""
from collections import OrderedDict
from itertools import groupby
from .stratification_categories import category_order


def row_groups(rows, key):
    return [{'label': value, 'rows': list(items)} for value, items in groupby(rows, key=lambda row: row.get(key))] if key else []


def comparison_sections(rows, state, columns, header_map, numeric_columns):
    row_key = 'stratum_' + state['row_variable'] if state.get('row_variable') else None
    column_key = 'stratum_' + state['col_variable'] if state.get('col_variable') else None
    table_key = 'stratum_' + state['table_variable'] if state.get('table_variable') else None
    sections = OrderedDict()
    for row in rows:
        sections.setdefault(row.get(table_key) if table_key else None, []).append(row)
    result = []
    for table_value in sorted(sections, key=lambda value: category_order(state.get('table_variable') or '', value)):
        source = sections[table_value]
        title = f'{header_map.get(table_key, table_key)}: {table_value}' if table_key else ''
        section_columns = [col for col in columns if col != table_key]
        if row_key in section_columns:
            section_columns.remove(row_key)
            section_columns.insert(0, row_key)
        headers = dict(header_map)
        numeric = list(numeric_columns)
        column_groups = {}
        arranged = source
        if column_key:
            identifiers = [c for c in section_columns if c in ('label', 'geoid', 'tract_geoid') or (c.startswith('stratum_') and c != column_key)]
            measures = [c for c in section_columns if c not in identifiers and c != column_key]
            values = sorted(set(row.get(column_key) for row in source), key=lambda value: category_order(state['col_variable'], value))
            pivot_keys = {(value, measure): f'comparison_{i}_{j}' for i, value in enumerate(values) for j, measure in enumerate(measures)}
            section_columns = identifiers + list(pivot_keys.values())
            for (value, measure), key in pivot_keys.items():
                headers[key] = header_map.get(measure, measure)
                column_groups[key] = str(value)
                if measure in numeric_columns: numeric.append(key)
            indexed = OrderedDict()
            seen = set()
            for row in source:
                identity = tuple(row.get(c) for c in identifiers)
                cell = (identity, row.get(column_key))
                if cell in seen:
                    raise ValueError('Duplicate subgroup rows cannot be combined without recalculating the measures.')
                seen.add(cell)
                target = indexed.setdefault(identity, {c: row.get(c) for c in identifiers})
                for measure in measures:
                    target[pivot_keys[(row.get(column_key), measure)]] = row.get(measure)
            arranged = list(indexed.values())
        if row_key:
            arranged = sorted(arranged, key=lambda row: (category_order(state['row_variable'], row.get(row_key)), str(row.get('label') or ''), str(row.get('geoid') or '')))
        result.append({'title': title, 'rows': arranged, 'columns': section_columns,
                       'header_map': headers, 'numeric_columns': numeric, 'column_groups': column_groups,
                       'row_key': row_key, 'row_label': header_map.get(row_key, row_key)})
    return result
