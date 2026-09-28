"""Administrator research extracts: one row per qualifying registry record."""
import logging
import math
from collections import defaultdict

from django.conf import settings
from django.db import DatabaseError, connections
from django.db.models import TextField
from django.db.models.expressions import RawSQL

from . import services
from .models import NaaccrData, NaaccrPatientCensusLinking
from .cancer_center_access import get_cancer_center_access_lookup
from .stratification import API_CODES, StratificationUnavailable

logger = logging.getLogger(__name__)
PROVIDER_SOURCES = {
    'pcp': 'bg_2020_familypractice_fixepcp1_1220.RDS',
    'onc': 'bg_2020_familypractice_fixecnc1_1220.RDS',
    'ext_care': 'bg_2020_familypractice_fixecnc2_1220.RDS',
}
OUTPUTS = {
    'pcp': 'primary_care_providers_travel_adjusted_per_100k',
    'onc': 'oncology_providers_travel_adjusted_per_100k',
    'ext_care': 'extended_cancer_care_providers_travel_adjusted_per_100k',
    'mammo_fac': 'mammography_facilities_travel_adjusted_per_100k',
    'coc': 'nearest_coc_acad_drive_time',
    'nci': 'nearest_nci_drive_time',
}
RECORD_FIELDS = {
    'mid': 'patient_id', 'sequence_number': 'sequence_number',
    'dx_date': 'diagnosis_date', 'primary_site': 'primary_site',
    'hist_o3': 'histology_code', 'behavior': 'behavior_code',
    'age_at_dx': 'age_at_diagnosis', 'sex': 'sex_code',
    'race1': 'race_code', 'hispanic_origin': 'hispanic_origin_code',
    'stg_grp': 'summary_stage_code',
}
HEADERS = {
    'patient_id': 'Registry patient ID', 'sequence_number': 'Tumor sequence number',
    'diagnosis_date': 'Diagnosis date (registry format)', 'primary_site': 'Primary site (ICD-O-3)',
    'histology_code': 'Histology (ICD-O-3)', 'behavior_code': 'Behavior code (ICD-O-3)',
    'age_at_diagnosis': 'Age at diagnosis (registry code)', 'sex_code': 'Sex (registry code)',
    'race_code': 'Race 1 (registry code)', 'hispanic_origin_code': 'Hispanic origin (registry code)',
    'summary_stage_code': 'Summary Stage 2018 (registry code)',
    'block_group_geoid': 'Census block group GEOID', 'linkage_year': 'Geography linkage year',
    'linkage_status': 'Block group linkage status',
    'primary_care_providers_travel_adjusted_per_100k': 'Primary care providers per 100,000 (travel time-adjusted)',
}
BASE_COLUMNS = list(RECORD_FIELDS.values()) + ['block_group_geoid', 'linkage_year', 'linkage_status']


def patient_access_allowed(user):
    return bool(getattr(user, 'is_active', False) and
                (getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False)))


def linkage_year():
    # The loaded 2020 access surfaces match the 2023 block-group linkage.
    # Do not switch implicitly when an unrelated/new census vintage is loaded.
    return str(getattr(settings, 'POPCASE_PATIENT_ACCESS_LINKAGE_YEAR', '2023'))


def _block_group(raw):
    value = str(raw or '').strip()
    return value.zfill(12) if value.isdigit() and len(value) <= 12 else None


def _source_values(token):
    """Exact source selection; ambiguous/invalid observations remain missing."""
    result, seen = {}, set()
    try:
        alias = 'default' if token == 'mammo_fac' else 'popcase_manual_etl'
        with connections[alias].cursor() as cursor:
            if token == 'mammo_fac':
                cursor.execute('SELECT id, mammo_per_100k FROM public.fda_mammography_travel_bg')
            else:
                cursor.execute('SELECT geoid, "count.x" FROM public.travel_bg_2020 WHERE source_file = %s',
                               [PROVIDER_SOURCES[token]])
            for raw_geoid, raw_value in cursor.fetchall():
                geoid = _block_group(raw_geoid)
                if not geoid:
                    continue
                if geoid in seen:
                    result.pop(geoid, None)
                    continue
                seen.add(geoid)
                try:
                    value = float(raw_value) * (1 if token == 'mammo_fac' else 100_000)
                except (TypeError, ValueError, OverflowError):
                    continue
                if math.isfinite(value) and value >= 0:
                    result[geoid] = value
    except DatabaseError:
        logger.exception('Block-group access source unavailable for %s.', token)
        return {}
    return result


def get_block_group_access_lookup(requested):
    selected = set(requested) & OUTPUTS.keys()
    result = get_cancer_center_access_lookup('blockgroup', selected & {'nci', 'coc'})
    for token in OUTPUTS:
        if token in selected and token not in {'nci', 'coc'}:
            for geoid, value in _source_values(token).items():
                result.setdefault(geoid, {})[OUTPUTS[token]] = value
    return result


def get_patient_links(year):
    links = defaultdict(set)
    rows = NaaccrPatientCensusLinking.objects.filter(
        geographic_level='block_group', year=year).values_list('pat_id', 'geoid')
    for patient_id, raw_geoid in rows.iterator(chunk_size=5000):
        # Keep invalid links as None, so a valid + invalid pair is ambiguous.
        links[patient_id].add(_block_group(raw_geoid))
    return links


def _matches_demographics(record, filters):
    stages = services._as_list(filters.get('stage'))
    stage_codes = {'in_situ': ['0'], 'localized': ['1'], 'regional': ['2', '3', '4', '5'],
                   'metastatic': ['7'], 'unknown': ['9']}
    if set(stages) - stage_codes.keys():
        raise StratificationUnavailable('Unrecognized cancer stage filter.')
    if stages and set(stages) != stage_codes.keys():
        if record['stg_grp'] not in [c for s in stages for c in stage_codes[s]]:
            return False
    races = services._as_list(filters.get('race'))
    if races and 'all' not in races:
        codes = {'nh_white': {'01'}, 'nh_black': {'02'}, 'nh_aian': {'03'},
                 'nh_api': API_CODES, 'nh_other': {'96'}}
        if set(races) - (codes.keys() | {'hisp_any'}):
            raise StratificationUnavailable('Unrecognized race filter.')
        origin = str(record['hispanic_origin'] or '').strip()
        race = str(record['race1'] or '').strip().zfill(2)
        if not any(origin in set('12345678') if token == 'hisp_any'
                   else origin == '0' and race in codes[token] for token in races):
            return False
    groups = services._as_list(filters.get('age_groups'))
    ranges = [services.AGE_GROUP_RANGES[g] for g in groups if g in services.AGE_GROUP_RANGES]
    low, high = filters.get('age_from'), filters.get('age_to')
    if ranges or low not in (None, '') or high not in (None, ''):
        try:
            age = int(record['age_at_dx'])
        except (ValueError, TypeError):
            return False
        if not 0 <= age <= 120:
            return False
        if ranges:
            return any(a <= age and (b is None or age <= b) for a, b in ranges)
        return (low in (None, '') or age >= int(low)) and (high in (None, '') or age <= int(high))
    return True


def build_patient_dataset(year_range, filters, requested):
    selected = [token for token in OUTPUTS if token in requested]
    if not selected:
        raise StratificationUnavailable('Select at least one patient access measure on the Measures tab.')
    year = linkage_year()
    links = get_patient_links(year)
    access = get_block_group_access_lookup(selected)
    counties = services._selected_county_geoids(filters)
    # ctid protects distinct tumor records from the shared cancer-site UNION.
    # It is an internal query key only and is never exported.
    base = NaaccrData.objects.annotate(record_key=RawSQL('ctid::text', [], output_field=TextField()))
    if filters.get('exclude_multiple_primaries'):
        multiple_ids = NaaccrData.objects.filter(sequence_number__regex=r'^0*[1-9][0-9]*$').values('mid')
        base = base.exclude(mid__in=multiple_ids)
    query_filters = dict(filters, dx_start=year_range[0], dx_end=year_range[1],
                         race='all', race_ethnicity=[], age_groups=[], age_from=None, age_to=None,
                         geography='all_ohio', counties=[])
    records = services.apply_naaccr_filters(base, query_filters, geographic_level='patient').values(
        'record_key', *RECORD_FIELDS).order_by('mid', 'dx_date', 'sequence_number', 'record_key')
    result = []
    for record in records.iterator(chunk_size=5000):
        if not _matches_demographics(record, filters):
            continue
        candidates = links.get(record['mid'], set())
        geoid = next(iter(candidates)) if len(candidates) == 1 else None
        status = 'Linked' if geoid else 'Conflicting links' if len(candidates) > 1 else 'Missing link'
        if counties and (not geoid or geoid[:5] not in counties):
            continue
        row = {output: record[field] for field, output in RECORD_FIELDS.items()}
        row.update(block_group_geoid=geoid, linkage_year=year, linkage_status=status)
        row.update({OUTPUTS[token]: access.get(geoid, {}).get(OUTPUTS[token]) for token in selected})
        result.append(row)
    return result
