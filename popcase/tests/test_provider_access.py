from unittest.mock import patch

from django.db import DatabaseError
from django.test import SimpleTestCase, override_settings

from popcase.provider_access import get_pcp_tract_lookup


@override_settings(POPCASE_PCP_TRACT_SOURCE_FILE="approved-file.xlsx")
class PCPTractTests(SimpleTestCase):
    def lookup(self, rows):
        with patch("popcase.provider_access.TravelTimeTract.objects") as manager:
            query = manager.using.return_value
            query.filter.return_value.values.return_value.iterator.return_value = iter(rows)
            result = get_pcp_tract_lookup()
            query.filter.assert_called_once_with(source_file="approved-file.xlsx")
            return result

    def test_approved_source_is_filtered_and_scaled_without_capping_at_100(self):
        self.assertEqual(self.lookup([
            {"tract_geoid": "39001000100", "count_x": .0015},
            {"tract_geoid": "39001000200", "count_x": 0},
        ]), {"39001000100": 150, "39001000200": 0})

    @override_settings(POPCASE_PCP_TRACT_SOURCE_FILE="")
    def test_unconfigured_source_never_reads_all_specialties(self):
        with patch("popcase.provider_access.TravelTimeTract.objects") as manager:
            with self.assertLogs("popcase.provider_access", level="WARNING"):
                self.assertEqual(get_pcp_tract_lookup(), {})
            manager.using.assert_not_called()

    def test_missing_invalid_and_nonfinite_values_are_not_zero(self):
        rows = [{"tract_geoid": str(i), "count_x": value}
                for i, value in enumerate([None, "", "bad", -1, "NaN", "inf", 1e308])]
        self.assertEqual(self.lookup(rows), {})

    def test_duplicate_tract_is_omitted_regardless_of_row_order(self):
        rows = [{"tract_geoid": "39001000100", "count_x": v}
                for v in [.001, .002, .003]]
        for ordered in [rows, list(reversed(rows))]:
            with self.assertLogs("popcase.provider_access", level="WARNING"):
                self.assertEqual(self.lookup(ordered), {})

    def test_query_failure_does_not_return_partial_results(self):
        def failing_rows():
            yield {"tract_geoid": "39001000100", "count_x": .001}
            raise DatabaseError("unavailable")
        with self.assertLogs("popcase.provider_access", level="ERROR"):
            self.assertEqual(self.lookup(failing_rows()), {})

    def test_unknown_source_has_no_fallback(self):
        with self.assertLogs("popcase.provider_access", level="WARNING"):
            self.assertEqual(self.lookup([]), {})

    @override_settings(POPCASE_PCP_TRACT_SOURCE_FILE=None)
    @patch.dict("os.environ", {}, clear=True)
    def test_default_matches_current_sdd_and_uses_count_x(self):
        with patch("popcase.provider_access.TravelTimeTract.objects") as manager:
            query = manager.using.return_value
            query.filter.return_value.values.return_value.iterator.return_value = iter([
                {"tract_geoid": "39001000100", "count_x": .002, "weighted_sa_final": .9}
            ])
            self.assertEqual(get_pcp_tract_lookup(), {"39001000100": 200})
            query.filter.assert_called_once_with(source_file="pcp1_1220_tr.xlsx.xlsx")
            query.filter.return_value.values.assert_called_once_with("tract_geoid", "count_x")
