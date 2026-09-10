"""Registry-based stratification; geography-wide rates are never reused as subgroup rates."""
from collections import defaultdict, Counter
import csv
from math import sqrt
from pathlib import Path
from itertools import product

from django.db.models import Q, Subquery, TextField, Case, When, Value, BooleanField, IntegerField
from django.db.models.expressions import RawSQL
from django.db.models.functions import Cast
from .models import NaaccrData, NaaccrPatientCensusLinking
from . import services
from .stratified_rates import INCIDENCE_MEASURES, ADJUSTED_INCIDENCE, IncidenceCalculator, selected_cancer_sites
from .rate_statistics import age_band_for_age

AXES = ('row_variable', 'col_variable', 'table_variable')
LABELS = {'sex': 'Sex', 'race_eth': 'Race/Ethnicity', 'age_broad': 'Age (broad)',
          'age_narrow': 'Age (narrow)', 'site': 'Cancer site', 'stage': 'Cancer stage',
          'insurance': 'Insurance at diagnosis', 'receptor3': 'Breast receptor status (3 categories)', 'receptor4': 'Breast receptor status (4 categories)'}
MEASURES = {'case_count', 'pct_advanced', 'pct_advanced_ci', 'pct_metastatic',
            'pct_metastatic_ci', 'median_tti', 'median_tti_iqr', 'gleason', 'gleason_ci'}
STAGES = {'0': 'In situ', '1': 'Localized', '2': 'Regional', '3': 'Regional',
          '4': 'Regional', '5': 'Regional', '7': 'Distant', '9': 'Unknown'}
API_CODES = {'04', '05', '06', '07', '08', '09', '10', '12', '13', '14', '15', '16'}

class StratificationUnavailable(ValueError):
    pass


def cancer_site_conditions(filters):
    selected = selected_cancer_sites(filters)
    with Path(services.__file__).with_name('cancer_site_logic.csv').open(encoding='utf-8-sig', newline='') as source:
        definitions = list(selected.values()) if selected else list(csv.DictReader(source))
    conditions = {}
    for meta in definitions:
        label = services.get_cancer_type_leaf_label(meta) if selected else (meta.get('Sites') or '').strip()
        if label == 'Brain and Other Nervous System':
            label = 'Nervous system'
        script = (meta.get('QueryScript') or '').strip()
        if not label or not script:
            raise StratificationUnavailable('A cancer-site definition lacks its classification rule.')
        condition = services.parse_cancer_queryscript(script)
        conditions[label] = conditions[label] | condition if label in conditions else condition
    return conditions


def selected_variables(state):
    variables = [state.get(axis) for axis in AXES if state.get(axis)]
    if len(variables) != len(set(variables)):
        raise StratificationUnavailable('Choose a different variable for each stratification placement.')
    unsupported = [v for v in variables if v not in LABELS]
    if unsupported:
        raise StratificationUnavailable('Calculations are not yet available for: ' + ', '.join(unsupported) + '. Choose another stratification variable or None.')
    if {'age_broad', 'age_narrow'} <= set(variables) or {'receptor3', 'receptor4'} <= set(variables):
        raise StratificationUnavailable('Choose only one age grouping and one receptor grouping.')
    return variables


def group_label(record, variable, geographic_level):
    def code(field):
        return str(record.get(field) or '').strip()
    if variable == 'sex':
        return {'1': 'Male', '2': 'Female'}.get(code('sex'), 'Unknown/other sex')
    if variable == 'race_eth':
        origin, race = code('hispanic_origin'), code('race1').zfill(2)
        if origin in set('12345678'):
            return 'Hispanic (any race)'
        if origin != '0':
            return 'Unknown race/ethnicity'
        label = {'01': 'Non-Hispanic White', '02': 'Non-Hispanic Black', '03': 'Non-Hispanic AI/AN'}.get(race)
        if race in API_CODES:
            label = 'Non-Hispanic API'
        if label:
            return label + (' alone' if geographic_level in ('zcta', 'place') else '')
        return 'Other Non-Hispanic' if race == '96' else 'Unknown race/ethnicity'
    if variable.startswith('age_'):
        try:
            age = int(code('age_at_dx'))
        except ValueError:
            return 'Unknown age'
        if not 0 <= age <= 120:
            return 'Unknown age'
        if variable == 'age_broad':
            return '0-49' if age < 50 else '50-64' if age < 65 else '65-84' if age < 85 else '85+'
        if geographic_level in ('zcta', 'place'):
            if age < 5: return '0-4'
            if age >= 85: return '85+'
        if age == 0: return '0'
        if age < 5: return '1-4'
        if age >= 90: return '90+'
        low = age // 5 * 5
        return f'{low}-{low + 4}'
    if variable == 'insurance':
        payer = code('insurance_code').zfill(2)
        groups = {'20': 'Private', '21': 'Private', '60': 'Medicare', '61': 'Medicare',
                  '62': 'Medicare', '63': 'Medicare', '31': 'Medicaid', '35': 'Medicaid',
                  '64': 'Medicare/Medicaid dual eligible', '10': 'Insurance NOS',
                  '65': 'TRICARE/Military', '66': 'TRICARE/Military', '67': 'VA',
                  '68': 'Indian/Public Health Service', '01': 'Uninsured/Self-pay', '02': 'Uninsured/Self-pay'}
        return groups.get(payer, 'Insurance status unknown')
    if variable == 'stage':
        return STAGES.get(code('stg_grp'), 'Not applicable/unstaged')
    if variable == 'site':
        return record.get('site_group') or 'Unclassified cancer site'
    if variable.startswith('receptor'):
        if not code('primary_site').startswith('C50'):
            return 'Not applicable (non-breast)'
        er, her = code('er_summ'), code('her_summ')
        if her == '1' and variable == 'receptor3': return 'HER2+'
        if er not in ('0', '1') or her not in ('0', '1'): return 'Unknown receptor status'
        return {('1', '0'): 'ER+/HER2-', ('1', '1'): 'ER+/HER2+',
                ('0', '1'): 'ER-/HER2+', ('0', '0'): 'Triple negative'}[(er, her)]
    raise StratificationUnavailable('Unsupported stratification variable.')


def summarize(records, measures, details):
    out = {}
    if 'case_count' in measures:
        out['case_count'] = len(records)
    stages = [str(r.get('stg_grp') or '').strip() for r in records]
    denominator = sum(s in {'0', '1', '2', '3', '4', '5', '6', '7', '9'} for s in stages)
    for token, prefix, codes in [('pct_advanced', 'adv', {'2', '3', '4', '5', '7'}), ('pct_metastatic', 'meta', {'7'})]:
        if token not in measures and token + '_ci' not in measures:
            continue
        p = sum(s in codes for s in stages) / denominator if denominator else None
        if token in measures: out[token] = round(100 * p, 2) if p is not None else None
        if token + '_ci' in measures:
            se = sqrt(p * (1-p) / denominator) if p is not None else None
            out[prefix + '_ci_lower'] = round(100 * max(0, p - 1.96*se), 2) if p is not None else None
            out[prefix + '_ci_upper'] = round(100 * min(1, p + 1.96*se), 2) if p is not None else None
    for field, outputs in [
        ('tti_days', [('median_tti', 'median_tti', 'median'), ('median_tti_iqr', 'median_tti_iqr_lower', 'q1'), ('median_tti_iqr', 'median_tti_iqr_upper', 'q3')]),
        ('gleason', [('gleason', 'mean_gleason_score', 'mean'), ('gleason_ci', 'gleason_ci_lower', 'ci_lower'), ('gleason_ci', 'gleason_ci_upper', 'ci_upper')])]:
        if not any(token in measures for token, _, _ in outputs): continue
        summary = services._summarize_numeric_values([details.get(r.get('record_key', str(r['mid']).strip()), {}).get(field) for r in records])
        for token, key, stat in outputs:
            if token in measures:
                out[key] = round(summary[stat], 2) if summary[stat] is not None else None
    return out


def build_stratified_dataset(geographic_level, year_range, filters, disease_measures,
                             stratification, linking_year, _mortality=False):
    variables = selected_variables(stratification)
    if geographic_level == 'patient':
        raise StratificationUnavailable('Patient-level stratification is not yet available.')
    measures = set(disease_measures)
    from .stratified_mortality import MEASURE_MAP, mortality_rows, prepare_deaths
    mortality_measures = measures & MEASURE_MAP.keys()
    if mortality_measures:
        from .incidence_rates import rate_linking_year
        linking_year = rate_linking_year(linking_year, geographic_level)
        deaths = mortality_rows(geographic_level, year_range, filters, mortality_measures, stratification, linking_year)
        other = measures - mortality_measures
        if not other:
            return deaths
        cases = build_stratified_dataset(geographic_level, year_range, filters, other, stratification, linking_year)
        def identity(row):
            return (row['geoid'],) + tuple(row.get('stratum_' + v) for v in variables)
        combined = {identity(row): row for row in cases}
        for row in deaths:
            combined.setdefault(identity(row), {}).update(row)
        return list(combined.values())
    unsupported = measures - MEASURES - INCIDENCE_MEASURES
    if unsupported:
        raise StratificationUnavailable('Unsupported stratified measures: ' + ', '.join(sorted(unsupported)))
    if not measures:
        raise StratificationUnavailable('Select a disease measure to calculate within patient subgroups. Community measures describe the whole geographic area.')
    filters = dict(filters, dx_start=year_range[0], dx_end=year_range[1])
    query_filters = dict(filters)
    if _mortality:
        query_filters.update(dx_start='', dx_end='', age_groups=[], age_from=None, age_to=None)
    rate_calculator = None
    if measures & INCIDENCE_MEASURES:
        from .incidence_rates import rate_linking_year
        linking_year = rate_linking_year(linking_year, geographic_level)
        rate_calculator = IncidenceCalculator(geographic_level, filters, variables, linking_year)
    # Apply race and stage restrictions explicitly before the cancer-site union.
    # The unmanaged model declares patient ID as its key, but patients may have
    # multiple registry rows. ctid identifies a row only within this calculation;
    # it is never exported or used as a persistent patient/tumor identifier.
    base = NaaccrData.objects.all().annotate(record_key=RawSQL('ctid::text', [], output_field=TextField()))
    detail_fields = {}
    if measures & {'median_tti', 'median_tti_iqr'}:
        for i, column in enumerate(('Date Initial RX SEER', 'Date 1st Crs RX CoC', 'RX Date Surgery',
                'RX Date Chemo', 'RX Date Radiation', 'RX Date Systemic', 'RX Date Hormone',
                'RX Date BRM', 'RX Date Other', 'RX Date Mst Defn Srg')):
            detail_fields['tx_' + str(i)] = RawSQL('"' + column + '"', [], output_field=TextField())
    if measures & {'gleason', 'gleason_ci'}:
        detail_fields['gleason_clinical'] = RawSQL('"Gleason Score Clinical"', [], output_field=TextField())
        detail_fields['gleason_pathological'] = RawSQL('"Gleason Score Pathological"', [], output_field=TextField())
    if detail_fields:
        base = base.annotate(**detail_fields)
    stages = services._as_list(filters.get('stage'))
    stage_codes = {'in_situ': ['0'], 'localized': ['1'], 'regional': ['2', '3', '4', '5'], 'metastatic': ['7'], 'unknown': ['9']}
    if set(stages) - set(stage_codes):
        raise StratificationUnavailable('Unrecognized cancer stage filter.')
    if stages and set(stages) != set(stage_codes):
        base = base.filter(stg_grp__in=[c for s in stages for c in stage_codes.get(s, [])])
    if filters.get('exclude_multiple_primaries'):
        base = base.filter(sequence_number__in=['0', '00'])
    if query_filters.get('age_groups') or any(query_filters.get(key) not in (None, '') for key in ('age_from', 'age_to')):
        base = base.alias(strat_valid_age=Case(
            When(age_at_dx__regex=r'^[0-9]{1,3}$', then=Cast('age_at_dx', IntegerField())),
            default=Value(None), output_field=IntegerField(),
        )).filter(strat_valid_age__gte=0, strat_valid_age__lte=120)
    races = services._as_list(filters.get('race'))
    if races and 'all' not in races:
        race_query = Q()
        for race in races:
            if race == 'hisp_any': race_query |= Q(hispanic_origin__in=list('12345678'))
            else:
                codes = {'nh_white': ['01'], 'nh_black': ['02'], 'nh_aian': ['03'], 'nh_api': list(API_CODES), 'nh_other': ['96']}.get(race)
                if codes is None: raise StratificationUnavailable('Unrecognized race filter.')
                race_query |= Q(hispanic_origin='0', race1__in=codes)
        base = base.filter(race_query)
    if 'insurance' in variables:
        base = base.annotate(insurance_code=RawSQL('"Primary Payer at DX"', [], output_field=TextField()))
    site_fields = {}
    if 'site' in variables:
        # Compile the shared cancer definitions into this same registry query,
        # so site assignment refers to the tumor row, never just its patient ID.
        conditions = cancer_site_conditions(filters)
        annotations = {}
        for i, (label, condition) in enumerate(conditions.items()):
            field = 'strat_site_' + str(i)
            site_fields[field] = label
            annotations[field] = Case(When(condition, then=Value(True)), default=Value(False), output_field=BooleanField())
        base = base.annotate(**annotations)
    filtered = services.apply_naaccr_filters(base, dict(query_filters, race='all', race_ethnicity=[]))
    fields = ['record_key', 'mid', 'dx_date', 'sex', 'race1', 'hispanic_origin', 'age_at_dx', 'primary_site', 'stg_grp', 'er_summ', 'her_summ']
    if 'insurance' in variables: fields.append('insurance_code')
    fields.extend(detail_fields)
    fields.extend(site_fields)
    if _mortality:
        fields.extend(['vital_status', 'last_contact', 'birth_date', 'cause_of_death', 'icd_revision', 'hist_o3'])
    records = list(filtered.values(*fields))
    reference_records = []
    if measures & ADJUSTED_INCIDENCE and geographic_level in ('tract', 'zcta', 'place'):
        # The Ohio reference uses the same disease/demographic/time restrictions,
        # but never the target county/catchment restriction.
        ohio_ids = NaaccrPatientCensusLinking.objects.filter(
            year=str(linking_year), geographic_level='state', geoid='39').values('pat_id')
        reference_query = services.apply_naaccr_filters(
            base.filter(mid__in=Subquery(ohio_ids)),
            dict(query_filters, geography='all_ohio', counties=[], race='all', race_ethnicity=[]))
        reference_records = list(reference_query.values(*fields))
    if set(variables) & {'receptor3', 'receptor4'} and any(
            not str(record.get('primary_site') or '').strip().upper().startswith('C50')
            for record in records):
        raise StratificationUnavailable('Receptor stratification requires a breast-cancer-only selection in Filters.')
    if 'site' in variables:
        for record in records + reference_records:
            labels = sorted(label for field, label in site_fields.items() if record[field])
            record['site_group'] = labels[0] if len(labels) == 1 else 'Unclassified cancer site' if not labels else 'Overlapping site definitions: ' + ' / '.join(labels)
    mortality_error = reference_error = ''
    if _mortality:
        records, mortality_error = prepare_deaths(records, filters, geographic_level, variables)
        reference_records, reference_error = prepare_deaths(reference_records, filters, geographic_level, variables)
    by_id = defaultdict(list)
    for record in records:
        by_id[record['mid']].append(record)
    if measures & ADJUSTED_INCIDENCE and geographic_level in ('tract', 'zcta', 'place'):
        rate_calculator.reference_counts = defaultdict(Counter)
        for record in reference_records:
            group = tuple(group_label(record, variable, geographic_level) for variable in variables)
            source_sex = str(record.get('sex') or '').strip()
            age = age_band_for_age(record.get('age_at_dx'), geographic_level)
            rate_calculator.reference_counts[(group, source_sex)][age] += 1
    details = {}
    for record in records:
        detail = {'tti_days': None, 'gleason': None}
        dx = services._parse_yyyymmdd(record.get('dx_date'))
        dates = [services._parse_yyyymmdd(record.get(name)) for name in detail_fields if name.startswith('tx_')]
        dates = [date for date in dates if date is not None]
        if dx is not None and dates:
            days = (min(dates) - dx).days
            if 0 <= days <= 365: detail['tti_days'] = days
        if str(record.get('primary_site') or '').strip().upper() == 'C619':
            detail['gleason'] = services._parse_gleason_value(record.get('gleason_pathological'))
            if detail['gleason'] is None:
                detail['gleason'] = services._parse_gleason_value(record.get('gleason_clinical'))
        details[record['record_key']] = detail
    level = geographic_level
    grouped = defaultdict(dict)
    if geographic_level in ('total', 'none'):
        # No geographic comparison does not require a census link. Explicit
        # county filters have already restricted the registry query above.
        for record in records:
            key = ('total',) + tuple(group_label(record, v, geographic_level) for v in variables)
            grouped[key][record['record_key']] = record
    else:
        links = NaaccrPatientCensusLinking.objects.filter(geographic_level=level, year=str(linking_year), pat_id__in=Subquery(filtered.values('mid'))).values_list('pat_id', 'geoid').distinct()
        for mid, raw_geoid in links:
            geoid = services._normalize_geoid_for_level_value(raw_geoid, level)
            if not geoid or not services._geoid_in_scope(level, geoid, filters): continue
            for record in by_id[mid]:
                key = (geoid,) + tuple(group_label(record, v, geographic_level) for v in variables)
                grouped[key][record['record_key']] = record
    from .stratification_categories import category_values, category_order
    domains = [category_values(v, [group_label(r, v, level) for r in records], filters, level) for v in variables]
    geoids = {key[0] for key in grouped}
    if level in ('total', 'none'):
        geoids = {'total'}
    elif level == 'county':
        geoids.update(services._selected_county_geoids(filters) or services.OHIO_COUNTY_NAMES)
    else:
        # Enumerate linked locations independently of matching tumors, so an
        # empty query in a known location still has explicit zero case counts.
        for raw_geoid in NaaccrPatientCensusLinking.objects.filter(
                geographic_level=level, year=str(linking_year)).values_list('geoid', flat=True).distinct():
            geoid = services._normalize_geoid_for_level_value(raw_geoid, level)
            if geoid and services._geoid_in_scope(level, geoid, filters):
                geoids.add(geoid)
    for geoid in geoids:
        for combination in product(*domains):
            grouped.setdefault((geoid,) + combination, {})
    rows = []
    for key in sorted(grouped, key=lambda k: tuple(category_order(v, value) for v, value in zip(variables, k[1:])) + ((0, 0, k[0]),)):
        geoid = key[0]
        row = {'label': 'All selected locations' if geoid == 'total' else services._geo_label(level, geoid), 'geoid': geoid}
        row.update({'stratum_' + variable: value for variable, value in zip(variables, key[1:])})
        row.update(summarize(list(grouped[key].values()), measures, details))
        if rate_calculator:
            row.update(rate_calculator.calculate(geoid, key[1:], list(grouped[key].values()), measures))
        if _mortality and mortality_error:
            for field in list(row):
                if field not in ('label', 'geoid') and not field.startswith('stratum_'):
                    row[field] = None
            row['stratification_rate_note'] = mortality_error
        elif _mortality and reference_error and level in ('tract', 'zcta', 'place'):
            for field in ('age_adjusted_per_100k', 'inc_ci_lower_per_100k', 'inc_ci_upper_per_100k'):
                if field in row:
                    row[field] = None
            row['stratification_rate_note'] = reference_error
        rows.append(row)
    return rows
