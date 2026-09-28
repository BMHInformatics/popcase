import csv
import io
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, RequestFactory
from django.db import DatabaseError
from django.template.loader import render_to_string

from popcase import patient_access as access, views
from popcase.forms import GeographicLevelForm, MeasuresForm, StratificationForm


class PatientAccessTests(SimpleTestCase):
    def test_exact_sources_scaling_zero_and_invalid_values(self):
        for token in ['pcp', 'onc', 'ext_care', 'mammo_fac']:
            with self.subTest(token=token), patch.object(access, 'connections') as connections:
                cursor = connections.__getitem__.return_value.cursor.return_value.__enter__.return_value
                cursor.fetchall.return_value = [(390017701001, .002), (390017701002, 0),
                    (390017701003, None), (390017701004, float('nan')), (390017701005, -1),
                    (390017701006, 1), (390017701006, 2), (390017701006, 3)]
                result = access._source_values(token)
                self.assertEqual(result, {'390017701001': .002 if token == 'mammo_fac' else 200,
                                          '390017701002': 0})
                if token == 'mammo_fac':
                    self.assertIn('mammo_per_100k', cursor.execute.call_args.args[0])
                    connections.__getitem__.assert_called_with('default')
                else:
                    self.assertEqual(cursor.execute.call_args.args[1], [access.PROVIDER_SOURCES[token]])
                    connections.__getitem__.assert_called_with('popcase_manual_etl')
                cursor.execute.side_effect = DatabaseError('unavailable')
                with self.assertLogs('popcase.patient_access', level='ERROR'):
                    self.assertEqual(access._source_values(token), {})

    def test_link_vintage_and_conflicting_links(self):
        with patch.object(access.NaaccrPatientCensusLinking, 'objects') as manager:
            manager.filter.return_value.values_list.return_value.iterator.return_value = iter([
                ('P1', '390017701001'), ('P1', '390017701001'),
                ('P2', '390017701001'), ('P2', '390017701002'), ('P3', None)])
            links = access.get_patient_links('2023')
            manager.filter.assert_called_once_with(geographic_level='block_group', year='2023')
            self.assertEqual(links['P1'], {'390017701001'})
            self.assertEqual(len(links['P2']), 2)
            self.assertEqual(links['P3'], {None})

    def record(self, mid='P1', sequence='00', **kwargs):
        return dict(dict.fromkeys(access.RECORD_FIELDS, ''), mid=mid, sequence_number=sequence,
                    record_key=sequence, age_at_dx='50', race1='01', hispanic_origin='0', stg_grp='3', **kwargs)

    def dataset(self, records, filters=None):
        with patch.object(access, 'get_patient_links', return_value={
                'P1': {'390017701001'}, 'P2': {'390017701001', '390017701002'}}), \
             patch.object(access, 'get_block_group_access_lookup', return_value={
                '390017701001': {access.OUTPUTS['pcp']: 0}}), \
             patch.object(access.services, 'apply_naaccr_filters') as apply_filters:
            apply_filters.return_value.values.return_value.order_by.return_value.iterator.return_value = iter(records)
            rows = access.build_patient_dataset(('2020q1', '2022q4'), filters or {}, ['pcp', 'mammo_fac'])
            passed = apply_filters.call_args.args[1]
            self.assertEqual(passed['dx_start'], '2020q1')
            self.assertEqual(passed['geography'], 'all_ohio')
            self.assertEqual(passed['race'], 'all')
            return rows

    def test_distinct_tumors_missing_links_and_missing_values(self):
        rows = self.dataset([self.record(sequence='01'), self.record(sequence='02'),
                             self.record(mid='P2'), self.record(mid='P3')])
        self.assertEqual(len(rows), 4)
        self.assertEqual([r['sequence_number'] for r in rows[:2]], ['01', '02'])
        self.assertEqual(rows[0][access.OUTPUTS['pcp']], 0)
        self.assertIsNone(rows[0][access.OUTPUTS['mammo_fac']])
        self.assertEqual(rows[2]['linkage_status'], 'Conflicting links')
        self.assertEqual(rows[3]['linkage_status'], 'Missing link')
        self.assertNotIn('record_key', rows[0])
        self.assertIsNone(rows[2][access.OUTPUTS['pcp']])

    def test_county_uses_selected_link_vintage_and_excludes_unlocated_records(self):
        records = [self.record(), self.record(mid='P2'), self.record(mid='P3')]
        self.assertEqual(len(self.dataset(records, {'geography': 'county:39001'})), 1)
        self.assertEqual(self.dataset(records, {'geography': 'county:39003'}), [])

    def test_demographic_filters_regional_hispanic_unknown_age(self):
        record = self.record()
        self.assertTrue(access._matches_demographics(record, {'stage': ['regional'], 'race': ['nh_white']}))
        self.assertFalse(access._matches_demographics(record, {'stage': ['localized']}))
        self.assertFalse(access._matches_demographics(record, {'race': ['hisp_any']}))
        record['hispanic_origin'] = '1'
        self.assertTrue(access._matches_demographics(record, {'race': ['hisp_any']}))
        self.assertFalse(access._matches_demographics(record, {'race': ['nh_white']}))
        record['age_at_dx'] = '999'
        self.assertFalse(access._matches_demographics(record, {'age_groups': ['age_90_plus']}))
        record['age_at_dx'] = 'unknown'
        self.assertFalse(access._matches_demographics(record, {'age_from': 0, 'age_to': 120}))

    def request(self, staff=False):
        request = RequestFactory().get('/results/')
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_staff=staff, is_superuser=False)
        request.session = {'popcase_wizard': {'geographic_level': 'patient', 'measures': {
            'access_patient_measures': ['pcp', 'mammo_fac'], 'disease_measures': ['case_count']},
            'stratification': {'row_variable': 'sex'}, 'filters': {'dx_start': '2020q1', 'dx_end': '2022q4'}}}
        return request

    def test_unauthorized_results_export_and_wizard_fail_before_data_read(self):
        with patch.object(views, 'build_patient_dataset') as reader, patch.object(views, '_latest_linking_year') as year:
            self.assertEqual(views.results(self.request()).status_code, 403)
            self.assertEqual(views.export_geo_dataset_csv(self.request()).status_code, 403)
            self.assertEqual(views.wizard_step(self.request(), 'measures').status_code, 403)
            reader.assert_not_called()
            year.assert_not_called()
        form = GeographicLevelForm({'geographic_level': 'patient'})
        self.assertFalse(form.is_valid())
        self.assertTrue(GeographicLevelForm({'geographic_level': 'patient'}, allow_patient=True).is_valid())

    def test_patient_forms_discard_aggregate_and_grouping_selections(self):
        self.assertFalse(MeasuresForm({'disease_measures': ['case_count']}, geographic_level='patient').is_valid())
        form = MeasuresForm({'disease_measures': ['case_count'], 'access_patient_measures': ['onc']}, geographic_level='patient')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['disease_measures'], [])
        form = StratificationForm({'row_variable': 'sex', 'col_variable': 'sex'}, geographic_level='patient')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['row_variable'], '')

    def test_export_uncached_zero_missing_and_empty_headers(self):
        rows = self.dataset([self.record()])
        with patch.object(views, 'build_patient_dataset', side_effect=[rows, []]) as reader, \
             patch.object(views, '_build_results_payload_cached') as cached, \
             patch.object(views, 'get_default_diagnosis_quarter_range', return_value=('2020q1', '2022q4')):
            response = views.export_geo_dataset_csv(self.request(staff=True))
            empty = views.export_geo_dataset_csv(self.request(staff=True))
            cached.assert_not_called()
            self.assertEqual(reader.call_count, 2)
        data = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(data[1][data[0].index(access.HEADERS['patient_id'])], 'P1')
        self.assertEqual(data[1][data[0].index(access.HEADERS[access.OUTPUTS['pcp']])], '0')
        self.assertEqual(data[1][data[0].index(views.DATASET_HEADER_MAP[access.OUTPUTS['mammo_fac']])], '')
        self.assertEqual(list(csv.reader(io.StringIO(empty.content.decode('utf-8-sig'))))[0], data[0])

    def test_patient_pages_render_and_ignore_stale_stratification(self):
        html = render_to_string('popcase/wizard/measures.html', {
            'is_patient_level': True, 'is_geo_strat': False,
            'form': MeasuresForm(geographic_level='patient'), 'current_step': 'measures'})
        for token in access.OUTPUTS:
            self.assertIn(f'value="{token}"', html)
        self.assertNotRegex(html, r'<input[^>]*name="disease_measures"')
        self.assertNotIn('under development', html)
        rows = self.dataset([self.record()])
        with patch.object(views, 'build_patient_dataset', return_value=rows), \
             patch.object(views, 'get_default_diagnosis_quarter_range', return_value=('2020q1', '2022q4')), \
             patch.object(views, '_build_cancer_type_labels', return_value=[]):
            response = views.results(self.request(staff=True))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        html = response.content.decode()
        self.assertIn('Registry patient ID', html)
        self.assertIn('P1', html)
        self.assertEqual(html.count('One row per qualifying cancer registry record'), 1)
