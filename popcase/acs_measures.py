"""ACS community measures, with explicit universes and non-overlapping sums.

Column definitions: https://api.census.gov/data/2023/acs/acs5/groups.html
Approximate MOEs follow the ACS handbook's sum and proportion formulas.
No Census API access is needed at application runtime.
"""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Component:
    key: str
    label: str
    cells: tuple
    denominator: int = 1
    median: bool = False

    @property
    def columns(self):
        stem = self.key.removesuffix('_pct')
        return (self.key, stem + '_ci_lower', stem + '_ci_upper')


def pct(key, label, *cells):
    return Component(key, label, tuple(cells))


def age_cells(first, last):
    return tuple(range(first, last + 1)) + tuple(range(first + 24, last + 25))


# Each entry is (table code, UI group label, output components). Denominators
# are the published table totals, not NAACCR cases or ACS sample sizes.
ACS_MEASURES = {
    'age_dist': ('B01001', 'Age distribution', tuple(
        Component(f'age_{key}_pct', label, age_cells(first, last))
        for key, label, first, last in [
            ('0_14', 'Age 0–14 (%)', 3, 5), ('15_39', 'Age 15–39 (%)', 6, 13),
            ('40_49', 'Age 40–49 (%)', 14, 15), ('50_64', 'Age 50–64 (%)', 16, 19),
            ('65_79', 'Age 65–79 (%)', 20, 23), ('80_plus', 'Age 80+ (%)', 24, 25),
        ])),
    'marital_status': ('B07008', 'Marital status (age 15+)', (
        pct('marital_married_pct', 'Married, except separated (%)', 3),
        pct('marital_not_married_pct', 'Not married, including separated (%)', 2, 4, 5, 6),
    )),
    'educ_attain': ('B06009', 'Educational attainment (age 25+)', tuple(
        pct('education_' + key + '_pct', label, cell) for key, label, cell in [
            ('less_than_high_school', 'Less than high school (%)', 2),
            ('high_school', 'High school graduate or equivalent (%)', 3),
            ('some_college', "Some college or associate's degree (%)", 4),
            ('bachelors', "Bachelor's degree (%)", 5),
            ('graduate', 'Graduate or professional degree (%)', 6),
        ])),
    'lang_home': ('C16001', 'Language spoken at home (age 5+)', tuple(
        pct('language_' + key + '_pct', label + ' (%)', cell) for key, label, cell in [
            ('english', 'English only', 2), ('spanish', 'Spanish', 3),
            ('french', 'French, Haitian, or Cajun', 6),
            ('german', 'German or other West Germanic', 9),
            ('slavic', 'Russian, Polish, or other Slavic', 12),
            ('other_indo_european', 'Other Indo-European', 15),
            ('korean', 'Korean', 18), ('chinese', 'Chinese', 21),
            ('vietnamese', 'Vietnamese', 24), ('tagalog', 'Tagalog', 27),
            ('other_asian_pacific', 'Other Asian and Pacific Island', 30),
            ('arabic', 'Arabic', 33), ('other', 'Other and unspecified', 36),
        ])),
    'limited_english_pct': ('C16001', 'English proficiency (age 5+)', (
        pct('limited_english_pct', 'Speak English less than very well, age 5+ (%)', *range(5, 39, 3)),
    )),
    'citizenship': ('B07007', 'Citizenship (age 1+)', (
        pct('citizenship_native_pct', 'Native citizen (%)', 2),
        pct('citizenship_naturalized_pct', 'Naturalized citizen (%)', 4),
        pct('citizenship_non_citizen_pct', 'Non-citizen (%)', 5),
    )),
    'income_pov_ratio': ('B17026', 'Family income-to-poverty ratio', tuple(
        pct(f'income_poverty_band_{cell:02d}_pct', label + ' (%)', cell)
        for cell, label in enumerate([
            'Under 0.50', '0.50–0.74', '0.75–0.99', '1.00–1.24', '1.25–1.49',
            '1.50–1.74', '1.75–1.84', '1.85–1.99', '2.00–2.99', '3.00–3.99',
            '4.00–4.99', '5.00 and over',
        ], 2))),
    'median_housing_costs': ('B25105', 'Housing', (
        Component('median_housing_costs', 'Median monthly housing costs ($)', (1,), median=True),
    )),
    'occupants_per_room': ('B25014', 'Housing', (
        pct('occupants_per_room', 'Occupied housing units with >1 occupant per room (%)', 5, 6, 7, 11, 12, 13),
    )),
    'plumbing_complete': ('B25048', 'Housing', (
        pct('plumbing_complete_pct', 'Occupied housing units with complete plumbing (%)', 2),
    )),
    'kitchen_complete': ('B25052', 'Housing', (
        pct('kitchen_complete_pct', 'Occupied housing units with complete kitchen facilities (%)', 2),
    )),
    'female_headed': ('B11005', 'Household characteristics', (
        pct('female_headed_pct', 'Households with female householder, no spouse present (%)', 7, 10, 16, 19),
    )),
    'grandparents_care': ('B10063', 'Household characteristics', (
        pct('grandparents_care_pct', 'Households with grandparents responsible for own grandchildren under 18 (%)', 3),
    )),
    'internet_access': ('B28002', 'Household internet access', (
        pct('internet_access_pct', 'Any internet subscription (%)', 2),
        pct('internet_dialup_only_pct', 'Dial-up only (%)', 3),
        pct('internet_broadband_pct', 'Broadband of any type, including cellular (%)', 4),
        pct('internet_wired_pct', 'Cable, fiber optic, or DSL (%)', 7),
        pct('internet_cellular_only_pct', 'Cellular data plan only (%)', 6),
        pct('internet_satellite_only_pct', 'Satellite only (%)', 10),
        pct('internet_without_subscription_pct', 'Access without subscription (%)', 12),
        pct('internet_none_pct', 'No internet access (%)', 13),
    )),
}

COMPONENT_COLUMNS = {
    token: [column for component in components for column in component.columns]
    for token, (_, _, components) in ACS_MEASURES.items()
}
COMPONENT_CI_KEYS = {
    token: {column for component in components for column in component.columns[1:]}
    for token, (_, _, components) in ACS_MEASURES.items()
}
HEADER_MAP = {}
COLUMN_GROUPS = {}
for _token, (_, _group, _components) in ACS_MEASURES.items():
    for _component in _components:
        _value, _lower, _upper = _component.columns
        HEADER_MAP.update({_value: _component.label,
                           _lower: _component.label + ' CI 95% (L)',
                           _upper: _component.label + ' CI 95% (U)'})
        if len(_components) > 1:
            COLUMN_GROUPS.update({column: _group for column in _component.columns})


def numeric_value(value, annotation=None):
    """Missing, annotated and negative Census sentinel values are not zero.

    These measures use only nonnegative estimates/MOEs. An annotation marks
    a value requiring special interpretation (e.g. a bound or unavailable MOE).
    Leave it unavailable rather than using it as an ordinary estimate.
    """
    if annotation is not None and str(annotation).strip() not in ('', 'null', 'None'):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def read_sum(row, code, cells, suffix):
    values = []
    for cell in cells:
        column = f'{code}_{cell:03d}{suffix}'
        value, annotation = row.get(column), row.get(column + 'A')
        # The ***** / -555555555 MOE means a controlled population/housing
        # total, not a missing survey estimate. Its sampling-error contribution
        # to a derived sum or percentage is zero. Other sentinels stay missing.
        try:
            controlled = float(value) == -555555555
        except (TypeError, ValueError):
            controlled = False
        if suffix == 'M' and (str(annotation).strip() == '*****' or controlled):
            values.append(0.0)
        else:
            values.append(numeric_value(value, annotation))
    if not values or any(value is None for value in values):
        return None
    # ACS approximation: sum estimates, root-sum-square their MOEs.
    return sum(values) if suffix == 'E' else math.sqrt(sum(value * value for value in values))


def calculate_component(row, code, component):
    value_key, lower_key, upper_key = component.columns
    result = dict.fromkeys(component.columns)
    estimate = read_sum(row, code, component.cells, 'E')
    moe = read_sum(row, code, component.cells, 'M')
    factor = 1.96 / 1.645
    if estimate is None:
        return result
    if component.median:
        result[value_key] = round(estimate, 2)
        if moe is not None:
            result[lower_key] = round(max(0, estimate - factor * moe), 2)
            result[upper_key] = round(estimate + factor * moe, 2)
        return result

    denominator = read_sum(row, code, (component.denominator,), 'E')
    denominator_moe = read_sum(row, code, (component.denominator,), 'M')
    if denominator is None or denominator <= 0 or estimate > denominator:
        return result
    proportion = estimate / denominator
    result[value_key] = round(100 * proportion, 2)
    if moe is not None and denominator_moe is not None:
        numerator_variance = moe * moe
        denominator_term = proportion * proportion * denominator_moe * denominator_moe
        variance = numerator_variance - denominator_term
        if variance < 0 and math.isclose(numerator_variance, denominator_term, rel_tol=1e-12):
            variance = 0.0
        if variance < 0:
            # ACS fallback to the ratio approximation when subtraction fails.
            variance = numerator_variance + denominator_term
        half_width = 100 * factor * math.sqrt(variance) / denominator
        result[lower_key] = round(max(0, 100 * proportion - half_width), 2)
        result[upper_key] = round(min(100, 100 * proportion + half_width), 2)
    return result


def get_community_lookup(requested, geographic_level, acs_period):
    # Lazy import avoids coupling the declarative catalog to Django at import.
    from . import services

    by_table = {}
    for token in sorted(set(requested) & ACS_MEASURES.keys()):
        code, _, components = ACS_MEASURES[token]
        by_table.setdefault(code, []).extend(components)
    result = {}
    for code, components in by_table.items():
        table = services._resolve_acs_table(code, geographic_level, acs_period)
        if not table:
            continue
        cells = {cell for component in components for cell in component.cells}
        cells.update(component.denominator for component in components if not component.median)
        columns = {'geo': ['GEO_ID', 'geo_id', 'geoid', 'GEOID']}
        for cell in sorted(cells):
            for suffix in ('E', 'M', 'EA', 'MA'):
                column = f'{code}_{cell:03d}{suffix}'
                columns[column] = [column]
        where, params = services._community_where_sql(table, geographic_level, 'acs', acs_period)
        rows, aliases = services._select_columns_from_table(table, columns, where, params)
        for raw in rows:
            row = {alias: raw.get(actual) for alias, actual in aliases.items()}
            geoid = services._community_geoid_from_geo_id(row.get('geo'), geographic_level)
            if not geoid:
                continue
            output = result.setdefault(geoid, {})
            for component in components:
                output.update(calculate_component(row, code, component))
    return result
