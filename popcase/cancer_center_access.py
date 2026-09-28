"""Nearest cancer-center car travel times from population-weighted centroids."""
import logging

from django.db import DatabaseError, connections

logger = logging.getLogger(__name__)

# Explicit allowlist: geography names never come directly from SQL input.
MATRICES = {
    level: f'coc_travelmatrix_{level}_to_center_car_p50'
    for level in ('county', 'tract', 'zcta', 'blockgroup', 'place')
}
GEOID_WIDTHS = {'county': 5, 'tract': 11, 'zcta': 5, 'blockgroup': 12, 'place': 7}
CENTER_TYPES = {'nci': 'NCI', 'coc': 'Academic Comprehensive Cancer Program'}
OUTPUTS = {'nci': 'nearest_nci_drive_time', 'coc': 'nearest_coc_acad_drive_time'}


def get_cancer_center_access_lookup(geographic_level, requested):
    """Minimize each center category independently; missing routes stay missing.

    Values retain the source travel_time_p50 units. The center lookup is the
    authority for category membership, including for the denormalized BG table.
    """
    selected = sorted(set(requested) & OUTPUTS.keys())
    if not selected or geographic_level not in MATRICES:
        return {}
    result = {}
    try:
        with connections['default'].cursor() as cur:
            table = MATRICES[geographic_level]
            cur.execute('SELECT to_regclass(%s)', [f'public.{table}'])
            if cur.fetchone()[0] is None:
                logger.warning('Cancer-center drive times unavailable: missing %s', table)
                return {}
            # Keep represented origins even when the selected center category
            # has no usable route, including queries without matching patients.
            cur.execute(f'SELECT DISTINCT from_id FROM public."{table}" WHERE from_id IS NOT NULL')
            for (raw_geoid,) in cur.fetchall():
                geoid = str(raw_geoid).zfill(GEOID_WIDTHS[geographic_level])
                result[geoid] = {OUTPUTS[token]: None for token in selected}
            cur.execute(f'''
                SELECT m.from_id, c."Type", MIN(m.travel_time_p50)
                FROM public."{table}" m
                JOIN public.coc_travelmatrix_center_lookup c ON c.id = m.to_id
                WHERE c."Type" IN ({','.join(['%s'] * len(selected))})
                  AND m.travel_time_p50 >= 0 AND m.from_id IS NOT NULL
                GROUP BY m.from_id, c."Type"
            ''', [CENTER_TYPES[token] for token in selected])
            tokens = {CENTER_TYPES[token]: token for token in selected}
            for raw_geoid, category, travel_time in cur.fetchall():
                geoid = str(raw_geoid).zfill(GEOID_WIDTHS[geographic_level])
                result.setdefault(geoid, {})[OUTPUTS[tokens[category]]] = travel_time
    except DatabaseError:
        logger.exception('Cancer-center drive times unavailable for %s', geographic_level)
        return {}
    return result
