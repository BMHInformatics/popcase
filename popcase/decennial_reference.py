"""Ohio race-alone/Hispanic reference populations for ZCTA and Place rates."""
import json
import re
from functools import lru_cache
from pathlib import Path
from .rate_statistics import AGE_BANDS_18, RateDataUnavailable, population_year_exposure

GROUPS = {'nh_white': ('I',), 'nh_black': ('J',), 'nh_aian': ('K',),
          'nh_api': ('L', 'M'), 'hisp_any': ('H',)}


def residual_population(total, classified):
    """Other NH = total minus Hispanic and the four specified NH race groups.

    Inputs use identical ages, sex, geography and person-year exposure. This
    residual includes other-race-alone and multiracial non-Hispanic residents.
    Missing components never become zero population.
    """
    result, errors = {}, {}
    for geoid, ages in total.items():
        if geoid not in classified or set(ages) != set(classified[geoid]):
            errors[geoid] = 'Race population components are incomplete.'
            continue
        remaining = {b:p - classified[geoid][b] for b,p in ages.items()}
        if any(p < 0 for p in remaining.values()):
            errors[geoid] = 'Race population components exceed the total population.'
        else:
            result[geoid] = remaining
    return result, errors


def reference_path():
    from django.conf import settings
    return Path(settings.BASE_DIR) / 'popcase' / 'data' / 'ohio_decennial_reference.json'


def place_reference_path():
    return reference_path().with_name('ohio_place_decennial_reference.json')


def zcta_reference_path():
    return reference_path().with_name('ohio_zcta_decennial_reference.json')


def validate_place_reference(data):
    """Every imported place/year must contain all race/sex/age cells."""
    level = data.get('geographic_level', 'place')
    if data.get('schema_version') != 1 or data.get('state') != '39' or level not in ('place', 'zcta'):
        raise ValueError('Invalid Place population metadata.')
    cells, coverage = {}, set()
    for row in data.get('cells', []):
        geoid, year = row['geoid'], row['year']
        if not re.fullmatch(r'[0-9]{5}' if level == 'zcta' else r'39[0-9]{5}', geoid) or year not in (2010, 2020):
            raise ValueError('Invalid Place population geography or year.')
        key = (geoid, year, row['sex'], row['race'], tuple(row['age']))
        value = row['population']
        if key in cells or not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError('Duplicate or invalid Place population cell.')
        cells[key] = value
        coverage.add((geoid, year))
    expected = {(g,y,s,r,b) for g,y in coverage for s in ('male','female') for r in GROUPS for b in AGE_BANDS_18}
    if not coverage or set(cells) != expected or {y for g,y in coverage} != {2010,2020}:
        raise ValueError('Place race/sex/age coverage is incomplete.')
    return cells


@lru_cache(maxsize=2)
def _place_cells(path, modified, size):
    return validate_place_reference(json.loads(Path(path).read_text(encoding='utf-8')))


def load_place_reference(year_exposure, bands, sex, races, level='place'):
    from .incidence_rates import _population_exposures
    path = zcta_reference_path() if level == 'zcta' else place_reference_path()
    try:
        info = path.stat()
        cells = _place_cells(str(path), info.st_mtime_ns, info.st_size)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RateDataUnavailable('Place race-specific populations require a validated decennial import.') from exc
    if any(race not in GROUPS for race in races):
        raise RateDataUnavailable('The selected race has no matching imported Place definition.')
    sexes = (sex,) if sex else ('male','female')
    weights = population_year_exposure(year_exposure, decennial=True)
    rows = [(g,y,b,s,r,p) for (g,y,s,r,b),p in cells.items()
            if y in weights and b in bands and s in sexes and r in races]
    return _population_exposures(rows, weights, bands, sexes, races)


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
