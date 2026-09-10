"""Ohio race-alone/Hispanic reference populations for ZCTA and Place rates."""
import json
import re
from pathlib import Path
from .rate_statistics import AGE_BANDS_18, RateDataUnavailable, population_year_exposure

GROUPS = {'nh_white': ('I',), 'nh_black': ('J',), 'nh_aian': ('K',),
          'nh_api': ('L', 'M'), 'hisp_any': ('H',)}


def reference_path():
    from django.conf import settings
    return Path(settings.BASE_DIR) / 'popcase' / 'data' / 'ohio_decennial_reference.json'


def aggregate_group(metadata, values):
    """Read labeled Census age cells; never infer counts for missing cells."""
    result = {(sex, band): 0 for sex in ('male', 'female') for band in AGE_BANDS_18}
    coverage = {(sex, band): set() for sex, band in result}
    totals = {}
    for field, meta in metadata.items():
        if meta.get('predicateType') != 'int':
            continue
        parts = [part.strip().strip(':') for part in meta.get('label', '').split('!!') if part.strip()]
        if len(parts) not in (2, 3) or parts[1] not in ('Male', 'Female'):
            continue
        try:
            value = int(values[field])
        except (KeyError, TypeError, ValueError):
            raise ValueError('A Census population cell is missing or invalid.')
        if value < 0:
            raise ValueError('A Census population cell is suppressed or invalid.')
        sex = parts[1].lower()
        if len(parts) == 2:
            totals[sex] = value
            continue
        label = parts[2]
        ages = [int(n) for n in re.findall(r'\d+', label)]
        if not ages:
            raise ValueError('Unrecognized Census age label.')
        low, high = (0, ages[0]-1) if label.startswith('Under') else (ages[0], 120 if 'over' in label else ages[-1])
        band = next((b for b in AGE_BANDS_18 if low >= b[0] and (b[1] is None or high <= b[1])), None)
        if band is None:
            raise ValueError('Census ages do not match the application age categories.')
        cells = set(range(low, high+1))
        if coverage[(sex, band)] & cells:
            raise ValueError('Overlapping Census age cells.')
        coverage[(sex, band)].update(cells)
        result[(sex, band)] += value
    for (sex, band), covered in coverage.items():
        if covered != set(range(band[0], (band[1] if band[1] is not None else 120)+1)):
            raise ValueError('Census age coverage is incomplete.')
    for sex in ('male', 'female'):
        if sum(value for (s, _), value in result.items() if s == sex) != totals.get(sex):
            raise ValueError('Census age counts do not reconcile to the sex total.')
    return result


def validate_reference(data):
    if data.get('schema_version') != 1 or data.get('state') != '39':
        raise ValueError('Invalid Ohio reference metadata.')
    result = {}
    for row in data.get('cells', []):
        key = (row['year'], row['sex'], row['race'], tuple(row['age']))
        value = row['population']
        if key in result or not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError('Duplicate or invalid reference population cell.')
        result[key] = value
    expected = {(year, sex, race, band) for year in (2010, 2020)
                for sex in ('male', 'female') for race in GROUPS for band in AGE_BANDS_18}
    if set(result) != expected:
        raise ValueError('Reference population coverage is incomplete.')
    return result


def load_reference(year_exposure, bands, sex, races):
    try:
        cells = validate_reference(json.loads(reference_path().read_text(encoding='utf-8')))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RateDataUnavailable('Ohio race-specific reference populations require a validated decennial import.') from exc
    if any(race not in GROUPS for race in races):
        raise RateDataUnavailable('The selected race has no matching Ohio reference definition.')
    sexes = (sex,) if sex else ('male', 'female')
    try:
        return {band: sum(duration * cells[(year, s, race, band)]
                          for year, duration in population_year_exposure(year_exposure, decennial=True).items()
                          for s in sexes for race in races) for band in bands}
    except KeyError as exc:
        raise RateDataUnavailable('Ohio reference age or year coverage is incomplete.') from exc
