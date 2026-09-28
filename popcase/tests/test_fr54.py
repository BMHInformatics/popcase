"""FR54 behavior eligibility, evaluated using a local in-memory SQL fixture."""
import re
import sqlite3

from django.db.backends.sqlite3.base import DatabaseWrapper
from django.test import SimpleTestCase

from popcase.models import NaaccrData
from popcase.services import apply_naaccr_filters, load_cancer_logic


class BehaviorFilterTests(SimpleTestCase):
    def setUp(self):
        self.db = DatabaseWrapper({"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:",
                                   "OPTIONS": {}, "TIME_ZONE": None}, alias="risk_fixture")
        self.raw = sqlite3.connect(":memory:")
        self.raw.create_function("regexp", 2, lambda pattern, value: bool(value is not None and re.search(pattern, str(value))))
        self.db.connection = self.raw
        fields = NaaccrData._meta.local_fields
        self.columns = [f.column for f in fields]
        self.raw.execute('CREATE TABLE naaccr_data (' + ', '.join('"'+c+'" TEXT' for c in self.columns) + ')')

    def tearDown(self):
        self.raw.close()

    def add(self, mid, site="C509", hist="8140", sex="2", age="051", behavior="3"):
        values = {f.column: None for f in NaaccrData._meta.local_fields}
        for key, value in dict(mid=mid, primary_site=site, hist_o3=hist, sex=sex,
                               age_at_dx=age, behavior=behavior).items():
            values[NaaccrData._meta.get_field(key).column] = value
        self.raw.execute("INSERT INTO naaccr_data VALUES ("+",".join("?" for _ in self.columns)+")",
                         [values[c] for c in self.columns])

    def ids(self, filters):
        qs = apply_naaccr_filters(NaaccrData.objects.all(), filters).values_list("mid", flat=True)
        sql, params = qs.query.get_compiler(connection=self.db).as_sql()
        return [r[0] for r in self.raw.execute(sql.replace("%s", "?"), params)]

    def test_fr54_bladder_exception_histology_and_site_boundaries(self):
        allowed_histologies = ("8120", "9049", "9056", "9139", "9141", "9589", "9993")
        excluded_histologies = ("9050", "9055", "9140", "9590", "9992", None, "")
        expected = set()
        for site in ("C669", "C670", "C679", "C680", None, ""):
            for hist in allowed_histologies + excluded_histologies:
                for behavior in ("0", "1", "2", "3", "6", "9", None, ""):
                    mid = f"{site!r}:{hist!r}:{behavior!r}"
                    self.add(mid, site=site, hist=hist, behavior=behavior)
                    if behavior == "3" or (behavior == "2" and site in ("C670", "C679")
                                            and hist in allowed_histologies):
                        expected.add(mid)
        for filters in ({}, {"sex": "female"}):
            with self.subTest(filters=filters):
                self.assertEqual(set(self.ids(filters)), expected)

    def test_fr54_exception_with_bladder_and_overlapping_risk_selections(self):
        bladder = next(k for k, v in load_cancer_logic()[1].items()
                       if v.get("Site_sub") == "Urinary Bladder")
        self.add("bladder_is", site="C670", hist="8120", behavior="2")
        self.add("bladder_malignant", site="C679", hist="8120", behavior="3")
        self.add("lymphoma_is", site="C679", hist="9590", behavior="2")
        self.add("breast_is", behavior="2")
        for selection in ([bladder], ["risk:tobacco"], [bladder, "risk:tobacco"]):
            with self.subTest(selection=selection):
                self.assertEqual(sorted(self.ids({"cancer_types": selection})),
                                 ["bladder_is", "bladder_malignant"])
