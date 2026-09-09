"""Provider measures mapped by the current Google Drive Software Design Document."""

import logging
import math
import os

from django.conf import settings
from django.db import DatabaseError

from .models import TravelTimeTract


logger = logging.getLogger(__name__)
SDD_PCP_TRACT_SOURCE = "pcp1_1220_tr.xlsx.xlsx"


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
        logger.exception("PCP tract measure unavailable: source query failed.")
        return {}
    if not seen:
        logger.warning("PCP tract measure unavailable: approved source_file has no rows.")
    if duplicate_geoids:
        logger.warning("PCP tract measure omitted %s duplicate tract IDs.", len(duplicate_geoids))
    return result
