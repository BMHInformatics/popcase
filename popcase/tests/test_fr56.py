"""FR56 selection, SQL aggregation, dataset, and export regression coverage."""
import csv
import io
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase

from popcase import services, views
from popcase.cancer_center_access import get_cancer_center_access_lookup, OUTPUTS
from popcase.forms import MeasuresForm


class CancerCenterAccessTests(SimpleTestCase):
    def test_sql_selects_nearest_within_each_category(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute("ATTACH DATABASE ':memory:' AS public")
        db.execute('CREATE TABLE public.coc_travelmatrix_center_lookup (id TEXT, "Type" TEXT)')
        db.executemany('INSERT INTO public.coc_travelmatrix_center_lookup VALUES (?,?)',
                       [('a', 'NCI'), ('b', 'NCI'), ('c', 'Academic Comprehensive Cancer Program'),
                        ('d', 'Other')])
        db.execute('CREATE TABLE public.coc_travelmatrix_zcta_to_center_car_p50 '
                   '(from_id INTEGER, to_id TEXT, travel_time_p50 INTEGER)')
        db.executemany('INSERT INTO public.coc_travelmatrix_zcta_to_center_car_p50 VALUES (?,?,?)',
                       [(1234, 'a', 60), (1234, 'b', 25), (1234, 'c', 40), (1234, 'd', 1),
                        (1234, 'a', -1), (1234, 'a', None), (1234, 'missing', 2),
                        (43001, 'a', 0), (43002, 'a', None)])
        cursor = MagicMock()
        cursor.fetchone.return_value = ('matrix',)
        def execute(sql, params=()):
            if 'to_regclass' not in sql:
                cursor.fetchall.return_value = db.execute(sql.replace('%s', '?'), params).fetchall()
        cursor.execute.side_effect = execute
        with patch('popcase.cancer_center_access.connections') as connections:
            connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = cursor
            result = get_cancer_center_access_lookup('zcta', ['nci', 'coc'])
            self.assertEqual(result['01234'], {OUTPUTS['nci']: 25, OUTPUTS['coc']: 40})
            self.assertEqual(result['43001'], {OUTPUTS['nci']: 0, OUTPUTS['coc']: None})
            self.assertEqual(result['43002'], {OUTPUTS['nci']: None, OUTPUTS['coc']: None})
            result = get_cancer_center_access_lookup('zcta', ['coc'])
            self.assertEqual(result, {'01234': {OUTPUTS['coc']: 40},
                                     '43001': {OUTPUTS['coc']: None}, '43002': {OUTPUTS['coc']: None}})

    def test_missing_place_table_is_unavailable(self):
        with patch('popcase.cancer_center_access.connections') as connections:
            cursor = connections.__getitem__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = (None,)
            with self.assertLogs('popcase.cancer_center_access', level='WARNING'):
                self.assertEqual(get_cancer_center_access_lookup('place', ['nci']), {})
            self.assertEqual(cursor.execute.call_count, 1)

    def test_controls_and_form_validation_for_all_community_levels(self):
        for level, label, field in [('tract', 'census tract', 'access_comm_tract'),
                                    ('county', 'County', 'access_comm_county'),
                                    ('zcta', 'ZCTA', 'access_comm_zcta_place'),
                                    ('place', 'Place', 'access_comm_zcta_place')]:
            with self.subTest(level=level):
                form = MeasuresForm({field: ['nci', 'coc']}, geographic_level=level)
                self.assertTrue(form.is_valid(), form.errors)
                html = render_to_string('popcase/wizard/measures.html',
                                        {'form': form, 'geographic_level': level,
                                         'current_step': 'measures', 'is_geo_strat': True})
                self.assertIn(f'Drive time to nearest cancer center from {label} centroid', html)
                for token in ('nci', 'coc'):
                    self.assertEqual(html.count(f'value="{token}"'), 1)
                self.assertEqual(views._get_measure_selections(form.cleaned_data, level)[1], ['nci', 'coc'])

    def test_dataset_and_csv_preserve_values_and_missing_categories(self):
        for level, geoid in [('county', '39001'), ('tract', '39001770100'),
                              ('zcta', '43001'), ('place', '3901000')]:
            with self.subTest(level=level):
                filtered = MagicMock()
                filtered.values_list.return_value = [('patient', '1')]
                links = MagicMock()
                links.filter.return_value = links
                links.values_list.return_value.distinct.return_value = [('patient', geoid)]
                support = {'cancer_center_access': {geoid: {OUTPUTS['nci']: 25}}} if level != 'place' else {}
                with patch.object(services, 'apply_naaccr_filters', return_value=filtered), \
                     patch.object(services.NaaccrPatientCensusLinking, 'objects', links), \
                     patch.object(services, '_get_geo_support_lookups', return_value=support), \
                     patch.object(services, '_get_period_community_lookups_cached', return_value=({}, set())), \
                     patch.object(services, '_geoid_in_scope', return_value=True), \
                     patch.object(services, '_geo_label', return_value='Example'):
                    rows = services._build_geo_dataset_uncached(level, ('2020', '2020'), {}, [], ['nci', 'coc'])
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0][OUTPUTS['nci']], None if level == 'place' else 25)
                self.assertIsNone(rows[0][OUTPUTS['coc']])
                field = {'county': 'access_comm_county', 'tract': 'access_comm_tract'}.get(level, 'access_comm_zcta_place')
                request = RequestFactory().get('/export/')
                request.user = SimpleNamespace(is_authenticated=True)
                request.session = {'popcase_wizard': {'geographic_level': level,
                    'filters': {'dx_start': '2020', 'dx_end': '2020'}, 'measures': {field: ['nci', 'coc']}}}
                with patch.object(views, '_latest_linking_year', return_value='2020'), \
                     patch.object(views, 'get_default_diagnosis_quarter_range', return_value=('2020', '2020')), \
                     patch.object(views, '_build_results_payload_cached', return_value={'dataset_rows': rows}):
                    response = views.export_geo_dataset_csv(request)
                records = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
                self.assertIn(views.DATASET_HEADER_MAP[OUTPUTS['nci']], records[0])
                self.assertIn(views.DATASET_HEADER_MAP[OUTPUTS['coc']], records[0])
