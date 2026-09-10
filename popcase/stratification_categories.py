"""Category domains and stable display ordering for stratified results."""
import re

RACES = ['Non-Hispanic White', 'Non-Hispanic Black', 'Non-Hispanic AI/AN', 'Non-Hispanic API', 'Hispanic (any race)']
ORDER = {
    'sex': ['Female', 'Male', 'Unknown/other sex'],
    'race_eth': RACES + ['Other Non-Hispanic', 'Unknown race/ethnicity'],
    'stage': ['In situ', 'Localized', 'Regional', 'Distant', 'Unknown', 'Not applicable/unstaged'],
    'receptor3': ['ER+/HER2-', 'HER2+', 'Triple negative', 'Unknown receptor status'],
    'receptor4': ['ER+/HER2-', 'ER+/HER2+', 'ER-/HER2+', 'Triple negative', 'Unknown receptor status'],
    'insurance': ['Private', 'Medicare', 'Medicaid', 'Medicare/Medicaid dual eligible', 'Insurance NOS',
                  'TRICARE/Military', 'VA', 'Indian/Public Health Service', 'Uninsured/Self-pay', 'Insurance status unknown'],
}


def category_order(variable, value):
    label = str(value or '')
    normalized = label.removesuffix(' alone')
    ordered = ORDER.get(variable, [])
    if normalized in ordered:
        return (0, ordered.index(normalized), '')
    if variable.startswith('age_') and re.match(r'^\d+', label):
        return (0, int(re.match(r'^\d+', label)[0]), label)
    return (1, 0, label.casefold())


def category_values(variable, observed, filters, level):
    from . import services
    from .rate_statistics import selected_age_bands
    from .stratification import group_label, cancer_site_conditions
    values = set(observed)
    if variable == 'sex':
        selected = services._normalize_requested_sex(filters)
        values.update([selected.title()] if selected else ['Female', 'Male'])
    elif variable == 'race_eth':
        tokens = services._as_list(filters.get('race'))
        mapping = dict(zip(['nh_white', 'nh_black', 'nh_aian', 'nh_api', 'hisp_any'], RACES))
        labels = [mapping[t] for t in tokens if t in mapping] if tokens and 'all' not in tokens else RACES
        values.update(label + (' alone' if level in ('zcta', 'place') and label != 'Hispanic (any race)' else '') for label in labels)
    elif variable.startswith('age_'):
        for low, high in selected_age_bands('county' if level in ('none', 'total') else level, filters):
            values.add(group_label({'age_at_dx': str(low)}, variable, level))
    elif variable == 'site':
        values.update(cancer_site_conditions(filters))
    elif variable == 'stage':
        mapping = {'localized': 'Localized', 'regional': 'Regional', 'metastatic': 'Distant', 'unknown': 'Unknown', 'in_situ': 'In situ'}
        selected = services._as_list(filters.get('stage'))
        values.update([mapping[s] for s in selected if s in mapping] if selected else ORDER['stage'])
    elif variable in ORDER:
        values.update(ORDER[variable])
    return sorted(values, key=lambda value: category_order(variable, value))
