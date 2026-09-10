"""Registry-attributed deaths, classified independently of diagnosis-period cases."""
from .mortality_rates import death_ages, death_date
from .rate_statistics import selected_age_bands, RateDataUnavailable

MEASURE_MAP = {'crude_mort_rate': 'crude_inc_rate', 'crude_mort_ci': 'crude_inc_ci',
               'mort_rate': 'inc_rate', 'mort_ci': 'inc_ci'}
OUTPUT_MAP = {'case_count': 'cancer_death_count',
              'crude_incidence_per_100k': 'crude_mortality_per_100k',
              'crude_inc_ci_lower_per_100k': 'crude_mort_ci_lower_per_100k',
              'crude_inc_ci_upper_per_100k': 'crude_mort_ci_upper_per_100k',
              'age_adjusted_per_100k': 'age_adjusted_mortality_per_100k',
              'inc_ci_lower_per_100k': 'mort_ci_lower_per_100k',
              'inc_ci_upper_per_100k': 'mort_ci_upper_per_100k',
              'stratification_rate_note': 'mortality_data_note'}


def prepare_deaths(records, filters, level, variables):
    from .stratification import group_label
    bands = selected_age_bands('county' if level in ('total', 'none') else level, filters)
    deaths, errors, identities, statuses = {}, set(), {}, {}
    for record in records:
        status = str(record.get('vital_status') or '').strip()
        prior_status = statuses.setdefault(record['mid'], status)
        if prior_status != status:
            errors.add('Mortality unavailable: conflicting vital status records for one person.')
        try:
            ages = death_ages([tuple(record.get(field) for field in (
                'mid', 'vital_status', 'last_contact', 'birth_date', 'cause_of_death',
                'icd_revision', 'primary_site', 'hist_o3', 'last_contact_year',
                'last_contact_month', 'last_contact_day'))], filters, level, bands)
        except RateDataUnavailable as exc:
            errors.add(str(exc))
            continue
        if not ages:
            continue
        if ages[record['mid']] is None and any(v.startswith('age_') for v in variables):
            errors.add('Mortality unavailable: age at death is unknown for a death that could belong to the requested age strata.')
        death = dict(record, age_at_dx=ages[record['mid']])
        identity = tuple(group_label(death, variable, level) for variable in variables)
        previous = identities.get(record['mid'])
        # One person has one death; competing tumor assignments need attribution.
        if previous is not None and previous != identity:
            errors.add('Mortality unavailable: one death matches multiple tumor strata; tumor-specific death attribution is required.')
        identities[record['mid']] = identity
        signature = (record.get('sex'), record.get('race1'), record.get('hispanic_origin'),
                     death_date(*(record.get(field) for field in (
                         'last_contact', 'last_contact_year', 'last_contact_month', 'last_contact_day'))),
                     ages[record['mid']])
        if record['mid'] in deaths and deaths[record['mid']][0] != signature:
            errors.add('Mortality unavailable: conflicting death or demographic records for one person.')
        deaths[record['mid']] = (signature, death)
    return [item[1] for item in deaths.values()], '; '.join(sorted(errors))


def mortality_rows(level, period, filters, measures, state, linking_year):
    from .stratification import build_stratified_dataset
    rows = build_stratified_dataset(level, period, filters,
        {MEASURE_MAP[m] for m in measures} | {'case_count'}, state, linking_year, _mortality=True)
    return [{OUTPUT_MAP.get(key, key): value for key, value in row.items()} for row in rows]
