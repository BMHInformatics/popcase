"""County-assigned whole-area selection for ZCTA and Place."""
from unittest.mock import MagicMock, patch

from django.db import DatabaseError
from django.test import SimpleTestCase

from popcase import county_crosswalks as cw, services
from popcase.forms import FiltersForm
from popcase.incidence_rates import subcounty_incidence
from popcase.rate_statistics import RateDataUnavailable
from popcase.stratified_rates import IncidenceCalculator


class CountyCrosswalkTests(SimpleTestCase):
    def setUp(self):
        cw.county_assignments.cache_clear()
        self.addCleanup(cw.county_assignments.cache_clear)

    def test_source_mapping_place_column_and_cache(self):
        with patch.object(cw, 'connections') as connections:
            cur = connections.__getitem__.return_value.cursor.return_value.__enter__.return_value
            cur.fetchall.return_value = [('39035', '3916000'), ('39035', '3916000')]
            self.assertEqual(cw.county_assignments('place'), {'3916000': '39035'})
            self.assertEqual(cw.geoids_for_counties('place', {'39035'}), {'3916000'})
            cur.execute.assert_called_once()
            self.assertIn('county2020_place2020_crosswalk', cur.execute.call_args.args[0])
            self.assertIn('zcta_geoid', cur.execute.call_args.args[0])

    def test_missing_empty_malformed_and_conflicting_sources_fail_closed(self):
        with patch.object(cw, 'connections') as connections:
            cur = connections.__getitem__.return_value.cursor.return_value.__enter__.return_value
            for rows in ([], [('39035', None)], [('39035', '44101'), ('39153', '44101')]):
                cur.fetchall.return_value = rows
                with self.assertRaises(RateDataUnavailable):
                    cw.county_assignments('zcta')
            cur.execute.side_effect = DatabaseError('missing')
            with self.assertRaises(RateDataUnavailable):
                cw.county_assignments('zcta')

    def test_scopes_use_assignments_for_single_multiple_and_catchment(self):
        for level, geoids in [('zcta', ['44101', '44301', '45650']),
                              ('place', ['3916000', '3901000', '3968196'])]:
            mapping = dict(zip(geoids, ['39035', '39153', '39001']))
            with patch.object(services, 'county_assignments', return_value=mapping):
                for filters, expected in [({'geography': 'county:39035'}, geoids[:1]),
                    ({'geography': 'counties', 'counties': ['39035', '39153']}, geoids[:2]),
                    ({'geography': 'neo15'}, geoids[:2]),
                    ({'geography': 'all_ohio'}, geoids)]:
                    self.assertEqual(set(services._filter_lookup_to_scope(dict.fromkeys(geoids, 10), level, filters)), set(expected))
                self.assertFalse(services._geoid_in_scope(level, '99999', {'geography': 'county:39035'}))

    def test_forms_accept_county_scope_and_report_unavailable_crosswalk(self):
        for level in ('zcta', 'place'):
            with patch('popcase.forms.get_diagnosis_quarter_choices', return_value=(('2020q1', '2020q1'),)), \
                 patch('popcase.forms.get_default_diagnosis_quarter_range', return_value=('2020q1', '2020q1')), \
                 patch('popcase.forms.county_assignments', return_value={'area': '39035'}) as crosswalk:
                data = {'sex': 'all', 'geography': 'counties', 'counties': ['39035']}
                form = FiltersForm(data, geographic_level=level)
                self.assertTrue(form.is_valid(), form.errors)
                crosswalk.side_effect = RateDataUnavailable('Crosswalk unavailable')
                form = FiltersForm(data, geographic_level=level)
                self.assertFalse(form.is_valid())
                self.assertIn('Crosswalk unavailable', str(form.errors))

    def test_patient_filter_uses_whole_area_links_not_patient_county(self):
        for level, geoid in [('zcta', '44101'), ('place', '3916000')]:
            with patch.object(services, 'geoids_for_counties', return_value={geoid}), \
                 patch.object(services, 'NaaccrPatientCensusLinking') as links:
                qs = MagicMock()
                qs.filter.return_value = qs
                services.apply_naaccr_filters(qs, {'geography': 'county:39035'}, geographic_level=level)
                links.objects.filter.assert_called_once_with(geographic_level=level, geoid__in={geoid})

    def test_rates_keep_whole_area_counts_and_denominators(self):
        band = (60, 64)
        filters = {'geography': 'county:39035', 'dx_start': '2020q1', 'dx_end': '2020q4', 'age_groups': ['age_60_64']}
        for level, selected, excluded in [('zcta', '44101', '45650'), ('place', '3916000', '3968196')]:
            with patch.object(services, 'county_assignments', return_value={selected: '39035', excluded: '39001'}), \
                 patch.object(services, '_geo_label', side_effect=lambda level, geoid: geoid), \
                 patch('popcase.incidence_rates.load_case_counts', return_value=({selected: 10, excluded: 20}, {}, {band: 100}, {})), \
                 patch('popcase.incidence_rates.load_target_populations', return_value=({selected: {band: 1000}, excluded: {band: 2000}}, {})), \
                 patch('popcase.incidence_rates.load_ohio_decennial_population', return_value={band: 10000}):
                for mortality in (False, True):
                    rows = subcounty_incidence('2020', level, filters, mortality=mortality)
                    self.assertEqual([r['geoid'] for r in rows], [selected])
                    self.assertEqual(rows[0]['case_count'], 10)
                    self.assertEqual(rows[0]['crude_incidence_per_100k'], 1000)
            with patch('popcase.stratified_rates.load_target_populations', return_value=({selected: {band: 1000}}, {})):
                calculator = IncidenceCalculator(level, filters, [], '2020')
                result = calculator.calculate(selected, (), [{'age_at_dx': '62'}] * 10, {'crude_inc_rate'})
                self.assertEqual(result['crude_incidence_per_100k'], 1000)
