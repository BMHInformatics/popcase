"""SDD explanations and explicit bindings to existing wizard controls."""
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def help_assets():
    """Bundle help with the page even when collected static files are stale."""
    root = Path(__file__).with_name('static') / 'popcase'
    return {
        'css': (root / 'css' / 'sdd-help.css').read_text(encoding='utf-8'),
        'js': (root / 'js' / 'sdd-help.js').read_text(encoding='utf-8'),
    }


@lru_cache(maxsize=1)
def catalog():
    return json.loads(Path(__file__).with_name('sdd_help.json').read_text(encoding='utf-8'))


def help_config(geography, step):
    if step not in {'filters', 'measures', 'stratification'}:
        return {}
    entries = {entry['id']: entry.get('display_text', entry['text']) for entry in catalog()['entries']}
    def text(number):
        return entries[f'sdd-{number}']
    texts = {key: text(number) for key, number in {
        'bridged_race': 345, 'treatment': 408, 'advanced': 404, 'metastatic': 406,
        'direct_rate': 421, 'indirect_20': 423, 'indirect_18': 425,
        'cdc': 494, 'acs': 618,
        'county_rurality': 689, 'tract_rurality': 690,
        'overcrowding': 734, 'all_registry': 872, 'gini': 721, 'internet': 745,
        'patient_pcp': 913, 'patient_onc': 920, 'patient_ext': 929, 'patient_mammo': 948,
        'tract_pcp': 519, 'tract_onc': 526, 'tract_ext': 535, 'tract_mammo': 554,
        'crude_ci': 410, 'direct_ci': 427, 'indirect_ci': 428,
        'cancer_selection': 393, 'stage_selection': 397,
        'county_pcp': 574, 'county_onc': 582, 'county_ext': 592, 'county_mammo': 611,
    }.items()}
    texts['patient_rurality'] = texts['county_rurality'] + '\n\n' + texts['tract_rurality']
    rate = 'direct_rate' if geography in {'county', 'none', 'total'} else 'indirect_20' if geography == 'tract' else 'indirect_18'
    bindings = []
    def bind(field, value, topic):
        bindings.append({'field': field, 'value': value, 'topic': topic})
    for value, topic in {'median_tti': 'treatment', 'inc_rate': rate, 'mort_rate': rate}.items():
        bind('disease_measures', value, topic)
    for value in ('crude_inc_ci', 'crude_mort_ci'):
        bind('disease_measures', value, 'crude_ci')
    for value in ('inc_ci', 'mort_ci'):
        bind('disease_measures', value, 'direct_ci' if geography in {'county', 'none', 'total'} else 'indirect_ci')
    for token, topic in [('pct_advanced', 'advanced'), ('pct_metastatic', 'metastatic')]:
        bind('disease_measures', token, topic)
        bind('disease_measures', token + '_ci', topic)
    bind('patient_all_registry', None, 'all_registry')
    bind('patient_variables', 'patient_rurality', 'patient_rurality')
    for field, prefix in [('access_patient_measures', 'patient'), ('access_comm_tract', 'tract'),
                          ('access_comm_county', 'county')]:
        for value, suffix in [('pcp', 'pcp'), ('onc', 'onc'), ('ext_care', 'ext'), ('mammo_fac', 'mammo')]:
            bind(field, value, prefix + '_' + suffix)
    survey = ['routine_checkup', 'no_transport', 'no_insurance', 'dentist']
    for field in ('access_comm_county', 'access_comm_tract', 'access_comm_zcta_place', 'patient_community_access'):
        for value in survey:
            bind(field, value, 'cdc')
    for field in ('cancer_prevention', 'noncancer_health_status'):
        bind(field, None, 'cdc')
    for value, topic in {'utility_shutoff_threat': 'cdc', 'housing_insecurity': 'cdc',
                         'occupants_per_room': 'overcrowding', 'gini': 'gini', 'internet_access': 'internet',
                         'rurality': 'community_rurality'}.items():
        bind('community_characteristics', value, topic)
    return {'geography': geography, 'texts': texts, 'bindings': bindings}
