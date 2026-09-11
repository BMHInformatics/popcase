"""Rebuild race-specific Place populations and Ohio references from Census."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import urlopen
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from popcase.decennial_reference import (
    GROUPS, aggregate_group, validate_reference, reference_path,
    validate_place_reference, place_reference_path, zcta_reference_path,
)


class Command(BaseCommand):
    help = 'Import validated 2010/2020 Place and Ohio race/sex/age populations. Requires CENSUS_API_KEY.'

    def add_arguments(self, parser):
        parser.add_argument('--geography', choices=('place','zcta'), default=getattr(self,'default_geography','place'))

    def handle(self, *args, **options):
        target = options['geography']
        key = os.environ.get('CENSUS_API_KEY') or getattr(settings, 'CENSUS_API_KEY', '')
        if not key:
            raise CommandError('Set CENSUS_API_KEY in the application environment before importing Census populations.')
        state_cells, place_cells, sources = [], [], []
        try:
            zctas = []
            if target == 'zcta':
                with connections['popcase_manual_etl'].cursor() as cursor:
                    cursor.execute('SELECT DISTINCT RIGHT("GEOID",5) FROM age_adjustment_zcta WHERE state_fips=%s', ['39'])
                    zctas = sorted({r[0] for r in cursor.fetchall()})
                if not zctas:
                    raise ValueError('No Ohio ZCTA geography coverage is available.')
            for year, dataset, prefix in ((2010,'dec/sf1','PCT12'), (2020,'dec/dhc','P12')):
                groups = {'state': {}, target: {}}
                for suffix in sorted({s for parts in GROUPS.values() for s in parts}):
                    group = prefix + suffix
                    base = f'https://api.census.gov/data/{year}/{dataset}'
                    with urlopen(f'{base}/groups/{group}.json', timeout=45) as response:
                        metadata = json.load(response)['variables']
                    for level in groups:
                        query = {'get':f'group({group})','for':'state:39' if level == 'state' else 'place:*','key':key}
                        if level == 'place':
                            query['in'] = 'state:39'
                        queries = [query]
                        if level == 'zcta':
                            queries = [dict(query, **{'for':'zip code tabulation area:' + ','.join(zctas[i:i+300])})
                                       for i in range(0,len(zctas),300)]
                        def fetch(query):
                            with urlopen(base + '?' + urlencode(query), timeout=60) as response:
                                return json.load(response)
                        records = {}
                        with ThreadPoolExecutor(max_workers=3) as pool:
                            for values in pool.map(fetch,queries):
                                for values_row in values[1:]:
                                    row = dict(zip(values[0], values_row))
                                    if level != 'zcta' and row.get('state') != '39':
                                        raise ValueError('Unexpected state in population response.')
                                    geoid = row['zip code tabulation area'] if level == 'zcta' else '39' + (row['place'] if level == 'place' else '')
                                    if geoid in records or (level == 'zcta' and geoid not in zctas):
                                        raise ValueError('Duplicate or unexpected population geography.')
                                    records[geoid] = aggregate_group(metadata, row)
                        if not records or (level == 'state' and set(records) != {'39'}):
                            raise ValueError('Missing population geographies.')
                        groups[level][suffix] = records
                    sources.append(f'{base}/groups/{group}.json')
                    self.stdout.write(f'Validated {year} {group} for Ohio and {target}.')
                for level, suffix_groups in groups.items():
                    geoids = set(next(iter(suffix_groups.values())))
                    if any(set(records) != geoids for records in suffix_groups.values()):
                        raise ValueError('Race groups do not cover the same places.')
                    for geoid in sorted(geoids):
                        for race, suffixes in GROUPS.items():
                            for sex, band in suffix_groups[suffixes[0]][geoid]:
                                cell = {'year':year,'sex':sex,'race':race,'age':list(band),
                                    'population':sum(suffix_groups[s][geoid][sex,band] for s in suffixes)}
                                if level != 'state':
                                    place_cells.append(dict(cell, geoid=geoid))
                                else:
                                    state_cells.append(cell)
            metadata = {'schema_version':1,'state':'39','sources':sources,
                        'retrieved_at':datetime.now(timezone.utc).isoformat()}
            state_data, place_data = dict(metadata,cells=state_cells), dict(metadata,cells=place_cells,geographic_level=target)
            validate_reference(state_data)
            validate_place_reference(place_data)
        except Exception:
            # Never echo network exceptions: request URLs may contain the key.
            raise CommandError('Census retrieval or validation failed. Existing population files were preserved.') from None
        target_path = zcta_reference_path() if target == 'zcta' else place_reference_path()
        for path, data in ((reference_path(),state_data),(target_path,place_data)):
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(data) + '\n', encoding='utf-8')
            temporary.replace(path)
        self.stdout.write(self.style.SUCCESS(f'Imported {len(state_cells)} Ohio and {len(place_cells)} {target} population cells.'))
