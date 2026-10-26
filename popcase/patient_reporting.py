"""Selectable patient registry fields and linked community measures (wireframe)."""
import json
from collections import defaultdict
from dataclasses import dataclass

from django.db import connection
from django.db.models import BooleanField, Case, TextField, Value, When
from django.db.models.expressions import RawSQL

from . import services, patient_access
from .models import NaaccrData, NaaccrPatientCensusLinking
from .stratification import StratificationUnavailable, cancer_site_conditions

VARIABLE_GROUPS = [
    ('Demographics', [('sex', 'Sex'), ('age_diagnosis', 'Age at diagnosis'),
        ('age_followup', 'Age at death or last follow-up'), ('race', 'Race'), ('ethnicity', 'Ethnicity')]),
    ('Location', [('county', 'County'), ('place', 'Census Designated Place / Municipality'),
        ('zcta', 'ZCTA'), ('tract', 'Census tract'), ('patient_rurality', 'Rurality (County RUCC and census tract RUCA codes)')]),
    ('Disease-focused', [('sequence', 'Tumor sequence number'), ('site', 'Cancer site/type'),
        ('histology', 'Histology'), ('lymphovascular', 'Lymphovascular invasion'),
        ('perineural', 'Perineural invasion'), ('nodes_examined', 'Lymph nodes examined'),
        ('nodes_positive', 'Lymph nodes positive'), ('tti_first', 'Time to treatment initiation (first modality)'),
        ('tti_modalities', 'Time to treatment initiation by modality'), ('gleason', 'Gleason score (prostate cancer only)')]),
]
VARIABLE_CHOICES = [choice for _, choices in VARIABLE_GROUPS for choice in choices]
RAW_FIELDS = {
    'sex': ['Sex'], 'race': ['Race 1'], 'ethnicity': ['Spanish/Hispanic Origin'],
    'sequence': ['Sequence Number_Central'], 'histology': ['Histologic Type ICD-O-3'],
    'lymphovascular': ['Lymphovascular Invasion'], 'perineural': ['Perineural Invasion'],
    'nodes_examined': ['Regional Nodes Examined'], 'nodes_positive': ['Regional Nodes Positive'],
    'site': ['Primary Site', 'Histologic Type ICD-O-3', 'Estrogen Receptor Summary',
             'Progesterone Receptor Summary', 'HER2 Overall Summary'],
}
TX_COLUMNS = {
    'coc_first_course': 'Date 1st Crs RX CoC', 'seer_initial': 'Date Initial RX SEER',
    'surgery': 'RX Date Surgery', 'definitive_surgery': 'RX Date Mst Defn Srg',
    'chemotherapy': 'RX Date Chemo', 'radiation': 'RX Date Radiation',
    'systemic': 'RX Date Systemic', 'hormone': 'RX Date Hormone',
    'biological_response_modifier': 'RX Date BRM', 'other': 'RX Date Other',
}
WIDTHS = {'county': 5, 'place': 7, 'zcta': 5, 'tract': 11, 'block_group': 12}
PATIENT_ID = 'Patient ID Number'


def registry_columns():
    # Schema introspection, not ORM fields: the loaded registry has many more
    # columns than the minimal unmanaged model. No patient data are cached.
    with connection.cursor() as cursor:
        cursor.execute("SELECT column_name FROM information_schema.columns "
                       "WHERE table_schema='public' AND table_name='naaccr_data' ORDER BY ordinal_position")
        return [row[0] for row in cursor.fetchall()]


def selected_variables(options):
    requested = set(options.get('patient_variables') or [])
    return [token for token, _ in VARIABLE_CHOICES if token in requested]


def patient_geographies(levels, year):
    links = {level: defaultdict(set) for level in levels}
    rows = NaaccrPatientCensusLinking.objects.filter(year=str(year), geographic_level__in=levels)
    for mid, level, raw in rows.values_list('pat_id', 'geographic_level', 'geoid').iterator(chunk_size=5000):
        value = str(raw or '').strip()
        geoid = value.zfill(WIDTHS[level]) if value.isdigit() and len(value) <= WIDTHS[level] else None
        links[level][mid].add(geoid)
    return links


def linked_geoid(links, level, mid):
    candidates = links.get(level, {}).get(mid, set())
    return next(iter(candidates)) if len(candidates) == 1 else None


def completed_age(birth, event):
    if birth is None or event is None or event < birth:
        return None
    age = event.year - birth.year - ((event.month, event.day) < (birth.month, birth.day))
    return age if 0 <= age <= 120 else None


def receptor_subtype(raw):
    if not str(raw.get('Primary Site') or '').startswith('C50'):
        return 'Not applicable (non-breast)'
    er, pr, her = [str(raw.get(column) or '').strip() for column in
        ('Estrogen Receptor Summary', 'Progesterone Receptor Summary', 'HER2 Overall Summary')]
    hr = '1' if '1' in (er, pr) else '0' if er == pr == '0' else None
    if hr is None or her not in {'0', '1'}:
        return 'Unknown receptor status'
    return {('1', '0'): 'HR+/HER2-', ('1', '1'): 'HR+/HER2+',
            ('0', '1'): 'HR-/HER2+', ('0', '0'): 'Triple negative'}[(hr, her)]


def derived_values(raw, selected):
    parse = services._parse_yyyymmdd
    diagnosis_dates = [parse(raw.get(name)) for name in ('Date of Diagnosis', 'Date Conclusive DX')]
    diagnosis = max((date for date in diagnosis_dates if date is not None), default=None)
    result = {}
    if 'age_diagnosis' in selected:
        result['age_at_diagnosis_years'] = completed_age(parse(raw.get('Date of Birth')), diagnosis)
    if 'age_followup' in selected:
        result['age_at_death_or_followup_years'] = completed_age(
            parse(raw.get('Date of Birth')), parse(raw.get('Date of Last Contact')))
    if set(selected) & {'tti_first', 'tti_modalities'}:
        dates = {token: parse(raw.get(column)) for token, column in TX_COLUMNS.items()}
        def days(date):
            value = (date - diagnosis).days if date and diagnosis else None
            return value if value is not None and value >= 0 else None
        if 'tti_first' in selected:
            # SDD: first-course CoC date, otherwise earliest available modality.
            first = dates['coc_first_course'] or min((d for d in dates.values() if d), default=None)
            result['time_to_first_treatment_days'] = days(first)
        if 'tti_modalities' in selected:
            result.update({f'time_to_{token}_days': days(date) for token, date in dates.items()})
    if 'gleason' in selected:
        value = None
        if str(raw.get('Primary Site') or '').strip().upper() == 'C619':
            value = services._parse_gleason_value(raw.get('Gleason Score Pathological'))
            if value is None:
                value = services._parse_gleason_value(raw.get('Gleason Score Clinical'))
        result['gleason_score'] = value
    return result


def community_options(options):
    level = options.get('patient_community_geography') or 'tract'
    if level not in {'tract', 'county', 'zcta', 'place'}:
        raise StratificationUnavailable('Choose a valid geography for patient community measures.')
    tokens = []
    for field in ('cancer_prevention', 'noncancer_health_status', 'patient_community_access', 'community_characteristics'):
        tokens.extend(options.get(field) or [])
    # These wireframe choices lack the requested source/definition; never substitute HRI or ADI.
    tokens = [t for t in tokens if t not in {'redlined_pct', 'svi_adi'} and
              (t != 'rurality' or level in {'county', 'tract'})]
    displays = [key for key in services.CI_DISPLAY_OPTION_TO_TOKENS if options.get(key)]
    if options.get('patient_community_access_ci'):
        displays.append({'tract': 'access_comm_tract_survey_ci', 'county': 'access_comm_county_survey_ci',
                         'place': 'access_comm_zcta_place_survey_ci', 'zcta': 'access_comm_zcta_place_survey_ci'}[level])
    if options.get('patient_community_age_adjusted') and level in {'county', 'place'}:
        displays.append(f'access_comm_{level}_survey_age_adjusted')
    return level, list(dict.fromkeys(tokens)), displays


@dataclass
class PatientReport:
    columns: list
    headers: dict
    rows: object


def prepare_patient_report(year_range, filters, options):
    """Prepare a stable schema and a streaming row iterator; no patient caching."""
    from . import views  # Reuse the same community column selection and labels.
    all_registry = bool(options.get('patient_all_registry'))
    selected = [] if all_registry else selected_variables(options)
    access_tokens = [] if all_registry else [t for t in patient_access.OUTPUTS if t in options.get('access_patient_measures', [])]
    community_level, community_tokens, displays = community_options(options)
    if not (all_registry or selected or access_tokens or community_tokens):
        raise StratificationUnavailable('Select at least one measure on the Measures tab.')
    available_columns = registry_columns()
    output_raw = available_columns if all_registry else list(dict.fromkeys(
        [PATIENT_ID] + [column for token in selected for column in RAW_FIELDS.get(token, [])]))
    needed_raw = set(output_raw)
    if set(selected) & {'age_diagnosis', 'age_followup', 'tti_first', 'tti_modalities'}:
        needed_raw.update(['Date of Birth', 'Date of Diagnosis', 'Date Conclusive DX', 'Date of Last Contact'])
    if set(selected) & {'tti_first', 'tti_modalities'}:
        needed_raw.update(TX_COLUMNS.values())
    if 'gleason' in selected:
        needed_raw.update(['Primary Site', 'Gleason Score Clinical', 'Gleason Score Pathological'])
    quote = connection.ops.quote_name
    if all_registry:
        raw_sql, raw_params = 'to_jsonb(naaccr_data)::text', []
    else:
        chosen = [name for name in available_columns if name in needed_raw]
        raw_sql = 'jsonb_build_object(' + ','.join('%s, ' + quote(name) for name in chosen) + ')::text'
        raw_params = chosen
    base = NaaccrData.objects.annotate(record_key=RawSQL('ctid::text', [], output_field=TextField()),
                                       registry_json=RawSQL(raw_sql, raw_params, output_field=TextField()))
    site_fields = {}
    if 'site' in selected:
        for index, (label, condition) in enumerate(cancer_site_conditions({}).items()):
            key = f'patient_site_{index}'
            base = base.annotate(**{key: Case(When(condition, then=Value(True)), default=Value(False), output_field=BooleanField())})
            site_fields[key] = label
    if filters.get('exclude_multiple_primaries'):
        multiple = NaaccrData.objects.filter(sequence_number__regex=r'^0*[1-9][0-9]*$').values('mid')
        base = base.exclude(mid__in=multiple)
    query_filters = dict(filters, dx_start=year_range[0], dx_end=year_range[1], race='all',
                         race_ethnicity=[], age_groups=[], age_from=None, age_to=None, geography='all_ohio', counties=[])
    records = services.apply_naaccr_filters(base, query_filters, geographic_level='patient').values(
        'record_key', 'registry_json', *patient_access.RECORD_FIELDS, *site_fields).order_by('mid', 'dx_date', 'sequence_number', 'record_key')
    year = patient_access.linkage_year()
    counties = services._selected_county_geoids(filters)
    levels = set(selected) & WIDTHS.keys()
    if counties: levels.add('county')
    if 'patient_rurality' in selected: levels.update(['county', 'tract'])
    if community_tokens: levels.add(community_level)
    if access_tokens: levels.add('block_group')
    links = patient_geographies(levels, year) if levels else {}
    access = patient_access.get_block_group_access_lookup(access_tokens) if access_tokens else {}
    rural = {level: services._get_most_recent_community_lookup(['rurality'], level)
             for level in ('county', 'tract')} if 'patient_rurality' in selected else {}
    community = {}
    community_columns = []
    community_headers = {}
    if community_tokens:
        # Community percentages refer to residents, not to the filtered registry cohort.
        community_geoids = {next(iter(values)) for values in links.get(community_level, {}).values()
                            if len(values) == 1 and None not in values}
        rows = services.build_geo_dataset(community_level, year_range, {}, [], community_tokens,
                                         displays, ['most_recent'], incidence_year=year,
                                         support_geoid_scope=community_geoids)
        schema_row = {}
        normalized = services._normalize_support_measure_tokens(community_tokens)
        services._add_display_option_columns(schema_row, normalized, displays)
        services._apply_display_option_contract(schema_row, normalized, displays)
        labels = views._with_dynamic_community_headers(views.DATASET_HEADER_MAP, rows, community_level)
        preferred = views._build_preferred_dataset_columns([], community_tokens, displays, community_level)
        community_columns = [col for col in views._build_dataset_columns(rows + [schema_row], labels, preferred)
                             if col not in {'label', 'geoid', 'tract_geoid'}]
        community = {row['geoid']: row for row in rows}
        community_headers = {'community_' + col: f"{community_level.title()} community: {labels.get(col, col)}"
                             for col in community_columns}
    columns = list(output_raw)
    columns += list(derived_values({}, selected))
    for level in WIDTHS:
        if level in selected:
            columns.extend([f'patient_{level}_geoid', f'patient_{level}_name'])
    if 'site' in selected: columns.extend(['cancer_site_type', 'breast_receptor_subtype'])
    if 'patient_rurality' in selected: columns.extend(['patient_county_rucc', 'patient_tract_ruca'])
    if levels: columns.append('geography_linkage_year')
    if access_tokens:
        columns.extend(['block_group_geoid', 'block_group_linkage_status'])
        columns.extend(patient_access.OUTPUTS[t] for t in access_tokens)
    if community_tokens:
        columns.extend(['community_geography', 'community_geoid', 'community_linkage_status'])
        columns.extend('community_' + c for c in community_columns)
    columns = list(dict.fromkeys(columns))
    headers = {c: c for c in output_raw}  # Preserve original registry names and codes.
    headers.update({c: c.replace('_', ' ').capitalize() for c in columns if c not in headers})
    headers.update({c: views.DATASET_HEADER_MAP.get(c, patient_access.HEADERS.get(c, c))
                    for c in patient_access.OUTPUTS.values() if c in columns})
    headers.update(community_headers)
    def status(level, mid):
        candidates = links.get(level, {}).get(mid, set())
        return 'Linked' if linked_geoid(links, level, mid) else 'Conflicting links' if len(candidates) > 1 else 'Missing link'
    def output_rows():
        for record in records.iterator(chunk_size=100):
            if not patient_access._matches_demographics(record, filters):
                continue
            mid = record['mid']
            if counties and linked_geoid(links, 'county', mid) not in counties:
                continue
            raw = json.loads(record['registry_json'])
            out = {column: raw.get(column) for column in output_raw}
            out.update(derived_values(raw, selected))
            for level in WIDTHS:
                if level in selected:
                    geoid = linked_geoid(links, level, mid)
                    out[f'patient_{level}_geoid'] = geoid
                    out[f'patient_{level}_name'] = services._geo_label(level, geoid) if geoid else None
            if 'site' in selected:
                labels = list(dict.fromkeys(label for key, label in site_fields.items() if record[key]))
                out['cancer_site_type'] = ' / '.join(labels) or 'Unclassified cancer site'
                out['breast_receptor_subtype'] = receptor_subtype(raw)
            if 'patient_rurality' in selected:
                for level, column in [('county', 'patient_county_rucc'), ('tract', 'patient_tract_ruca')]:
                    out[column] = rural[level].get(linked_geoid(links, level, mid), {}).get('rurality')
            if levels: out['geography_linkage_year'] = year
            if access_tokens:
                geoid = linked_geoid(links, 'block_group', mid)
                out.update(block_group_geoid=geoid, block_group_linkage_status=status('block_group', mid))
                out.update({patient_access.OUTPUTS[t]: access.get(geoid, {}).get(patient_access.OUTPUTS[t]) for t in access_tokens})
            if community_tokens:
                geoid = linked_geoid(links, community_level, mid)
                out.update(community_geography=community_level, community_geoid=geoid,
                           community_linkage_status=status(community_level, mid))
                out.update({'community_' + col: community.get(geoid, {}).get(col) for col in community_columns})
            yield out
    return PatientReport(columns, headers, output_rows())
