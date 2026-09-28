"""County assignments supplied by the 2020 geography crosswalks."""
from functools import lru_cache

from django.db import DatabaseError, connections

from .rate_statistics import RateDataUnavailable

TABLES = {'zcta': 'county2020_zcta2020_crosswalk', 'place': 'county2020_place2020_crosswalk'}


@lru_cache(maxsize=2)
def county_assignments(level):
    """Use the supplied assignment, not a new spatial/area-weighted allocation.

    The Place table's seven-digit Place GEOID is named zcta_geoid in the source.
    Duplicate identical pairs are harmless; conflicting assignments are errors.
    Fail closed if the source is unavailable, rather than return statewide data.
    """
    if level not in TABLES:
        raise ValueError('County crosswalks support ZCTA and Place only.')
    try:
        with connections['default'].cursor() as cursor:
            cursor.execute(f'SELECT county_geoid, zcta_geoid FROM public."{TABLES[level]}"')
            rows = cursor.fetchall()
    except DatabaseError as exc:
        raise RateDataUnavailable(f'The 2020 {level.upper()}–County crosswalk is unavailable.') from exc
    mapping = {}
    width = 5 if level == 'zcta' else 7
    for county, geoid in rows:
        county, geoid = str(county or '').strip(), str(geoid or '').strip()
        if not (county.isdigit() and len(county) == 5 and geoid.isdigit() and len(geoid) == width):
            raise RateDataUnavailable(f'The 2020 {level.upper()}–County crosswalk contains invalid geography IDs.')
        if geoid in mapping and mapping[geoid] != county:
            raise RateDataUnavailable(f'The 2020 {level.upper()}–County crosswalk contains conflicting assignments.')
        mapping[geoid] = county
    if not mapping:
        raise RateDataUnavailable(f'The 2020 {level.upper()}–County crosswalk is empty.')
    return mapping


def geoids_for_counties(level, counties):
    return {geoid for geoid, county in county_assignments(level).items() if county in counties}
