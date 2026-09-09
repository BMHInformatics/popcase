"""Sub-county incidence using the Software Design Document Controller Logic."""

from collections import defaultdict
import math

from django.db import connections
from django.db.models import Q

from .rate_statistics import (
    AGE_BANDS_20, AGE_BANDS_18, RateDataUnavailable, age_band_for_age,
    crude_rate, indirect_rate_ci, population_year_exposure, query_year_exposure,
    selected_age_bands,
)


RACES = {
    'nh_white': ('1', 'Non-Hispanic White'),
    'nh_black': ('2', 'Non-Hispanic Black'),
    'nh_aian': ('3', 'Non-Hispanic American Indian/Alaska Native'),
    'nh_api': ('4', 'Non-Hispanic Asian or Pacific Islander'),
    'hisp_any': ('5', 'Hispanic (All Races)'),
    'nh_other': (None, 'Other Non-Hispanic'),
}
API_RACE_CODES = ('04', '05', '06', '07', '08', '09', '10', '12', '13', '14', '15', '16')
P12_MALE_INDICES = ((3,), (4,), (5,), (6, 7), (8, 9, 10), (11,), (12,), (13,),
                    (14,), (15,), (16,), (17,), (18, 19), (20, 21), (22,), (23,), (24,), (25,))


def rate_linking_year(requested_year, geographic_level):
    # The loaded NCI tract series uses 2010 boundaries. The 2018 linking layer
    # matches those boundaries; 2023 uses 2020 boundaries. Never mix them.
    return '2018' if geographic_level == 'tract' else str(requested_year)


def _list(value):
    return list(value) if isinstance(value, (list, tuple, set)) else ([value] if value else [])


def demographic_selection(filters, geographic_level):
    from .services import _normalize_requested_sex, _sex_specific_cancer_sex_from_filters
    cancer_sexes = {_sex_specific_cancer_sex_from_filters(dict(filters, cancer_types=[site]))
                   for site in _list(filters.get('cancer_types'))}
    if len(cancer_sexes) > 1:
        raise RateDataUnavailable('Rates are unavailable for mixed sex-specific and other cancer selections.')
    sex = _normalize_requested_sex(filters)
    cancer_sex = _sex_specific_cancer_sex_from_filters(filters)
    if sex and cancer_sex and sex != cancer_sex:
        raise RateDataUnavailable('The selected sex does not match the sex-specific cancer.')
    sex = sex or cancer_sex
    races = tuple(sorted(set(str(r).lower() for r in _list(
        filters.get('race_ethnicity') or filters.get('race')) if str(r).lower() != 'all')))
    for race in races:
        if race not in RACES or (geographic_level in {'tract', 'county'} and RACES[race][0] is None):
            raise RateDataUnavailable('Population data do not support the selected race/ethnicity category.')
    return sex, races


def _population_label(band):
    return f'{band[0]}+' if band[1] is None else f'{band[0]}-{band[1]}'


def _population_exposures(rows, year_weights, bands, sexes, races, sparse_geo_years=None):
    """Validate every selected demographic cell before aggregating exposure.

    NCI extracts store positive cells only. For those sources, omitted cells in
    an independently verified geography/year are zeros. Entire missing years or
    geographies are never filled. Decennial cells must be explicitly present.
    """
    cells = {}
    geoids = set()
    if sparse_geo_years is not None:
        geoids.update(g for g, _ in sparse_geo_years)
    for geoid, year, band, sex, race, population in rows:
        geoids.add(geoid)
        key = (geoid, int(year), band, str(sex), str(race))
        if key in cells:
            raise RateDataUnavailable('Duplicate population cells remain after aggregation.')
        try:
            value = float(population)
        except (TypeError, ValueError):
            value = math.nan
        cells[key] = value
    valid, errors = {}, {}
    for geoid in geoids:
        exposure = defaultdict(float)
        try:
            for year, duration in year_weights.items():
                for band in bands:
                    for sex in sexes:
                        for race in races:
                            default = 0.0 if sparse_geo_years is not None and (geoid, year) in sparse_geo_years else None
                            value = cells.get((geoid, year, band, sex, race), default)
                            if value is None or not math.isfinite(value) or value < 0:
                                raise RateDataUnavailable(
                                    f'Population data are incomplete for {year} and the selected demographics.')
                            exposure[band] += duration * value
            valid[geoid] = dict(exposure)
        except RateDataUnavailable as exc:
            errors[geoid] = str(exc)
    return valid, errors


def load_target_populations(geographic_level, year_exposure, bands, sex, selected_races):
    if geographic_level in {'zcta', 'place'} and not selected_races:
        return load_decennial_population(year_exposure, bands, sex, geographic_level)
    is_tract = geographic_level == 'tract'
    weights = population_year_exposure(year_exposure, decennial=not is_tract)
    sexes = ('1', '2') if is_tract else ('Male', 'Female')
    if sex:
        sexes = (('1' if sex == 'male' else '2') if is_tract else sex.title(),)
    race_tokens = selected_races or tuple(k for k in RACES if not is_tract or RACES[k][0])
    races = tuple(RACES[r][0 if is_tract else 1] for r in race_tokens)
    if is_tract:
        table = 'age_adjustment_census_tract'
        geo = "state_fips || county_fips || tract"
        source_ages = tuple(f'{AGE_BANDS_20.index(b):02}' for b in bands)
        age_map = dict(zip(source_ages, bands))
    else:
        table = {'zcta': 'age_adjustment_zcta', 'place': 'age_adjustment_place'}[geographic_level]
        geo = 'RIGHT("GEOID", 5)' if geographic_level == 'zcta' else 'RIGHT("GEOID", 7)'
        source_ages = tuple(_population_label(b) for b in bands)
        age_map = dict(zip(source_ages, bands))
    with connections['popcase_manual_etl'].cursor() as cursor:
        sparse_geo_years = None
        if is_tract:
            cursor.execute(f'SELECT DISTINCT {geo}, year FROM {table} WHERE state_fips = %s AND year IN %s',
                           ['39', tuple(str(y) for y in weights)])
            sparse_geo_years = {(g, int(y)) for g, y in cursor.fetchall()}
            # Aggregate annual person-years in SQL instead of transferring millions of cells.
            cases = ' '.join('WHEN %s THEN %s' for _ in weights)
            params = [v for y, duration in weights.items() for v in (str(y), duration)]
            cursor.execute(f'''
                SELECT {geo}, age, SUM(population::numeric * (CASE year {cases} END)),
                       COUNT(*) FILTER (WHERE population IS NULL OR population::numeric < 0)
                FROM {table} WHERE state_fips = '39' AND year IN %s AND age IN %s
                    AND sex IN %s AND race IN %s GROUP BY 1, 2
            ''', params + [tuple(str(y) for y in weights), source_ages, sexes, races])
            values = {(g, age_map[a]): (p, invalid) for g, a, p, invalid in cursor.fetchall()}
            populations, errors = {}, {}
            for g in {g for g, _ in sparse_geo_years}:
                if any((g, y) not in sparse_geo_years for y in weights):
                    errors[g] = 'Population years are incomplete.'
                    continue
                cells = {b: values.get((g, b), (0, 0)) for b in bands}
                if any(p is None or invalid for p, invalid in cells.values()):
                    errors[g] = 'Population cells are invalid.'
                else:
                    populations[g] = {b: float(p) for b, (p, _) in cells.items()}
            return populations, errors
        # Identifiers above are fixed application constants. All values are bound.
        cursor.execute(f'''
            SELECT {geo}, year, age, sex, race,
                   CASE WHEN COUNT(*) FILTER (WHERE population IS NULL OR
                        BTRIM(population::text) !~ '^[0-9]+(\\.[0-9]+)?$') > 0
                        THEN NULL ELSE SUM(population::numeric) END
            FROM {table}
            WHERE state_fips = '39' AND year IN %s AND age IN %s
                  AND sex IN %s AND race IN %s
            GROUP BY 1, 2, 3, 4, 5
        ''', [tuple(str(y) for y in weights), source_ages, sexes, races])
        rows = [(str(g), y, age_map[a], s, r, p) for g, y, a, s, r, p in cursor.fetchall()]
    if not rows and not sparse_geo_years:
        raise RateDataUnavailable('No population records are available for the requested years and demographics.')
    return _population_exposures(rows, weights, bands, sexes, races, sparse_geo_years)


def load_ohio_decennial_population(year_exposure, bands, sex, races):
    if races:
        raise RateDataUnavailable(
            'Ohio decennial age-by-race reference populations are not available; '
            'the available statewide P12 tables contain all races combined.')
    populations, errors = load_decennial_population(year_exposure, bands, sex, 'state')
    if errors or '39' not in populations:
        raise RateDataUnavailable('Ohio decennial reference population is incomplete.')
    return populations['39']


def load_decennial_population(year_exposure, bands, sex, level):
    weights = population_year_exposure(year_exposure, decennial=True)
    cells = []
    for year, duration in population_year_exposure(year_exposure, decennial=True).items():
        columns = []
        by_band = {}
        for band in bands:
            indices = P12_MALE_INDICES[AGE_BANDS_18.index(band)]
            indices = tuple(i + shift for shift in ((0,) if sex == 'male' else (24,) if sex == 'female' else (0, 24)) for i in indices)
            fields = [f'P012{i:03}' if year == 2010 else f'P12_{i:03}N' for i in indices]
            by_band[band] = fields
            columns.extend(fields)
        where, params = '', []
        if level == 'zcta':
            # The raw ZCTA extract is national despite its "39" table name.
            with connections['popcase_manual_etl'].cursor() as cursor:
                cursor.execute('SELECT DISTINCT RIGHT("GEOID", 5) FROM age_adjustment_zcta WHERE state_fips = %s', ['39'])
                ohio_geoids = tuple(row[0] for row in cursor.fetchall())
            where, params = ' WHERE RIGHT("GEO_ID", 5) IN %s', [ohio_geoids]
        with connections['default'].cursor() as cursor:
            length = {'state': 2, 'zcta': 5, 'place': 7}[level]
            cursor.execute('SELECT RIGHT("GEO_ID", ' + str(length) + '), ' + ', '.join('"' + c + '"' for c in columns) +
                           f' FROM census_build.decennial_39_{level}_{year}' + where, params)
            rows = cursor.fetchall()
        for geoid, *values in rows:
            values = dict(zip(columns, values))
            for band, fields in by_band.items():
                try:
                    populations = [float(values[f]) for f in fields]
                    population = sum(populations) if all(math.isfinite(p) and p >= 0 for p in populations) else None
                except (ValueError, TypeError):
                    population = None
                cells.append((geoid, year, band, 'all', 'all', population))
    return _population_exposures(cells, weights, bands, ('all',), ('all',))


def validate_tract_rate_period(filters, default_year):
    years = query_year_exposure(filters, default_year)
    with connections['popcase_manual_etl'].cursor() as cursor:
        cursor.execute('SELECT DISTINCT year FROM age_adjustment_census_tract')
        available = {int(row[0]) for row in cursor.fetchall()}
    if not set(years) <= available:
        raise RateDataUnavailable(
            f'Tract incidence rates require population data for every selected year. '
            f'Available years are {min(available)}–{max(available)}; please change the diagnosis period.')


def load_ohio_annual_population(year_exposure, bands, sex, races):
    """Aggregate the 88 Ohio counties, independently of target geographic scope."""
    sexes = (1, 2) if sex is None else ((1,) if sex == 'male' else (2,))
    race_conditions, params = [], []
    for race in races:
        if race == 'hisp_any':
            race_conditions.append('origin = 1')
        else:
            race_conditions.append('(origin = 0 AND race = %s)')
            params.append(int(RACES[race][0]))
    where = ' AND (' + ' OR '.join(race_conditions) + ')' if races else ''
    age_ids = tuple(AGE_BANDS_20.index(b) for b in bands)
    with connections['default'].cursor() as cursor:
        cursor.execute('SELECT year, COUNT(DISTINCT county_fips) FROM population_county '
                       'WHERE state_fips = %s AND year IN %s GROUP BY year', ['39', tuple(year_exposure)])
        coverage = dict(cursor.fetchall())
        if any(coverage.get(y) != 88 for y in year_exposure):
            raise RateDataUnavailable('Ohio annual population coverage is incomplete for the requested years.')
        cursor.execute('''
            SELECT year, age, SUM(population), COUNT(DISTINCT county_fips),
                   COUNT(*) FILTER (WHERE population IS NULL OR population < 0)
            FROM population_county
            WHERE state_fips = '39' AND year IN %s AND age IN %s AND sex IN %s
        ''' + where + ' GROUP BY year, age', [tuple(year_exposure), age_ids, sexes] + params)
        rows = {(int(y), int(a)): (p, counties, invalid) for y, a, p, counties, invalid in cursor.fetchall()}
    exposure = defaultdict(float)
    for year, duration in year_exposure.items():
        for band in bands:
            row = rows.get((year, AGE_BANDS_20.index(band)))
            if row is None:
                exposure[band] += 0.0
                continue
            if row[2] or row[0] is None:
                raise RateDataUnavailable(f'Ohio annual reference population is incomplete for {year}.')
            exposure[band] += duration * float(row[0])
    return dict(exposure)


def load_case_counts(linking_year, geographic_level, filters, bands, sex, races, mortality=False):
    from .models import NaaccrData, NaaccrPatientCensusLinking
    from .services import apply_naaccr_filters, _normalize_geoid_for_level_value
    # Target geography never narrows the Ohio reference. Disease/time filters do.
    reference_filters = dict(filters, geography='all_ohio', race='all', race_ethnicity=[])
    if mortality:
        reference_filters.update(dx_start='', dx_end='', age_groups=[], age_from=None, age_to=None)
    if sex:
        reference_filters['sex'] = sex
    base = NaaccrData.objects.all()
    if races:
        race_q = Q()
        for token in races:
            if token == 'hisp_any':
                race_q |= Q(hispanic_origin__in=[str(n) for n in range(1, 9)])
            else:
                codes = {'nh_white': ('01',), 'nh_black': ('02',), 'nh_aian': ('03',),
                         'nh_api': API_RACE_CODES, 'nh_other': ('96',)}[token]
                race_q |= Q(hispanic_origin='0', race1__in=codes)
        base = base.filter(race_q)
    stages = _list(filters.get('stage'))
    if stages:
        stage_codes = {'in_situ': ('0',), 'localized': ('1',), 'regional': ('2', '3', '4', '5'),
                       'metastatic': ('7',), 'unknown': ('9',)}
        if any(s not in stage_codes for s in stages):
            raise RateDataUnavailable('Unrecognized cancer stage selection.')
        # All stage choices means no stage restriction.
        if set(stages) != set(stage_codes):
            base = base.filter(stg_grp__in=[v for s in stages for v in stage_codes[s]])
    if filters.get('exclude_multiple_primaries'):
        base = base.filter(sequence_number__in=('0', '00'))
    ohio_ids = NaaccrPatientCensusLinking.objects.filter(
        year=str(linking_year), geographic_level='state', geoid='39').values_list('pat_id', flat=True)
    # Filtering a union query is unsupported in Django; restrict before site unions.
    base = base.filter(mid__in=ohio_ids)
    qs = apply_naaccr_filters(base, reference_filters)
    if mortality:
        from .mortality_rates import death_ages
        cases = death_ages(qs.values_list('mid', 'vital_status', 'last_contact', 'birth_date',
            'cause_of_death', 'icd_revision', 'primary_site', 'hist_o3'), filters, geographic_level, bands)
    else:
        cases = dict(qs.values_list('mid', 'age_at_dx'))
    reference = defaultdict(int)
    for age in cases.values():
        band = age_band_for_age(age, geographic_level)
        if band in bands:
            reference[band] += 1
    target = defaultdict(int)
    target_age_counts = defaultdict(lambda: defaultdict(int))
    unknown = defaultdict(int)
    links = NaaccrPatientCensusLinking.objects.filter(
        year=str(linking_year), geographic_level=geographic_level,
        pat_id__in=cases).values_list('pat_id', 'geoid').distinct()
    for patient, raw_geoid in links:
        geoid = _normalize_geoid_for_level_value(raw_geoid, geographic_level)
        target[geoid] += 1
        band = age_band_for_age(cases[patient], geographic_level)
        if band in bands:
            target_age_counts[geoid][band] += 1
        elif band is None:
            unknown[geoid] += 1
    return dict(target), dict(target_age_counts), dict(reference), dict(unknown)


def subcounty_incidence(linking_year, geographic_level, filters, mortality=False):
    from .services import _geo_label, _geoid_in_scope
    if geographic_level == 'tract':
        validate_tract_rate_period(filters, linking_year)
    if geographic_level in {'zcta', 'place'} and filters.get('geography', 'all_ohio') not in ('', 'all_ohio', None):
        raise RateDataUnavailable(
            'County/catchment restrictions for ZCTA or Place require a geographic crosswalk. '
            'Select all Ohio or use tract geography; the restriction cannot be silently ignored.')
    bands = selected_age_bands(geographic_level, filters)
    sex, races = demographic_selection(filters, geographic_level)
    years = query_year_exposure(filters, linking_year)
    linking_year = rate_linking_year(linking_year, geographic_level)
    counts, age_counts, reference_cases, unknown = load_case_counts(
        linking_year, geographic_level, filters, bands, sex, races, mortality=mortality)
    if mortality and geographic_level == 'county':
        from .mortality_rates import load_county_population
        populations, population_errors = load_county_population(years, bands, sex, races)
    else:
        populations, population_errors = load_target_populations(geographic_level, years, bands, sex, races)
    reference_error = None
    try:
        if mortality and geographic_level == 'county':
            reference_population = {}
        elif geographic_level == 'tract':
            reference_population = load_ohio_annual_population(years, bands, sex, races)
        else:
            reference_population = load_ohio_decennial_population(years, bands, sex, races)
    except RateDataUnavailable as exc:
        reference_population, reference_error = {}, str(exc)
    results = []
    for geoid in sorted(set(populations) | set(population_errors) | set(counts)):
        if not _geoid_in_scope(geographic_level, geoid, filters):
            continue
        observed = counts.get(geoid, 0)
        population = populations.get(geoid)
        crude, adjusted = (None,) * 3, (None,) * 3
        message = population_errors.get(geoid)
        exposure = sum(population.values()) if population else None
        try:
            if population is None:
                raise RateDataUnavailable(message or 'Matching population data are unavailable for this geography.')
            crude = crude_rate(observed, exposure)
            if reference_error:
                raise RateDataUnavailable(reference_error)
            if unknown.get(geoid):
                raise RateDataUnavailable('Cases with unknown age prevent a matched indirect calculation.')
            if any(n > 0 and population.get(band, 0) <= 0 for band, n in age_counts.get(geoid, {}).items()):
                raise RateDataUnavailable('Observed cases have a zero population denominator in an age group.')
            if mortality and geographic_level == 'county':
                from .mortality_rates import direct_mortality
                adjusted = direct_mortality(age_counts.get(geoid, {}), population)
            else:
                if mortality and any(unknown.values()):
                    raise RateDataUnavailable('Ohio death ages are incomplete.')
                adjusted = indirect_rate_ci(observed, population, reference_cases, reference_population)
        except RateDataUnavailable:
            pass
        rounded_crude = tuple(round(v, 1) if v is not None else None for v in crude)
        rounded_adjusted = tuple(round(v, 1) if v is not None else None for v in adjusted)
        results.append({
            'geoid': geoid, 'label': _geo_label(geographic_level, geoid), 'case_count': observed,
            'population': exposure / sum(years.values()) if exposure is not None else None,
            'incidence_per_100k': rounded_crude[0],
            'crude_incidence_per_100k': rounded_crude[0],
            'crude_incidence_ci_lower': rounded_crude[1], 'crude_incidence_ci_upper': rounded_crude[2],
            'age_adjusted_per_100k': rounded_adjusted[0],
            'age_adjusted_ci_lower': rounded_adjusted[1], 'age_adjusted_ci_upper': rounded_adjusted[2],
        })
    return results
