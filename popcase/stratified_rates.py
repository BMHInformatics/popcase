"""Population-matched incidence for registry stratification groups."""
from math import sqrt
from collections import Counter
from .rate_statistics import RateDataUnavailable, selected_age_bands, query_year_exposure, crude_rate, AGE_BANDS_20, age_band_for_age, indirect_rate_ci
from .incidence_rates import demographic_selection, load_target_populations, load_ohio_annual_population, load_ohio_decennial_population
from .mortality_rates import load_county_population
from . import services

CRUDE_INCIDENCE = {'crude_inc_rate', 'crude_inc_ci'}
ADJUSTED_INCIDENCE = {'inc_rate', 'inc_ci'}
INCIDENCE_MEASURES = CRUDE_INCIDENCE | ADJUSTED_INCIDENCE
# Same US 2000 20-age standard used by the existing county mortality path.
STANDARD = dict(zip(AGE_BANDS_20, (3794901,15191619,19919840,20056779,19819518,
    18257225,17722067,19511370,22179956,22479229,19805793,17224359,13307234,
    10654272,9409940,8725574,7414559,4900234,2678567,1580606)))


def direct_incidence(records, population):
    counts = Counter(age_band_for_age(r.get('age_at_dx'), 'county') for r in records)
    if None in counts or any(b not in population for b in counts):
        raise RateDataUnavailable('Unknown or unmatched case ages prevent age adjustment.')
    if not population or any(p <= 0 for p in population.values()):
        raise RateDataUnavailable('A selected age group has no positive population denominator.')
    total_weight = sum(STANDARD[b] for b in population)
    weights = {b: STANDARD[b] / total_weight for b in population}
    rate = sum(weights[b] * counts[b] / p for b, p in population.items()) * 100000
    if not records:
        return 0, None, None
    se = sqrt(sum(weights[b]**2 * counts[b] / p**2 for b, p in population.items())) * 100000
    # Preserve the app's direct-rate normal approximation, with a lower bound of zero.
    return rate, max(0, rate - 1.96 * se), rate + 1.96 * se


def selected_cancer_sites(filters):
    """Explicit site selections; risk-factor bundles retain organ-site grouping."""
    keys = filters.get('cancer_types') or []
    _, metadata = services.load_cancer_logic()
    if not keys or any(not metadata.get(key) or metadata[key].get('risk_group') for key in keys):
        return {}
    return {key: metadata[key] for key in keys}


def group_demographics(variables, values, filters, level):
    selections = dict(filters)
    grouped = dict(zip(variables, values))
    if 'insurance' in grouped:
        raise RateDataUnavailable('No population denominator by insurance at diagnosis is available.')
    if 'sex' in grouped:
        sex = {'Male': 'male', 'Female': 'female'}.get(grouped['sex'])
        if not sex:
            raise RateDataUnavailable('No matching population denominator for unknown/other sex.')
        selections['sex'] = sex
    if 'race_eth' in grouped:
        label = grouped['race_eth'].removesuffix(' alone')
        races = {'Non-Hispanic White': 'nh_white', 'Non-Hispanic Black': 'nh_black',
                 'Non-Hispanic AI/AN': 'nh_aian', 'Non-Hispanic API': 'nh_api',
                 'Other Non-Hispanic': 'nh_other', 'Hispanic (any race)': 'hisp_any'}
        if label not in races:
            raise RateDataUnavailable('No matching population denominator for unknown race/ethnicity.')
        selections['race'] = selections['race_ethnicity'] = [races[label]]
    # Match the denominator to this site's selection, not the union of cancers.
    site_sex = {'Breast': 'female', 'Female genital system': 'female', 'Male genital system': 'male'}.get(grouped.get('site'))
    if 'site' in grouped:
        if grouped['site'].startswith(('Overlapping', 'Unclassified')):
            raise RateDataUnavailable('Cancer-site classification must be resolved before calculating a rate.')
        selections['cancer_types'] = []
        for key, meta in selected_cancer_sites(filters).items():
            if services.get_cancer_type_leaf_label(meta) == grouped['site']:
                selections['cancer_types'].append(key)
                if meta.get('Sites') == 'Breast':
                    site_sex = 'female'
    if site_sex:
        if selections.get('sex') not in (None, '', 'all', site_sex, site_sex.title(), '1' if site_sex == 'male' else '2'):
            raise RateDataUnavailable('The selected sex does not match the sex-specific cancer.')
        selections['sex'] = site_sex
    sex, races = demographic_selection(selections, level)
    bands = selected_age_bands(level, filters)
    age_label = grouped.get('age_broad', grouped.get('age_narrow'))
    if age_label:
        if age_label == 'Unknown age':
            raise RateDataUnavailable('No population denominator for unknown age.')
        if age_label.endswith('+'):
            low, high = int(age_label[:-1]), None
        elif '-' in age_label:
            low, high = map(int, age_label.split('-'))
        else:
            low = high = int(age_label)
        group_bands = selected_age_bands(level, {'age_from': low, 'age_to': high})
        bands = tuple(b for b in bands if b in group_bands)
        if not bands:
            raise RateDataUnavailable('The subgroup does not overlap the selected population ages.')
    return sex, races, bands


class IncidenceCalculator:
    def __init__(self, level, filters, variables, default_year):
        self.level = level
        self.population_level = 'county' if level in ('total', 'none') else level
        self.filters = filters
        self.variables = variables
        self.years = query_year_exposure(filters, default_year)
        self.cache = {}
        self.reference_counts = None
        self.reference_population_cache = {}

    def indirect(self, values, records, population, sex, races, bands):
        if self.reference_counts is None:
            raise RateDataUnavailable('Ohio subgroup reference counts are unavailable for indirect age adjustment.')
        ages = [age_band_for_age(record.get('age_at_dx'), self.level) for record in records]
        if any(age not in bands for age in ages):
            raise RateDataUnavailable('Unknown or unmatched case ages prevent indirect age adjustment.')
        if any(population.get(age, 0) <= 0 for age in ages):
            raise RateDataUnavailable('An observed case age group has no positive population denominator.')
        sexes = ('1',) if sex == 'male' else ('2',) if sex == 'female' else None
        counts = Counter()
        for (group, source_sex), source_counts in self.reference_counts.items():
            if group == tuple(values) and (sexes is None or source_sex in sexes):
                counts.update(source_counts)
        if None in counts:
            raise RateDataUnavailable('Unknown ages in the Ohio subgroup prevent indirect age adjustment.')
        signature = (sex, tuple(races), tuple(bands))
        if signature not in self.reference_population_cache:
            try:
                loader = load_ohio_annual_population if self.level == 'tract' else load_ohio_decennial_population
                self.reference_population_cache[signature] = loader(self.years, bands, sex, races)
            except RateDataUnavailable as exc:
                self.reference_population_cache[signature] = exc
        reference_population = self.reference_population_cache[signature]
        if isinstance(reference_population, RateDataUnavailable):
            raise reference_population
        return indirect_rate_ci(len(records), population, counts, reference_population)

    def calculate(self, geoid, values, records, measures):
        out = {}
        if 'crude_inc_rate' in measures: out['crude_incidence_per_100k'] = None
        if 'crude_inc_ci' in measures:
            out.update(crude_inc_ci_lower_per_100k=None, crude_inc_ci_upper_per_100k=None)
        if 'inc_rate' in measures: out['age_adjusted_per_100k'] = None
        if 'inc_ci' in measures:
            out.update(inc_ci_lower_per_100k=None, inc_ci_upper_per_100k=None)
        try:
            if self.level in ('zcta', 'place') and self.filters.get('geography', 'all_ohio') not in ('all_ohio', '', None):
                raise RateDataUnavailable('County-restricted ZCTA/place rates require a population crosswalk.')
            sex, races, bands = group_demographics(self.variables, values, self.filters, self.population_level)
            # Do not silently use a female denominator for male breast records,
            # or include unknown-sex records in a sex-specific numerator.
            if sex and any(str(r.get('sex') or '').strip() != ('1' if sex == 'male' else '2') for r in records):
                raise RateDataUnavailable('Some cases do not match the sex-specific population denominator.')
            # Cache across locations and cancer/stage/receptor groups; the
            # population denominator depends only on demographics and period.
            signature = (sex, tuple(races), tuple(bands))
            if signature not in self.cache:
                try:
                    loader = load_county_population if self.population_level == 'county' else None
                    result = loader(self.years, bands, sex, races) if loader else load_target_populations(self.population_level, self.years, bands, sex, races)
                    self.cache[signature] = result
                except RateDataUnavailable as exc:
                    self.cache[signature] = exc
            population_result = self.cache[signature]
            if isinstance(population_result, RateDataUnavailable): raise population_result
            populations, errors = population_result
            if geoid == 'total':
                geoids = services._selected_county_geoids(self.filters) or set(services.OHIO_COUNTY_NAMES)
            else:
                geoids = {geoid}
            if not geoids or any(g in errors or g not in populations for g in geoids):
                reason = next((errors[g] for g in geoids if g in errors), 'Matching population data are unavailable for this geography and period.')
                raise RateDataUnavailable(reason)
            exposure = sum(populations[g][band] for g in geoids for band in bands)
            estimate, low, high = crude_rate(len(records), exposure)
            if 'crude_inc_rate' in measures: out['crude_incidence_per_100k'] = round(estimate, 2)
            if 'crude_inc_ci' in measures:
                out.update(crude_inc_ci_lower_per_100k=round(low, 2), crude_inc_ci_upper_per_100k=round(high, 2))
            if measures & ADJUSTED_INCIDENCE:
                grouped_population = {band: sum(populations[g][band] for g in geoids) for band in bands}
                if self.level in ('county', 'total', 'none'):
                    adjusted, lower, upper = direct_incidence(records, grouped_population)
                else:
                    adjusted, lower, upper = self.indirect(values, records, grouped_population, sex, races, bands)
                if 'inc_rate' in measures: out['age_adjusted_per_100k'] = round(adjusted, 2)
                if 'inc_ci' in measures:
                    out.update(inc_ci_lower_per_100k=round(lower, 2) if lower is not None else None,
                               inc_ci_upper_per_100k=round(upper, 2) if upper is not None else None)
                    if lower is None or upper is None:
                        out['stratification_rate_note'] = 'The normal-approximation age-adjusted confidence interval is unavailable with zero events; the crude interval remains available.'
        except RateDataUnavailable as exc:
            out['stratification_rate_note'] = str(exc)
        return out
