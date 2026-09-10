"""Cancer-attributed registry deaths; missing attribution is never a zero death count."""
from datetime import datetime
import re
from collections import defaultdict

from django.db import connections
from .rate_statistics import AGE_BANDS_20, RateDataUnavailable, age_band_for_age


def cancer_death_matches(cause, revision, primary_site, histology):
    """Strict ICD-10 organ match; unresolved coding requires review, not inference."""
    cause = (cause or '').strip().upper().replace('.', '')
    site = (primary_site or '').strip().upper().replace('.', '')
    if not cause or cause in {'0000', '7777', '7797'}:
        return None
    # Alphabetic ICD-10 codes are unambiguous even when the revision field is blank.
    if (revision or '').strip() not in {'', '1'} or not re.fullmatch(r'[A-Z][0-9]{2,3}', cause):
        return None
    if not cause.startswith('C'):
        return False
    if cause[:3] >= 'C76':  # Secondary, unknown-primary and haematologic codes need a crosswalk.
        return None
    try:
        histology = int(histology)
    except (TypeError, ValueError):
        return None
    if not re.fullmatch(r'C[0-9]{3}', site) or histology >= 9590 or site[:3] == 'C42':
        return None
    # ICD-O uses C44 for skin; ICD-10 distinguishes melanoma (C43).
    expected = 'C43' if site[:3] == 'C44' and 8720 <= histology <= 8790 else site[:3]
    # Mesothelioma and Kaposi sarcoma are morphology-defined in ICD-10.
    expected = 'C45' if 9050 <= histology <= 9055 else 'C46' if histology == 9140 else expected
    return cause[:3] == expected


def death_date(last_contact, year=None, month=None, day=None):
    """Use the combined date when present, otherwise the NAACCR date parts."""
    value = str(last_contact or '').strip()
    if not value:
        parts = [str(part or '').strip() for part in (year, month, day)]
        if not all(re.fullmatch(pattern, part) for pattern, part in zip(
                (r'[0-9]{4}', r'[0-9]{1,2}', r'[0-9]{1,2}'), parts)):
            raise ValueError('Incomplete death date')
        value = parts[0] + parts[1].zfill(2) + parts[2].zfill(2)
    if not re.fullmatch(r'[0-9]{8}', value):
        raise ValueError('Invalid death date')
    return datetime.strptime(value, '%Y%m%d').date()


def death_ages(rows, filters, level, bands):
    from .services import diagnosis_quarter_bounds
    start_text, end_text = filters.get('dx_start') or '', filters.get('dx_end') or ''
    start = diagnosis_quarter_bounds(start_text + 'q1' if len(start_text) == 4 else start_text)
    end = diagnosis_quarter_bounds(end_text + 'q4' if len(end_text) == 4 else end_text)
    cases = {}
    for mid, status, last_contact, birth, cause, revision, site, histology, *date_parts in rows:
        status = (status or '').strip()
        if status == '1':
            continue
        if status != '0':
            raise RateDataUnavailable('Mortality unavailable: vital status is missing or unknown.')
        try:
            death = death_date(last_contact, *date_parts)
        except ValueError:
            raise RateDataUnavailable('Mortality unavailable: a death date is missing or invalid.')
        death_text = death.strftime('%Y%m%d')
        if (start and death_text < start[0]) or (end and death_text > end[1]):
            continue
        match = cancer_death_matches(cause, revision, site, histology)
        if match is None:
            raise RateDataUnavailable('Mortality unavailable: cause of death or its cancer attribution is missing or unresolved.')
        if not match:
            continue
        try:
            if not re.fullmatch(r'[0-9]{8}', (birth or '').strip()):
                raise ValueError()
            dob = datetime.strptime((birth or '').strip(), '%Y%m%d').date()
            if dob > death:
                raise ValueError()
            age = death.year - dob.year - ((death.month, death.day) < (dob.month, dob.day))
        except ValueError:
            age = None
        if age is None and any(filters.get(k) for k in ('age_groups', 'age_from', 'age_to')):
            raise RateDataUnavailable('Mortality unavailable: age at death cannot be matched to the selected ages.')
        if age is None or age_band_for_age(age, level) in bands:
            cases[mid] = age
    return cases


def load_county_population(years, bands, sex, races):
    from .incidence_rates import RACES
    conditions, params = [], [tuple(years), tuple(AGE_BANDS_20.index(b) for b in bands),
                             (1, 2) if sex is None else (1,) if sex == 'male' else (2,)]
    for race in races:
        conditions.append('origin = 1' if race == 'hisp_any' else '(origin = 0 AND race = %s)')
        if race != 'hisp_any':
            params.append(int(RACES[race][0]))
    where = ' AND (' + ' OR '.join(conditions) + ')' if conditions else ''
    with connections['default'].cursor() as c:
        c.execute('SELECT county_fips, year FROM population_county WHERE state_fips=%s GROUP BY 1,2', ['39'])
        coverage = {(str(g).zfill(3), int(y)) for g, y in c.fetchall()}
        c.execute('''SELECT county_fips,year,age,SUM(population),
            COUNT(*) FILTER (WHERE population IS NULL OR population < 0)
            FROM population_county WHERE state_fips='39' AND year IN %s AND age IN %s AND sex IN %s
        ''' + where + ' GROUP BY 1,2,3', params)
        cells = {(str(g).zfill(3), int(y), int(a)): (p, invalid) for g,y,a,p,invalid in c.fetchall()}
    valid, errors = {}, {}
    for county in {g for g,y in coverage}:
        geoid = '39' + county
        exposure = defaultdict(float)
        for year, duration in years.items():
            for band in bands:
                p, invalid = cells.get((county, year, AGE_BANDS_20.index(band)), (0, 0))
                if (county, year) not in coverage or p is None or invalid:
                    errors[geoid] = 'County population data are incomplete.'
                else:
                    exposure[band] += duration * float(p)
        if geoid not in errors:
            valid[geoid] = dict(exposure)
    return valid, errors


def direct_mortality(age_counts, populations):
    # https://seer.cancer.gov/stdpopulations/stdpop.20ages.html
    standard = dict(zip(AGE_BANDS_20, (3794901,15191619,19919840,20056779,19819518,
        18257225,17722067,19511370,22179956,22479229,19805793,17224359,13307234,
        10654272,9409940,8725574,7414559,4900234,2678567,1580606)))
    if any(p <= 0 for p in populations.values()):
        raise RateDataUnavailable('An age-specific population denominator is zero.')
    rate = sum(standard[b] * age_counts.get(b, 0) / p for b,p in populations.items())
    return rate * 100000 / sum(standard[b] for b in populations), None, None
