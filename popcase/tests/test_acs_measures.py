import math
import csv
import io
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, RequestFactory
from django.template.loader import render_to_string

from popcase.acs_measures import (
    ACS_MEASURES, COMPONENT_COLUMNS, calculate_component,
    get_community_lookup, numeric_value,
)
from popcase import services, views
from popcase.forms import MeasuresForm


def component(token, index=0):
    return ACS_MEASURES[token][2][index]


class ACSCalculationTests(SimpleTestCase):
    def test_married_uses_population_total_and_both_margins(self):
        row = {'B07008_001E': 1000, 'B07008_001M': 50,
               'B07008_003E': 600, 'B07008_003M': 40}
        result = calculate_component(row, 'B07008', component('marital_status'))
        self.assertEqual(result['marital_married_pct'], 60)
        half_width = 100 * (1.96 / 1.645) * math.sqrt(40**2 - .6**2 * 50**2) / 1000
        self.assertEqual(result['marital_married_ci_lower'], round(60 - half_width, 2))
        self.assertEqual(result['marital_married_ci_upper'], round(60 + half_width, 2))

    def test_not_married_sums_disjoint_categories_and_moes(self):
        row = {'B07008_001E': 1000, 'B07008_001M': 50}
        for cell, count in [(2, 100), (4, 150), (5, 50), (6, 100)]:
            row[f'B07008_{cell:03d}E'] = count
            row[f'B07008_{cell:03d}M'] = 10
        result = calculate_component(row, 'B07008', component('marital_status', 1))
        self.assertEqual(result['marital_not_married_pct'], 40)
        # sqrt(4 * 10^2 - .4^2 * 50^2) = 0, not a summed MOE of 40.
        self.assertEqual(result['marital_not_married_ci_lower'], 40)

    def test_ratio_fallback_and_percent_bounds(self):
        row = {'B07008_001E': 100, 'B07008_001M': 80,
               'B07008_003E': 95, 'B07008_003M': 2}
        result = calculate_component(row, 'B07008', component('marital_status'))
        self.assertLess(result['marital_married_ci_lower'], 95)
        self.assertEqual(result['marital_married_ci_upper'], 100)

    def test_missing_moe_keeps_estimate_without_inventing_ci(self):
        for missing in [None, -999999999, 'NaN']:
            with self.subTest(missing=missing):
                row = {'B07008_001E': 1000, 'B07008_001M': missing,
                       'B07008_003E': 600, 'B07008_003M': 40}
                result = calculate_component(row, 'B07008', component('marital_status'))
                self.assertEqual(result['marital_married_pct'], 60)
                self.assertIsNone(result['marital_married_ci_lower'])

    def test_controlled_population_has_zero_denominator_sampling_error(self):
        row = {'B07008_001E': 1000, 'B07008_001M': -555555555, 'B07008_001MA': '*****',
               'B07008_003E': 600, 'B07008_003M': 164.5}
        result = calculate_component(row, 'B07008', component('marital_status'))
        self.assertEqual(result['marital_married_ci_lower'], 40.4)
        self.assertEqual(result['marital_married_ci_upper'], 79.6)

    def test_annotations_sentinels_and_missing_components_are_not_zero(self):
        self.assertIsNone(numeric_value(-888888888))
        self.assertIsNone(numeric_value(250000, '250,000+'))
        self.assertEqual(numeric_value(0, None), 0)
        for bad in [None, '', -666666666, float('inf')]:
            row = {'B07008_001E': 1000, 'B07008_002E': 100,
                   'B07008_004E': 100, 'B07008_005E': bad, 'B07008_006E': 100}
            result = calculate_component(row, 'B07008', component('marital_status', 1))
            self.assertTrue(all(value is None for value in result.values()))

    def test_zero_denominator_and_inconsistent_counts_are_unavailable(self):
        for denominator in [0, None, 50]:
            row = {'B07008_001E': denominator, 'B07008_003E': 60}
            self.assertIsNone(calculate_component(row, 'B07008', component('marital_status'))['marital_married_pct'])

    def test_median_is_published_value_with_95_percent_conversion(self):
        result = calculate_component({'B25105_001E': 1200, 'B25105_001M': 164.5},
                                     'B25105', component('median_housing_costs'))
        self.assertEqual(result, {'median_housing_costs': 1200,
                                 'median_housing_costs_ci_lower': 1004,
                                 'median_housing_costs_ci_upper': 1396})

    def test_age_groups_cover_each_sex_age_cell_once(self):
        cells = [cell for part in ACS_MEASURES['age_dist'][2] for cell in part.cells]
        self.assertEqual(sorted(cells), list(range(3, 26)) + list(range(27, 50)))
        row = {f'B01001_{cell:03d}E': 10 for cell in cells}
        row['B01001_001E'] = 460
        total = sum(calculate_component(row, 'B01001', part)[part.key] for part in ACS_MEASURES['age_dist'][2])
        self.assertAlmostEqual(total, 100, places=1)

    def test_limited_english_uses_all_less_than_very_well_cells(self):
        row = {f'C16001_{cell:03d}E': 5 for cell in range(5, 39, 3)}
        row.update({'C16001_001E': 1000, 'C16001_004E': 900})
        self.assertEqual(calculate_component(row, 'C16001', component('limited_english_pct'))['limited_english_pct'], 6)

    def test_household_denominators_and_overcrowding(self):
        fixtures = [
            ('grandparents_care', 'B10063', {1: 1000, 2: 200, 3: 50}, 5),
            ('female_headed', 'B11005', {1: 1000, 7: 100, 10: 20, 16: 50, 19: 30}, 20),
            ('occupants_per_room', 'B25014', {1: 1000, 5: 10, 6: 10, 7: 10, 11: 20, 12: 20, 13: 30}, 10),
        ]
        for token, code, cells, expected in fixtures:
            with self.subTest(token=token):
                row = {f'{code}_{cell:03d}E': value for cell, value in cells.items()}
                self.assertEqual(calculate_component(row, code, component(token))[component(token).key], expected)


class ACSIntegrationTests(SimpleTestCase):
    def test_reader_resolves_period_and_handles_mixed_case_columns(self):
        raw = {'GEO_ID': '1400000US39001000100', 'b07008_001e': 1000,
               'b07008_003e': 600, 'b07008_001m': 50, 'b07008_003m': 40}
        aliases = {'geo': 'GEO_ID', **{key.upper(): key for key in raw if key != 'GEO_ID'}}
        with patch.object(services, '_resolve_acs_table', return_value='census_build.acs_39_B07008_tract_2018') as resolve, \
             patch.object(services, '_community_where_sql', return_value=(None, [])), \
             patch.object(services, '_select_columns_from_table', return_value=([raw], aliases)):
            result = get_community_lookup(['marital_status'], 'tract', '2014-2018')
        resolve.assert_called_once_with('B07008', 'tract', '2014-2018')
        self.assertEqual(result['39001000100']['marital_married_pct'], 60)

    def test_missing_table_does_not_substitute_another_period(self):
        with patch.object(services, '_resolve_acs_table', return_value=None):
            self.assertEqual(get_community_lookup(['lang_home'], 'tract', '2009-2013'), {})

    def test_service_dispatch_includes_new_measures(self):
        with patch.object(services, 'get_community_lookup', return_value={'39001': {'marital_married_pct': 60}}) as lookup:
            result = services._get_acs_period_community_lookup(['marital_status'], 'county', '2019-2023')
        lookup.assert_called_once_with({'marital_status'}, 'county', '2019-2023')
        self.assertEqual(result['39001']['marital_married_pct'], 60)

    def test_all_measures_have_headers_order_and_ci_toggle(self):
        for token, columns in COMPONENT_COLUMNS.items():
            with self.subTest(token=token):
                row = {column: 50 for column in columns}
                option = next(key for key, tokens in services.CI_DISPLAY_OPTION_TO_TOKENS.items() if token in tokens)
                with_ci = dict(row)
                services._add_display_option_columns(with_ci, [token], [option], with_ci)
                services._apply_display_option_contract(with_ci, [token], [option])
                self.assertEqual(set(with_ci), set(columns))
                services._apply_display_option_contract(row, [token], [])
                self.assertEqual(set(row), set(columns[::3]))
                self.assertTrue(all(column in views.DATASET_HEADER_MAP for column in columns))
                actual = views._build_dataset_columns([with_ci], views.DATASET_HEADER_MAP,
                                                     views._support_columns_for_token(token, [option]))
                self.assertEqual(actual, columns)

    def test_historical_components_keep_bounds_without_empty_unsuffixed_columns(self):
        suffix = '__acs_2014_2018'
        columns = COMPONENT_COLUMNS['marital_status']
        row = {column + suffix: 50 for column in columns}
        services._add_display_option_columns(row, ['marital_status'], ['community_extended_ci'], row)
        self.assertEqual(set(row), {column + suffix for column in columns})
        services._apply_display_option_contract(row, ['marital_status'], [])
        self.assertEqual(set(row), {column + suffix for column in columns[::3]})
        headers = views._with_dynamic_community_headers(views.DATASET_HEADER_MAP, [row])
        self.assertIn('2014-2018', headers['marital_married_pct' + suffix])

    def test_historical_plan_contains_new_measures(self):
        for token in COMPONENT_COLUMNS:
            plan = services._community_period_plan('2014q1', '2018q4', ['historical'], 'tract', [token])
            self.assertEqual(plan, [{'source': 'acs', 'period': '2014-2018'}])

    def test_ui_has_precise_labels_and_ci_controls_on_every_geo_level(self):
        for geo in ['county', 'tract', 'place', 'zcta']:
            html = render_to_string('popcase/wizard/measures.html', {
                'form': MeasuresForm(geographic_level=geo), 'geographic_level': geo,
                'is_geo_strat': True, 'current_step': 'measures', 'steps': [],
            })
            self.assertIn('Family income-to-poverty ratio distribution', html)
            self.assertIn('categories may overlap', html)
            self.assertIn('name="community_extended_ci"', html)
            self.assertNotIn('Age distribution (__ groups)', html)

    def test_csv_exports_component_values_and_only_requested_bounds(self):
        for enabled in [False, True]:
            request = RequestFactory().get('/export/')
            request.user = SimpleNamespace(is_authenticated=True)
            request.session = {'popcase_wizard': {
                'geographic_level': 'county', 'filters': {'dx_start': '2019q1', 'dx_end': '2023q4'},
                'measures': {'community_characteristics': ['marital_status'],
                             'community_extended_ci': enabled},
            }}
            row = {'label': 'Example County', 'geoid': '39001', 'marital_married_pct': 60,
                   'marital_married_ci_lower': 55, 'marital_married_ci_upper': 65,
                   'marital_not_married_pct': 40, 'marital_not_married_ci_lower': 35,
                   'marital_not_married_ci_upper': 45}
            services._apply_display_option_contract(row, ['marital_status'],
                ['community_extended_ci'] if enabled else [])
            with patch.object(views, '_latest_linking_year', return_value='2023'), \
                 patch.object(views, 'get_default_diagnosis_quarter_range', return_value=('2019q1','2023q4')), \
                 patch.object(views, '_build_results_payload_cached', return_value={'dataset_rows': [row]}):
                response = views.export_geo_dataset_csv(request)
            records = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
            self.assertEqual(response.status_code, 200)
            index = records[0].index('Married, except separated (%)')
            self.assertEqual(records[1][index], '60')
            self.assertEqual(any('CI 95%' in title for title in records[0]), enabled)
