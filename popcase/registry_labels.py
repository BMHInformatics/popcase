"""Readable preview labels; registry CSV values remain unchanged.

Sources: NAACCR Data Dictionary v25, items 160, 190, 220, 830,
3827, 3915, 3855 and 3909; v24 items 820 and 1182.
https://apps.naaccr.org/data-dictionary/data-dictionary/version=25/
"""
import re


SEX = {'1': 'Male', '2': 'Female', '3': 'Other (intersex/DSD)',
       '4': 'Transsexual, unspecified', '5': 'Transsexual, natal male',
       '6': 'Transsexual, natal female', '9': 'Unknown'}
RACE = {
    '1': 'White', '2': 'Black or African American', '3': 'American Indian or Alaska Native',
    '4': 'Chinese', '5': 'Japanese', '6': 'Filipino', '7': 'Native Hawaiian', '8': 'Korean',
    '10': 'Vietnamese', '11': 'Laotian', '12': 'Hmong', '13': 'Cambodian', '14': 'Thai',
    '15': 'Asian Indian or Pakistani, unspecified', '16': 'Asian Indian', '17': 'Pakistani',
    '20': 'Micronesian, unspecified', '21': 'Chamorro', '22': 'Guamanian, unspecified',
    '25': 'Polynesian, unspecified', '26': 'Tahitian', '27': 'Samoan', '28': 'Tongan',
    '30': 'Melanesian, unspecified', '31': 'Fiji Islander', '32': 'Papua New Guinean',
    '96': 'Other Asian', '97': 'Pacific Islander, unspecified', '98': 'Other race', '99': 'Unknown',
}
ORIGIN = {'0': 'Non-Hispanic', '1': 'Mexican', '2': 'Puerto Rican', '3': 'Cuban',
          '4': 'South or Central American (except Brazil)', '5': 'Other specified Spanish/Hispanic origin',
          '6': 'Spanish/Hispanic/Latino, unspecified', '7': 'Spanish surname only',
          '8': 'Dominican Republic', '9': 'Unknown'}
RECEPTOR = {'0': 'Negative', '1': 'Positive', '7': 'Test performed; result unavailable',
            '9': 'Unknown or indeterminate'}
CODE_LABELS = {
    'Sex': SEX, 'sex_code': SEX, 'Race 1': RACE, 'race_code': RACE,
    'Spanish/Hispanic Origin': ORIGIN, 'hispanic_origin_code': ORIGIN,
    'Lymphovascular Invasion': {'0': 'Not present', '1': 'Present',
        '2': 'Lymphatic/small-vessel invasion only', '3': 'Venous invasion / angioinvasion only',
        '4': 'Both lymphatic and venous invasion', '8': 'Not applicable', '9': 'Unknown or indeterminate'},
    'Perineural Invasion': {'0': 'Not present', '1': 'Present',
        '8': 'Not applicable / not collected', '9': 'Unknown or not assessed'},
    'Estrogen Receptor Summary': RECEPTOR,
    'Progesterone Receptor Summary': RECEPTOR,
    'HER2 Overall Summary': {'0': 'Negative or equivocal', '1': 'Positive',
        '7': 'Test ordered; result unavailable', '8': 'Not applicable / not collected',
        '9': 'Unknown or indeterminate'},
}


def display_value(column, value):
    if value is None or str(value).strip() == '':
        return value
    if column not in CODE_LABELS and column not in {'Regional Nodes Examined', 'Regional Nodes Positive'}:
        return value
    raw = str(value).strip()
    code = str(int(raw.split('.')[0])) if re.fullmatch(r'\d+(?:\.0*)?', raw) else raw
    if column in {'Regional Nodes Examined', 'Regional Nodes Positive'}:
        if code.isdigit() and 0 <= int(code) <= 89:
            return int(code)
        labels = {'90': '90 or more', '95': 'Aspiration only; no nodes removed',
                  '96': 'Sampling; count unknown', '97': 'Dissection; count unknown',
                  '98': 'Nodes examined; count unknown', '99': 'Unknown whether examined'}
        if column == 'Regional Nodes Positive':
            labels = {'90': '90 or more', '95': 'Positive aspiration',
                      '97': 'Positive nodes; count unknown', '98': 'No nodes examined',
                      '99': 'Unknown / not applicable'}
    else:
        labels = CODE_LABELS[column]
    return labels.get(code, f'Unrecognized code ({raw})')


def display_row(row):
    return {column: display_value(column, value) for column, value in row.items()}
