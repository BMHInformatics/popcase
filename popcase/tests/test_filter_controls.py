from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.template.loader import render_to_string

from popcase.forms import FiltersForm
from popcase import services
from popcase.rate_statistics import selected_age_bands
from popcase.views import _serialize_payload, _deserialize_payload


class FilterControlTests(SimpleTestCase):
    def make_form(self, data=None, geo="county", initial=None):
        if data is not None:
            data = {"sex": "all", **data}
        with patch("popcase.forms.get_diagnosis_quarter_choices", return_value=(("2010q1", "2010q1"), ("2024q4", "2024q4"))), patch("popcase.forms.get_default_diagnosis_quarter_range", return_value=("2010q1", "2024q4")):
            return FiltersForm(data=data, initial=initial, geographic_level=geo)

    def test_age_dropdowns_keep_geography_categories_and_include_intermediate_groups(self):
        for geo, count in (("county", 20), ("tract", 20), ("place", 18), ("zcta", 18)):
            selected = ["age_20_24", "age_35_39"]
            form = self.make_form({"age_group_from": selected[0], "age_group_to": selected[1]}, geo)
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(len(form.fields["age_group_from"].choices), count)
            self.assertEqual(len(form.fields["age_group_to"].choices), count)
            self.assertEqual(form.cleaned_data["age_groups"], ['age_20_24', 'age_25_29', 'age_30_34', 'age_35_39'])
            self.assertEqual(selected_age_bands(geo, form.cleaned_data), ((20, 24), (25, 29), (30, 34), (35, 39)))
        self.assertFalse(self.make_form({"age_group_from": "age_00"}, "place").is_valid())

    def test_multiple_counties_validate_scope_and_cache_round_trip(self):
        form = self.make_form({"geography": "counties", "counties": ["39035", "39153"]})
        self.assertTrue(form.is_valid(), form.errors)
        filters = _deserialize_payload(_serialize_payload(form.cleaned_data))
        self.assertTrue(services._geoid_in_scope("county", "39035", filters))
        self.assertTrue(services._geoid_in_scope("tract", "39153000100", filters))
        self.assertFalse(services._geoid_in_scope("county", "39001", filters))
        self.assertFalse(self.make_form({"geography": "counties"}).is_valid())
        self.assertFalse(self.make_form({"geography": "counties", "counties": ["99999"]}).is_valid())
        with patch("popcase.forms.county_assignments", return_value={"3916000": "39035"}):
            self.assertTrue(self.make_form({"geography": "counties", "counties": ["39035"]}, "place").is_valid())

    def test_case_query_uses_selected_counties(self):
        qs = MagicMock()
        qs.filter.return_value = qs
        with patch.object(services, "NaaccrPatientCensusLinking") as linking:
            services.apply_naaccr_filters(qs, {"geography": "counties", "counties": ["39035", "39153"]})
            linking.objects.filter.assert_called_once_with(geographic_level="county", geoid__in={"39035", "39153"})
            self.assertIn("mid__in", qs.filter.call_args.kwargs)

    def test_quarter_minimum_and_labels(self):
        services.get_diagnosis_quarter_choices.cache_clear()
        cursor = MagicMock()
        cursor.fetchone.return_value = ("19500701", "20241231")
        with patch.object(services, "connection") as database:
            database.cursor.return_value.__enter__.return_value = cursor
            choices = services.get_diagnosis_quarter_choices()
        services.get_diagnosis_quarter_choices.cache_clear()
        self.assertEqual(choices[0][0], "2010q1")
        self.assertEqual(choices[-1][0], "2024q4")
        self.assertFalse(self.make_form({"dx_start": "2009q4"}).is_valid())
        form = self.make_form()
        self.assertEqual(form["dx_start"].label, "From (quarter)")
        self.assertEqual(form["dx_end"].label, "To (quarter)")

    def test_controls_render_without_submitted_all_race_token(self):
        html = render_to_string("popcase/wizard/filters.html", {"form": self.make_form(), "current_step": "filters"})
        self.assertIn('id="race_all"', html)
        self.assertIn('name="age_group_from"', html)
        self.assertIn('name="age_group_to"', html)
        self.assertNotIn('name="age_groups"', html)
        self.assertNotIn("Leave blank for All.", html)
        self.assertIn("Includes both selected age groups", html)
        self.assertIn("Distant", html)
        self.assertNotIn('value="all" name="race_ethnicity"', html)
