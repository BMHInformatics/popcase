import sqlite3
from unittest.mock import patch

from django.db.backends.sqlite3.base import DatabaseWrapper
from django.test import SimpleTestCase
from django.template.loader import render_to_string

from popcase.forms import FiltersForm
from popcase.models import NaaccrData
from popcase.rate_statistics import selected_age_bands, age_bands
from popcase.services import apply_naaccr_filters


class AgeRangeTests(SimpleTestCase):
    def form(self, data=None, level='county', initial=None):
        with patch('popcase.forms.get_diagnosis_quarter_choices', return_value=(('2020q1', '2020q1'),)), \
             patch('popcase.forms.get_default_diagnosis_quarter_range', return_value=('2020q1', '2020q1')):
            return FiltersForm(None if data is None else dict(sex='all', **data),
                               geographic_level=level, initial=initial)

    def test_defaults_and_rendered_menus(self):
        for level, first, last, count in [('county', 'age_00', 'age_90_plus', 20),
                                         ('tract', 'age_00', 'age_90_plus', 20),
                                         ('place', 'age_00_04', 'age_85_plus', 18),
                                         ('zcta', 'age_00_04', 'age_85_plus', 18)]:
            with self.subTest(level=level):
                form = self.form(level=level)
                self.assertEqual(form['age_group_from'].value(), first)
                self.assertEqual(form['age_group_to'].value(), last)
                for name, label in [('age_group_from', 'Youngest'), ('age_group_to', 'Oldest')]:
                    self.assertEqual(form.fields[name].label, label)
                    self.assertEqual(len(form.fields[name].choices), count)
                    self.assertIn('<select', str(form[name]))
                html = render_to_string('popcase/wizard/filters.html', {'form': form, 'current_step': 'filters'})
                self.assertNotIn('name="age_groups"', html)
                posted = self.form({'age_group_from': first, 'age_group_to': last}, level)
                self.assertTrue(posted.is_valid(), posted.errors)
                self.assertEqual(posted.cleaned_data['age_groups'], [])
                self.assertEqual(selected_age_bands(level, posted.cleaned_data), age_bands(level))

    def test_same_band_open_ended_and_reversed_ranges(self):
        for level, token, expected in [('county', 'age_00', (0, 0)),
                                        ('tract', 'age_90_plus', (90, None)),
                                        ('place', 'age_85_plus', (85, None)),
                                        ('zcta', 'age_00_04', (0, 4))]:
            form = self.form({'age_group_from': token, 'age_group_to': token}, level)
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(selected_age_bands(level, form.cleaned_data), (expected,))
        form = self.form({'age_group_from': 'age_60_64', 'age_group_to': 'age_20_24'})
        self.assertFalse(form.is_valid())
        self.assertIn('age_group_to', form.errors)
        self.assertFalse(self.form({'age_group_to': 'invalid'}).is_valid())

    def test_range_drives_case_query_and_population_bands(self):
        form = self.form({'age_group_from': 'age_20_24', 'age_group_to': 'age_30_34'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(selected_age_bands('county', form.cleaned_data), ((20, 24), (25, 29), (30, 34)))
        raw = sqlite3.connect(':memory:')
        self.addCleanup(raw.close)
        db = DatabaseWrapper({'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:',
                              'OPTIONS': {}, 'TIME_ZONE': None}, alias='age_fixture')
        db.connection = raw
        columns = [f.column for f in NaaccrData._meta.local_fields]
        raw.execute('CREATE TABLE naaccr_data (' + ','.join('"' + c + '" TEXT' for c in columns) + ')')
        for age in ('019', '020', '024', '025', '029', '030', '034', '035', '999', None):
            raw.execute('INSERT INTO naaccr_data ("Age at Diagnosis", "Behavior Code ICD-O-3") VALUES (?, ?)', (age, '3'))
        qs = apply_naaccr_filters(NaaccrData.objects.all(), form.cleaned_data).values_list('age_at_dx', flat=True)
        sql, params = qs.query.get_compiler(connection=db).as_sql()
        actual = [row[0] for row in raw.execute(sql.replace('%s', '?'), params)]
        self.assertEqual(actual, ['020', '024', '025', '029', '030', '034'])

    def test_saved_filters_restore_and_adapt_after_geography_changes(self):
        form = self.form(initial={'age_groups': ['age_20_24', 'age_35_39']})
        self.assertEqual(form['age_group_from'].value(), 'age_20_24')
        self.assertEqual(form['age_group_to'].value(), 'age_35_39')
        self.assertTrue(form.age_range_notice)
        for level, initial, expected in [
            ('place', {'age_group_from': 'age_00', 'age_group_to': 'age_90_plus'}, ('age_00_04', 'age_85_plus')),
            ('county', {'age_group_from': 'age_00_04', 'age_group_to': 'age_85_plus'}, ('age_00', 'age_90_plus')),
            ('tract', {'age_group_from': 'age_20_24', 'age_group_to': 'age_30_34'}, ('age_20_24', 'age_30_34'))]:
            form = self.form(level=level, initial=initial)
            self.assertEqual((form['age_group_from'].value(), form['age_group_to'].value()), expected)

    def test_dropdowns_override_stale_checkbox_data(self):
        form = self.form({'age_group_from': 'age_20_24', 'age_group_to': 'age_20_24',
                          'age_groups': ['age_90_plus']})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['age_groups'], ['age_20_24'])
