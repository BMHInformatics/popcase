"""Provider measures mapped by the current Google Drive Software Design Document."""

import logging
import math
import os

from django.conf import settings
from django.db import DatabaseError, connections

from .models import TravelTimeTract


logger = logging.getLogger(__name__)
SDD_PCP_TRACT_SOURCE = "pcp1_1220_tr.xlsx.xlsx"
TRACT_PROVIDER_SOURCES = {'onc': 'cnc1_1220_tr.xlsx.xlsx', 'ext_care': 'cnc2_1220_tr.xlsx.xlsx'}
TRACT_ACCESS_OUTPUTS = {
    'onc': 'oncology_providers_travel_adjusted_per_100k',
    'ext_care': 'extended_cancer_care_providers_travel_adjusted_per_100k',
    'mammo_access': 'mammography_facilities_travel_adjusted_per_100k',
}


def get_pcp_tract_lookup():
    """Return the SDD's travel-adjusted PCPs per 100,000, for one approved file.

    The travel table contains multiple specialties and variants for every tract.
    Neither their filename ordering nor their prefixes establish the approved PCP
    definition. The current SDD supplies an exact default source and specifies
    count.x * 100,000. See docs/provider-access.md for source evidence.
    """
    source = getattr(settings, "POPCASE_PCP_TRACT_SOURCE_FILE", None)
    if source is None:
        source = os.environ.get("POPCASE_PCP_TRACT_SOURCE_FILE", SDD_PCP_TRACT_SOURCE)
    if not isinstance(source, str) or not source.strip():
        logger.warning("PCP tract measure unavailable: no approved source_file configured.")
        return {}
    source = source.strip()
    return _get_tract_provider_source(source)


def _get_tract_provider_source(source):
    """Read a single approved specialty and apply the SDD's count.x scaling."""
    result = {}
    seen = set()
    duplicate_geoids = set()
    try:
        rows = (
            TravelTimeTract.objects.using("popcase_manual_etl")
            .filter(source_file=source)
            .values("tract_geoid", "count_x")
            .iterator(chunk_size=5000)
        )
        for row in rows:
            geoid = str(row["tract_geoid"] or "").strip()
            if not geoid:
                continue
            if geoid in seen:
                duplicate_geoids.add(geoid)
                result.pop(geoid, None)
                continue
            seen.add(geoid)
            try:
                value = float(row["count_x"])
            except (TypeError, ValueError, OverflowError):
                continue
            scaled = value * 100_000
            if value >= 0 and math.isfinite(scaled):
                result[geoid] = scaled
    except DatabaseError:
        logger.exception("Tract provider measure unavailable: source query failed for %s.", source)
        return {}
    if not seen:
        logger.warning("Tract provider measure unavailable: %s has no rows.", source)
    if duplicate_geoids:
        logger.warning("Tract provider measure %s omitted %s duplicate tract IDs.", source, len(duplicate_geoids))
    return result


def get_mammography_tract_lookup():
    """The source mammo_per_100k is already scaled; never rescale it."""
    result, seen, duplicates = {}, set(), set()
    try:
        with connections['default'].cursor() as cursor:
            cursor.execute('SELECT id, mammo_per_100k FROM public.fda_mammography_travel_tract')
            for raw_geoid, raw_value in cursor.fetchall():
                geoid = str(raw_geoid or '').strip()
                if not geoid.isdigit() or len(geoid) > 11:
                    continue
                geoid = geoid.zfill(11)
                if geoid in seen:
                    duplicates.add(geoid)
                    result.pop(geoid, None)
                    continue
                seen.add(geoid)
                try:
                    value = float(raw_value)
                except (TypeError, ValueError, OverflowError):
                    continue
                if math.isfinite(value) and value >= 0:
                    result[geoid] = value
    except DatabaseError:
        logger.exception('Tract mammography measure unavailable: source query failed.')
        return {}
    if duplicates:
        logger.warning('Tract mammography omitted %s duplicate tract IDs.', len(duplicates))
    return result


def get_tract_access_lookup(requested):
    result = {}
    for token in sorted(set(requested) & TRACT_ACCESS_OUTPUTS.keys()):
        values = (get_mammography_tract_lookup() if token == 'mammo_access'
                  else _get_tract_provider_source(TRACT_PROVIDER_SOURCES[token]))
        for geoid, value in values.items():
            result.setdefault(geoid, {})[TRACT_ACCESS_OUTPUTS[token]] = value
    return result
