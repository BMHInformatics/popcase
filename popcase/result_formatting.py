"""Shared display precision for result tables and CSV exports."""
from decimal import Decimal
from numbers import Real

WHOLE_NUMBER_COLUMNS = {
    'case_count', 'total_population', 'total_population_ci_lower', 'total_population_ci_upper',
    'population', 'male_population', 'female_population', 'n_total_staged_unstaged',
    'median_household_income', 'median_household_income_ci_lower', 'median_household_income_ci_upper',
    'mammography_facility_count_20mi',
}


def format_result(value, column=''):
    if value is None or isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        return value
    base = column.removeprefix('community_').split('__', 1)[0]
    if base in {'geoid', 'tract_geoid', 'rurality'} or base.endswith('_code'):
        return value
    ratio = 'travel_adjusted' in base or 'travel_time_adjusted' in base
    if isinstance(value, int) and not ratio:
        return value
    numeric = Decimal(str(value))
    if not numeric.is_finite():
        return None
    if base in WHOLE_NUMBER_COLUMNS or base.endswith('_count'):
        return int(numeric)
    digits = 2 if ratio or 0 < abs(numeric) < Decimal('0.1') else 1
    result = f'{numeric:.{digits}f}'
    # Preserve tiny nonzero rates rather than displaying them as true zeros.
    if numeric and Decimal(result) == 0:
        return format(Decimal(f'{numeric:.2g}'), 'f')
    return result.lstrip('-') if Decimal(result) == 0 else result


def format_result_row(row):
    return {key: format_result(value, key) for key, value in row.items()}
