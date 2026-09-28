import csv
import io
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.db import DatabaseError
from django.test import SimpleTestCase, RequestFactory

from popcase import provider_access as access, services, views


class TractAccessTests(SimpleTestCase):
    def test_provider_sources_scaled_independently(self):
        with patch.object(access.TravelTimeTract, 'objects') as manager:
            query = manager.using.return_value
            query.filter.return_value.values.return_value.iterator.side_effect = [
                iter([{'tract_geoid': '39001770100', 'count_x': .002}]),
                iter([{'tract_geoid': '39001770100', 'count_x': .0005}]),
            ]
            result = access.get_tract_access_lookup(['onc', 'ext_care'])
            self.assertEqual(result['39001770100'], {
                access.TRACT_ACCESS_OUTPUTS['ext_care']: 200,
                access.TRACT_ACCESS_OUTPUTS['onc']: 50,
            })
            self.assertEqual([call.kwargs['source_file'] for call in query.filter.call_args_list],
                             ['cnc2_1220_tr.xlsx.xlsx', 'cnc1_1220_tr.xlsx.xlsx'])
            manager.using.assert_called_with('popcase_manual_etl')

    def test_mammography_uses_stored_rate_and_preserves_zero(self):
        with patch.object(access, 'connections') as connections:
            cursor = connections.__getitem__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchall.return_value = [(39001770100, 0), (39001770200, 11.538011),
                (39001770300, None), (39001770400, -1), (39001770500, float('nan')),
                (39001770600, float('inf')), (39001770700, 5), (39001770700, 6)]
            with self.assertLogs('popcase.provider_access', level='WARNING'):
                result = access.get_mammography_tract_lookup()
            self.assertEqual(result, {'39001770100': 0, '39001770200': 11.538011})
            connections.__getitem__.assert_called_with('default')
            self.assertIn('mammo_per_100k', cursor.execute.call_args.args[0])
            cursor.execute.side_effect = DatabaseError('missing')
            with self.assertLogs('popcase.provider_access', level='ERROR'):
                self.assertEqual(access.get_mammography_tract_lookup(), {})

    def test_selected_sources_only_and_no_proxy_fallback(self):
        services._get_geo_support_lookups_cached.cache_clear()
        self.addCleanup(services._get_geo_support_lookups_cached.cache_clear)
        with patch.object(services, '_get_most_recent_community_lookup', return_value={}), \
             patch.object(services, '_get_cdc_places_lookup', return_value={}), \
             patch.object(services, 'get_tract_access_lookup', return_value={}) as reader, \
             patch.object(services, '_get_tract_mammography_access_lookup') as proxy:
            self.assertEqual(services._get_geo_support_lookups('tract', ['mammo_fac']), {'tract_access': {}})
            reader.assert_called_once_with({'mammo_access'})
            proxy.assert_not_called()
        with patch.object(access, '_get_tract_provider_source') as provider:
            self.assertEqual(access.get_tract_access_lookup(['unrelated']), {})
            provider.assert_not_called()

    def test_dataset_and_export_use_new_values_and_labels(self):
        geoid = '39001770100'
        values = {access.TRACT_ACCESS_OUTPUTS['onc']: 10,
                  access.TRACT_ACCESS_OUTPUTS['ext_care']: 25,
                  access.TRACT_ACCESS_OUTPUTS['mammo_access']: 0}
        filtered = MagicMock()
        filtered.values_list.return_value = []
        with patch.object(services, 'apply_naaccr_filters', return_value=filtered), \
             patch.object(services, '_get_geo_support_lookups', return_value={'tract_access': {geoid: values}}), \
             patch.object(services, '_get_period_community_lookups_cached', return_value=({}, set())), \
             patch.object(services, '_geo_label', return_value='Example tract'):
            rows = services._build_geo_dataset_uncached('tract', ('2020', '2020'), {}, [], ['onc', 'ext_care', 'mammo_fac'])
        self.assertEqual(len(rows), 1)
        self.assertEqual({k: rows[0][k] for k in values}, values)
        self.assertNotIn('nearest_mammography_distance_miles', rows[0])
        self.assertNotIn('mammography_access_score', rows[0])
        request = RequestFactory().get('/export/')
        request.user = SimpleNamespace(is_authenticated=True)
        request.session = {'popcase_wizard': {'geographic_level': 'tract',
            'filters': {'dx_start': '2020', 'dx_end': '2020'},
            'measures': {'access_comm_tract': ['onc', 'ext_care', 'mammo_fac']}}}
        with patch.object(views, '_latest_linking_year', return_value='2020'), \
             patch.object(views, 'get_default_diagnosis_quarter_range', return_value=('2020', '2020')), \
             patch.object(views, '_build_results_payload_cached', return_value={'dataset_rows': rows}):
            response = views.export_geo_dataset_csv(request)
        csv_rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        for key, value in values.items():
            header = views.DATASET_HEADER_MAP[key]
            self.assertIn('travel time-adjusted', header)
            self.assertEqual(float(csv_rows[1][csv_rows[0].index(header)]), value)
        self.assertFalse(any('within 20 miles' in header or 'in county' in header for header in csv_rows[0]))
