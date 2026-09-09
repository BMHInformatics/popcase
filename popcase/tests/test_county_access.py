import csv
import io
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, RequestFactory

from popcase.county_access import count_providers, provider_categories, get_county_access_lookup, OUTPUTS
from popcase import services, views


class CountyCountingTests(SimpleTestCase):
    def test_specialty_rules_and_overlap(self):
        self.assertEqual(provider_categories('INTERNAL MEDICINE', 'CARDIOLOGY'), set())
        self.assertEqual(provider_categories(' internal medicine ', 'PEDIATRIC MEDICINE'), {'pcp_access_score'})
        self.assertEqual(provider_categories('NURSE PRACTITIONER', ''), {'pcp_access_score'})
        self.assertEqual(provider_categories('FAMILY PRACTICE', 'HEMATOLOGY'), {'pcp_access_score', 'onc', 'ext_care'})
        self.assertEqual(provider_categories('UROLOGY', ''), {'ext_care'})
        self.assertEqual(provider_categories('RADIOLOGY', 'UROLOGY'), set())

    def test_counts_distinct_npi_per_category_and_county(self):
        rows = [
            ('1234567890','FAMILY PRACTICE','','1 Main','44101',['39035']),
            ('1234567890','FAMILY PRACTICE','','2 Main','44101',['39035']),
            ('1234567890','HEMATOLOGY','','2 Main','44101',['39035']),
            ('1234567890','FAMILY PRACTICE','','3 Main','44001',['39001']),
        ]
        counts, audit = count_providers(rows)
        self.assertEqual(counts['39035'], {'pcp_access_score': 1, 'onc': 1, 'ext_care': 1})
        self.assertEqual(counts['39001'], {'pcp_access_score': 1})
        self.assertEqual(audit['unassigned_records'], 0)

    def test_recovery_requires_unique_exact_address_and_zip(self):
        rows = [
            ('1234567890','FAMILY PRACTICE','','1 MAIN','441010000',['39035']),
            ('1234567891','FAMILY PRACTICE','',' 1 main ','44101',[]),
            ('1234567892','FAMILY PRACTICE','','1 MAIN','44001',[]),
            ('1234567893','FAMILY PRACTICE','','ambiguous','44101',['39035','39001']),
        ]
        counts, audit = count_providers(rows)
        self.assertEqual(counts['39035']['pcp_access_score'], 2)
        self.assertEqual(audit['recovered_records'], 1)
        self.assertEqual(audit['unassigned_qualifying_records'], 2)

    def test_invalid_npi_is_not_a_clinician(self):
        counts, audit = count_providers([('', 'FAMILY PRACTICE', '', '1 Main', '44101', ['39035'])])
        self.assertEqual(counts, {})
        self.assertEqual(audit['invalid_npi_records'], 1)

    def test_rates_use_population_and_keep_zero_distinct_from_missing(self):
        with patch('popcase.county_access.connections') as connections:
            cur = connections.__getitem__.return_value.cursor.return_value.__enter__.return_value
            cur.fetchall.side_effect = [
                [('0500000US39035',1000), ('0500000US39001',2000), ('0500000US39003',0)],
                [('1234567890','FAMILY PRACTICE','','1 MAIN','44101',['39035'])],
                [('FACILITY','1 MAIN','CITY',['39035'])],
            ]
            result = get_county_access_lookup(OUTPUTS)
        self.assertEqual(result['39035']['primary_care_providers_per_100k'], 100)
        self.assertEqual(result['39035']['mammography_facilities_per_100k'], 100)
        self.assertEqual(result['39001']['primary_care_providers_per_100k'], 0)
        self.assertIsNone(result['39003']['primary_care_providers_per_100k'])

    def test_empty_source_is_unavailable_not_zero(self):
        with patch('popcase.county_access.connections') as connections:
            cur = connections.__getitem__.return_value.cursor.return_value.__enter__.return_value
            cur.fetchall.side_effect = [[('0500000US39035',1000)], []]
            with self.assertLogs('popcase.county_access', level='WARNING'):
                result = get_county_access_lookup(['pcp_access_score'])
        self.assertIsNone(result['39035']['primary_care_providers_per_100k'])
        self.assertIn('unavailable', result['39035']['provider_data_note'])


class SurveyDisplayTests(SimpleTestCase):
    def test_all_four_checkbox_combinations(self):
        tokens = ['routine_checkup', 'no_transport', 'no_insurance', 'dentist']
        for ci in [False, True]:
            for age in [False, True]:
                options = []
                if ci:
                    options.append('access_comm_county_survey_ci')
                if age:
                    options.append('access_comm_county_survey_age_adjusted')
                for token in tokens:
                    value, low, high, adjusted = services.SUPPORT_MEASURE_OUTPUT_SPECS[token]
                    adjusted_low = adjusted.replace('_pct', '_ci_lower')
                    adjusted_high = adjusted.replace('_pct', '_ci_upper')
                    row = {value: 60, low: 55, high: 65, adjusted: 50, adjusted_low: 44, adjusted_high: 56}
                    services._apply_display_option_contract(row, [token], options)
                    self.assertEqual(low in row, ci)
                    self.assertEqual(adjusted in row, age)
                    self.assertEqual(adjusted_low in row, ci and age)
                    self.assertEqual(adjusted_high in row, ci and age)

    def test_county_provider_and_adjusted_interval_csv_headers(self):
        request = RequestFactory().get('/export/')
        request.user = SimpleNamespace(is_authenticated=True)
        request.session = {'popcase_wizard': {
            'geographic_level': 'county', 'filters': {'dx_start': '2019q1', 'dx_end': '2023q4'},
            'measures': {'access_comm_county': ['pcp','onc','ext_care','mammo_fac','dentist'],
                         'access_comm_county_survey_ci': True,
                         'access_comm_county_survey_age_adjusted': True},
        }}
        row = {'label': 'Example', 'geoid': '39001', **{key: 12 for key in OUTPUTS.values()},
               'dentist_pct': 60, 'dentist_age_adjusted_pct': 50,
               'dentist_age_adjusted_ci_lower': 44, 'dentist_age_adjusted_ci_upper': 56}
        with patch.object(views, '_latest_linking_year', return_value='2023'), \
             patch.object(views, 'get_default_diagnosis_quarter_range', return_value=('2019q1','2023q4')), \
             patch.object(views, '_build_results_payload_cached', return_value={'dataset_rows': [row]}):
            response = views.export_geo_dataset_csv(request)
        records = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertIn('Oncology providers per 100,000 in county', records[0])
        self.assertIn('Dentist visit age-adjusted CI 95% (L)', records[0])
