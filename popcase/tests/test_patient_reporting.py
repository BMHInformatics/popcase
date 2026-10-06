import csv
import io
import json
from contextlib import ExitStack
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, RequestFactory
from django.template.loader import render_to_string

from popcase import patient_reporting as reporting, views
from popcase.forms import MeasuresForm


class PatientReportingTests(SimpleTestCase):
    def test_patient_only_and_community_only_forms_and_all_override(self):
        for data in ({'patient_variables': ['sex', 'age_diagnosis']}, {'cancer_prevention': ['smoking']},
                     {'patient_community_access': ['dentist']}, {'patient_all_registry': True}):
            form = MeasuresForm(data, geographic_level='patient')
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.cleaned_data['patient_schema_version'], '2')
        form = MeasuresForm({'patient_all_registry': True, 'patient_variables': ['sex'],
            'access_patient_measures': ['pcp'], 'community_characteristics': ['pop_total', 'redlined_pct', 'svi_adi'],
            'patient_community_geography': 'zcta', 'patient_community_age_adjusted': True}, geographic_level='patient')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['patient_variables'], [])
        self.assertEqual(form.cleaned_data['access_patient_measures'], [])
        self.assertEqual(form.cleaned_data['community_characteristics'], ['pop_total'])
        self.assertFalse(form.cleaned_data['patient_community_age_adjusted'])

    def test_wireframe_controls_are_rendered_once(self):
        html = render_to_string('popcase/wizard/measures.html', {'form': MeasuresForm(geographic_level='patient'),
            'is_patient_level': True, 'is_geo_strat': False, 'current_step': 'measures'})
        for token, _ in reporting.VARIABLE_CHOICES:
            self.assertEqual(html.count(f'name="patient_variables" value="{token}"'), 1)
        for text in ['Output all available registry variables', 'Variables describing the patient',
                     'Cancer Screening (Community-level)', 'Household characteristics', 'Social Vulnerability Index']:
            self.assertIn(text, html)
        for level in ('tract', 'county', 'place', 'zcta'):
            self.assertIn(f'name="patient_community_geography" value="{level}"', html)

    def test_complete_dates_latest_diagnosis_and_treatment_rule(self):
        raw = {'Date of Birth': '19700615', 'Date of Diagnosis': '20200601',
               'Date Conclusive DX': '20200620', 'Date of Last Contact': '20210614',
               'Date 1st Crs RX CoC': '20200701', 'RX Date Surgery': '20200625'}
        values = reporting.derived_values(raw, ['age_diagnosis', 'age_followup', 'tti_first', 'tti_modalities'])
        self.assertEqual(values['age_at_diagnosis_years'], 50)
        self.assertEqual(values['age_at_death_or_followup_years'], 50)
        self.assertEqual(values['time_to_first_treatment_days'], 11)
        self.assertEqual(values['time_to_surgery_days'], 5)
        raw['Date 1st Crs RX CoC'] = '20200700'
        self.assertEqual(reporting.derived_values(raw, ['tti_first'])['time_to_first_treatment_days'], 5)
        raw['RX Date Surgery'] = '20200610'
        self.assertIsNone(reporting.derived_values(raw, ['tti_first'])['time_to_first_treatment_days'])
        self.assertIsNone(reporting.completed_age(None, date(2020, 1, 1)))
        self.assertIsNone(reporting.completed_age(date(2021, 1, 1), date(2020, 1, 1)))
        raw['Date of Birth'] = '19700600'
        self.assertIsNone(reporting.derived_values(raw, ['age_diagnosis'])['age_at_diagnosis_years'])

    def test_gleason_and_breast_subtypes_keep_site_specific_meaning(self):
        raw = {'Primary Site': 'C619', 'Gleason Score Pathological': '99', 'Gleason Score Clinical': '7'}
        self.assertEqual(reporting.derived_values(raw, ['gleason'])['gleason_score'], 7)
        raw.update({'Primary Site': 'C509', 'Estrogen Receptor Summary': '0',
                    'Progesterone Receptor Summary': '1', 'HER2 Overall Summary': '0'})
        self.assertIsNone(reporting.derived_values(raw, ['gleason'])['gleason_score'])
        self.assertEqual(reporting.receptor_subtype(raw), 'HR+/HER2-')
        raw['Progesterone Receptor Summary'] = '0'
        self.assertEqual(reporting.receptor_subtype(raw), 'Triple negative')
        raw['HER2 Overall Summary'] = '9'
        self.assertEqual(reporting.receptor_subtype(raw), 'Unknown receptor status')

    def test_followup_age_preserves_source_dates_even_before_diagnosis(self):
        raw = {'Date of Birth': '19561215', 'Date of Diagnosis': '20190610',
               'Date Conclusive DX': '20190610', 'Date of Last Contact': '20171121'}
        values = reporting.derived_values(raw, ['age_diagnosis', 'age_followup'])
        self.assertEqual(values['age_at_diagnosis_years'], 62)
        self.assertEqual(values['age_at_death_or_followup_years'], 60)
        raw['Date of Last Contact'] = '20190610'
        self.assertEqual(reporting.derived_values(raw, ['age_followup'])['age_at_death_or_followup_years'], 62)
        raw['Date Conclusive DX'] = '20200610'
        self.assertEqual(reporting.derived_values(raw, ['age_followup'])['age_at_death_or_followup_years'], 62)

    def test_preview_decodes_categories_without_mutating_raw_export_values(self):
        from popcase.registry_labels import display_row
        raw = {'Sex': '1', 'Race 1': '01', 'Spanish/Hispanic Origin': '0',
               'Perineural Invasion': '9', 'Patient ID Number': '00000338',
               'Histologic Type ICD-O-3': '8140', 'Regional Nodes Examined': '99'}
        shown = display_row(raw)
        self.assertEqual([shown[k] for k in ('Sex', 'Race 1', 'Spanish/Hispanic Origin')],
                         ['Male', 'White', 'Non-Hispanic'])
        self.assertEqual(shown['Patient ID Number'], '00000338')
        self.assertEqual(shown['Histologic Type ICD-O-3'], '8140')
        self.assertEqual(shown['Regional Nodes Examined'], 'Unknown whether examined')
        self.assertEqual(raw['Sex'], '1')
        self.assertEqual(display_row({'Race 1': '96'})['Race 1'], 'Other Asian')
        self.assertEqual(display_row({'Sex': 'unexpected'})['Sex'], 'Unrecognized code (unexpected)')
        self.assertIsNone(display_row({'Sex': None})['Sex'])

    def report(self, options, level='tract'):
        stack = ExitStack()
        self.addCleanup(stack.close)
        columns = ['Patient ID Number', 'Sex', 'Sequence Number_Central', 'Extra registry field']
        stack.enter_context(patch.object(reporting, 'registry_columns', return_value=columns))
        stack.enter_context(patch.object(reporting, 'patient_geographies', return_value={
            level: {'P1': {'39001770100'}, 'P2': {'39001770101', '39001770102'}}}))
        builder = stack.enter_context(patch.object(reporting.services, 'build_geo_dataset', return_value=[
            {'geoid': '39001770100', 'label': 'Community', 'total_population': 1000}]))
        filtered = stack.enter_context(patch.object(reporting.services, 'apply_naaccr_filters'))
        records = []
        for patient, sequence in [('P1', '01'), ('P1', '02'), ('P2', '00'), ('P3', '00')]:
            raw = dict(zip(columns, [patient, '2', sequence, 'kept']))
            record = dict.fromkeys(reporting.patient_access.RECORD_FIELDS, '')
            record.update(mid=patient, sequence_number=sequence, registry_json=json.dumps(raw))
            records.append(record)
        filtered.return_value.values.return_value.order_by.return_value.iterator.return_value = iter(records)
        report = reporting.prepare_patient_report(('2020q1', '2022q4'), {}, options)
        return report, builder

    def test_community_join_preserves_tumors_missing_and_ambiguous_links(self):
        for level in ('tract', 'zcta', 'place', 'county'):
            report, builder = self.report({'patient_variables': ['sex'], 'community_characteristics': ['pop_total'],
                                          'patient_community_geography': level}, level)
            rows = list(report.rows)
            self.assertEqual(len(rows), 4)
            self.assertEqual([r['community_total_population'] for r in rows], [1000, 1000, None, None])
            self.assertEqual(rows[2]['community_linkage_status'], 'Conflicting links')
            self.assertEqual(rows[3]['community_linkage_status'], 'Missing link')
            self.assertEqual(builder.call_args.args[0], level)
            self.assertEqual(builder.call_args.args[2], {})
            self.assertEqual(builder.call_args.kwargs['support_geoid_scope'], {'39001770100'})
            self.assertIn('Sex', report.columns)
            self.assertNotIn('Extra registry field', report.columns)

    def test_geographic_builder_limits_rows_without_changing_area_values(self):
        services = reporting.services
        field = services.SUPPORT_MEASURE_OUTPUT_SPECS['smoking'][0]
        filtered = MagicMock()
        filtered.values_list.return_value = []
        sources = {'places': {'3901000': {field: 12}, '3902000': {field: 25}}}
        with patch.object(services, 'apply_naaccr_filters', return_value=filtered), \
             patch.object(services, '_get_geo_support_lookups', return_value=sources), \
             patch.object(services, '_get_period_community_lookups_cached', return_value=({}, False)), \
             patch.object(services, '_geo_label', return_value='Community'):
            rows = services.build_geo_dataset('place', ('2020', '2022'), {}, [], ['smoking'],
                                              support_geoid_scope={'3901000'})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['geoid'], '3901000')
        self.assertEqual(rows[0][field], 12)
        self.assertEqual(len(sources['places']), 2)  # Shared source cache is untouched.

    def test_source_scope_is_parameterized_uncached_and_reset_after_errors(self):
        services = reporting.services
        services._get_geo_support_lookups_cached.cache_clear()
        self.addCleanup(services._get_geo_support_lookups_cached.cache_clear)
        observed = []
        def source(*args):
            observed.append(services._community_where_sql('acs_source', 'zcta', 'acs', '2019-2023'))
            return {}
        with patch.object(services, '_get_table_column_map', return_value={'geo_id': 'GEO_ID'}), \
             patch.object(services, '_get_most_recent_community_lookup', side_effect=source) as reader, \
             patch.object(services, '_get_cdc_places_lookup', return_value={}):
            services._get_geo_support_lookups('zcta', ['pop_total'], geoid_scope={'43001'})
            self.assertIn('RIGHT("GEO_ID"::text, %s) = ANY(%s)', observed[0][0])
            self.assertEqual(observed[0][1], [5, ['43001']])
            self.assertEqual(services._get_geo_support_lookups_cached.cache_info().currsize, 0)
            self.assertIsNone(services._patient_community_source_scope.get())
            services._get_geo_support_lookups('zcta', ['pop_total'])
            self.assertEqual(observed[1], (None, []))
            reader.side_effect = RuntimeError('source unavailable')
            with self.assertRaises(RuntimeError):
                services._get_geo_support_lookups('zcta', ['pop_total'], geoid_scope={'43001'})
            self.assertIsNone(services._patient_community_source_scope.get())

    def test_all_registry_includes_unmodeled_fields_with_original_names(self):
        report, _ = self.report({'patient_all_registry': True})
        rows = list(report.rows)
        self.assertEqual(len(rows), 4)
        self.assertEqual(report.columns, ['Patient ID Number', 'Sex', 'Sequence Number_Central', 'Extra registry field'])
        self.assertEqual([r['Sequence Number_Central'] for r in rows[:2]], ['01', '02'])
        self.assertEqual(rows[0]['Extra registry field'], 'kept')
        self.assertEqual(report.headers['Extra registry field'], 'Extra registry field')

    def test_streamed_export_and_bounded_preview_use_same_schema(self):
        request = RequestFactory().get('/results/')
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_staff=True)
        options = {'patient_schema_version': '2', 'patient_all_registry': True}
        request.session = {'popcase_wizard': {'geographic_level': 'patient', 'measures': options,
            'filters': {'dx_start': '2020q1', 'dx_end': '2022q4'}}}
        def report(*args):
            return reporting.PatientReport(['Patient ID Number', 'Extra registry field'],
                {'Patient ID Number': 'Patient ID Number', 'Extra registry field': 'Extra registry field'},
                ({'Patient ID Number': str(i), 'Extra registry field': 0 if i == 0 else None} for i in range(205)))
        with patch.object(reporting, 'prepare_patient_report', side_effect=report), \
             patch.object(views, 'get_default_diagnosis_quarter_range', return_value=('2020q1', '2022q4')):
            response = views.export_geo_dataset_csv(request)
            self.assertTrue(response.streaming)
            data = list(csv.reader(io.StringIO(b''.join(response.streaming_content).decode('utf-8-sig'))))
            self.assertEqual(len(data), 206)
            self.assertEqual(data[1], ['0', '0'])
            self.assertEqual(data[2], ['1', ''])
            payload = views._build_results_payload(geographic_level='patient', dx_start='2020q1', dx_end='2022q4',
                filters_json='{}', patient_measures_json=json.dumps(options))
            self.assertEqual(len(payload['dataset_rows']), 200)
            self.assertEqual(payload['patient_row_count'], 205)
            self.assertEqual(payload['patient_columns'], data[0])
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_expanded_extract_requires_administrator_before_loading_data(self):
        request = RequestFactory().get('/export/')
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_staff=False, is_superuser=False)
        request.session = {'popcase_wizard': {'geographic_level': 'patient', 'measures': {
            'patient_schema_version': '2', 'patient_all_registry': True}}}
        with patch.object(reporting, 'prepare_patient_report') as reader:
            self.assertEqual(views.results(request).status_code, 403)
            self.assertEqual(views.export_geo_dataset_csv(request).status_code, 403)
            reader.assert_not_called()
