"""Cancer-attributed registry deaths; missing attribution is never a zero death count."""
from datetime import datetime
import re
from collections import defaultdict
from functools import lru_cache
import json

from django.db import connections
from django.conf import settings
from .rate_statistics import AGE_BANDS_20, RateDataUnavailable, age_band_for_age


def synthetic_death_codes(cancer_types, metadata):
    """Map selected definitions to site codes present in the synthetic fixture.

    Histology-only and site-based definitions use the same registry selector.
    This is not an ICD-O to ICD-10 mapping for production data.
    """
    from .models import NaaccrData
    from .services import apply_cancer_logic
    base = NaaccrData.objects.all()
    selected = base.none().values_list('primary_site', flat=True)
    for key in cancer_types:
        selected = selected.union(apply_cancer_logic(base, metadata[key]).values_list('primary_site', flat=True))
    return frozenset(code.strip().upper().replace('.', '') for code in selected if code)


@lru_cache(maxsize=128)
def selected_death_codes(cancer_types, synthetic):
    """Production definitions must explicitly provide ICD-10 mortality codes.

    The synthetic adapter reads the already prepared cause column;
    it never substitutes or writes a patient's primary site at calculation time.
    """
    from .services import load_cancer_logic
    _, metadata = load_cancer_logic()
    if not cancer_types:
        return None
    if any(key not in metadata for key in cancer_types):
        raise RateDataUnavailable('A selected cancer definition is unavailable.')
    if synthetic:
        return synthetic_death_codes(cancer_types, metadata)
    codes = set()
    for key in cancer_types:
        meta = metadata[key]
        # Separate code system: never treat ICD-O topography as real ICD-10.
        specification = meta.get('mortality_icd10')
        if not specification:
            raise RateDataUnavailable('The selected cancer definition lacks its ICD-10 mortality codes.')
        try:
            entries = json.loads(specification)
            if not isinstance(entries, list) or not entries:
                raise ValueError()
            for entry in entries:
                if not isinstance(entry, str) or not re.fullmatch(r'C[0-9]{2,3}', entry):
                    raise ValueError()
                codes.update([entry] if len(entry) == 4 else [entry + str(n) for n in range(10)])
        except (ValueError, TypeError) as exc:
            raise RateDataUnavailable('Invalid ICD-10 mortality definition.') from exc
    return frozenset(codes)


def cancer_death_matches(cause, revision, filters):
    """Match recorded cause against selected causes, independently of the tumor."""
    cause = (cause or '').strip().upper().replace('.', '')
    if not cause or cause in {'0000', '7777', '7797'}:
        return None
    synthetic = settings.MORTALITY_SYNTHETIC_PRIMARY_SITE_CAUSES
    if not re.fullmatch(r'[A-Z][0-9]{2,3}', cause):
        return None
    if not synthetic and (revision or '').strip() not in {'', '1'}:
        return None
    if not cause.startswith('C'):
        return False
    selection = filters.get('cancer_types') or []
    selection = [selection] if isinstance(selection, str) else selection
    codes = selected_death_codes(tuple(sorted(selection)), synthetic)
    if codes is None:
        return synthetic or 'C00' <= cause[:3] <= 'C97'
    if len(cause) == 3:
        # A category-only cause cannot resolve a narrower selected subcategory.
        matches = [cause + str(n) in codes for n in range(10)]
        return True if all(matches) else None if any(matches) else False
    return cause in codes


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
    selections = filters.get('cancer_types') or []
    selections = [selections] if isinstance(selections, str) else selections
    selected_death_codes(tuple(sorted(selections)), settings.MORTALITY_SYNTHETIC_PRIMARY_SITE_CAUSES)
    start_text, end_text = filters.get('dx_start') or '', filters.get('dx_end') or ''
    start = diagnosis_quarter_bounds(start_text + 'q1' if len(start_text) == 4 else start_text)
    end = diagnosis_quarter_bounds(end_text + 'q4' if len(end_text) == 4 else end_text)
    cases, identities = {}, {}
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
        match = cancer_death_matches(cause, revision, filters)
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
            # Attribution has already matched the selected cancer union.
            # Multiple matching tumor records still represent one death.
            identity = (death, age)
            if mid in identities and identities[mid] != identity:
                raise RateDataUnavailable('Mortality unavailable: conflicting death records for one person.')
            identities[mid] = identity
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
    from .rate_statistics import direct_adjusted_rate
    return direct_adjusted_rate(age_counts, populations)
