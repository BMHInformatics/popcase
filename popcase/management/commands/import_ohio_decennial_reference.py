"""Refresh the application's Ohio reference from Census SF1/DHC."""
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import urlopen
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from popcase.decennial_reference import GROUPS, aggregate_group, validate_reference, reference_path


class Command(BaseCommand):
    help = 'Import validated 2010/2020 Ohio age-by-sex-by-race populations. Requires CENSUS_API_KEY.'

    def handle(self, *args, **options):
        key = os.environ.get('CENSUS_API_KEY') or getattr(settings, 'CENSUS_API_KEY', '')
        if not key:
            raise CommandError('Set CENSUS_API_KEY in the process environment before running this import.')
        cells, sources = [], []
        try:
            for year, dataset, prefix in ((2010, 'dec/sf1', 'PCT12'), (2020, 'dec/dhc', 'P12')):
                groups = {}
                for suffix in sorted({s for suffixes in GROUPS.values() for s in suffixes}):
                    group = prefix + suffix
                    base = f'https://api.census.gov/data/{year}/{dataset}'
                    with urlopen(f'{base}/groups/{group}.json', timeout=45) as response:
                        metadata = json.load(response)['variables']
                    query = urlencode({'get': f'group({group})', 'for': 'state:39', 'key': key})
                    with urlopen(base + '?' + query, timeout=45) as response:
                        values = json.load(response)
                    if len(values) != 2:
                        raise ValueError('Expected exactly one Ohio population record.')
                    record = dict(zip(values[0], values[1]))
                    if record.get('state') != '39':
                        raise ValueError('Population response is not Ohio.')
                    groups[suffix] = aggregate_group(metadata, record)
                    sources.append(f'{base}/groups/{group}.json')
                for race, suffixes in GROUPS.items():
                    for sex, band in groups[suffixes[0]]:
                        cells.append({'year': year, 'sex': sex, 'race': race, 'age': list(band),
                                      'population': sum(groups[s][(sex, band)] for s in suffixes)})
            data = {'schema_version': 1, 'state': '39', 'sources': sources,
                    'retrieved_at': datetime.now(timezone.utc).isoformat(), 'cells': cells}
            validate_reference(data)
        except Exception:
            # URLs can contain API credentials: do not echo underlying exceptions.
            raise CommandError('Census import failed or did not pass population validation. Existing reference data were preserved.') from None
        path = reference_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
        temporary.replace(path)
        self.stdout.write(self.style.SUCCESS(f'Imported {len(cells)} verified Ohio population cells.'))
