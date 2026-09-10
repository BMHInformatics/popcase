from django import forms
from .models import UICounty
from .services import OHIO_COUNTY_NAMES, get_default_diagnosis_quarter_range, get_diagnosis_quarter_choices, diagnosis_quarter_sort_key

GEO_CHOICES = [
    ("none", "Do not compare locations"),
    ("county", "Compare counties"),
    ("place", "Compare census Designated Places / Municipalities"),
    ("zcta", "Compare Zip Code Tabulation Areas (ZCTAs)"),
    ("tract", "Compare census Tracts"),
    ("patient", "Patient-level (Administrator)"),
]

SEX_CHOICES = [
    ("all", "All"),
    ("female", "Female"),
    ("male", "Male"),
]

BRIDGED_RACE_CHOICES = [
    ("nh_white", "Non-Hispanic White"),
    ("nh_black", "Non-Hispanic Black"),
    ("nh_aian", "Non-Hispanic AI/AN"),
    ("nh_api", "Non-Hispanic API"),
    ("hisp_any", "Hispanic (any race)"),
]

ZCTA_PLACE_RACE_CHOICES = [
    ("nh_white", "Non-Hispanic White alone"),
    ("nh_black", "Non-Hispanic Black alone"),
    ("nh_aian", "Non-Hispanic AI/AN alone"),
    ("nh_api", "Non-Hispanic API alone"),
    ("nh_other", "Other Non-Hispanic"),
    ("hisp_any", "Hispanic (any race(s))"),
]

# Retain the original name for imports outside this module. The default/no-
# comparison, county, and tract views use the bridged categories.
RACE_CHOICES = BRIDGED_RACE_CHOICES

STAGE_CHOICES = [
    ("in_situ", "In situ"),
    ("localized", "Localized"),
    ("regional", "Regional"),
    ("metastatic", "Distant"),
    ("unknown", "Stage Unknown"),
]

CANCER_TYPE_CHOICES = [
    ("breast", "Breast"),
    ("lung", "Lung"),
    ("colorectal", "Colorectal"),
    ("prostate", "Prostate"),
    ("cervix", "Cervix"),
    ("melanoma", "Melanoma"),
    ("other", "Other / Specify later"),
]

# Measures (subset for UI scaffold; expand as needed)
MEASURE_DISEASE_CHOICES = [
    ("case_count", "Case count"),
    ("pct_advanced", "% Advanced at diagnosis (Regional or metastatic spread)"),
    ("pct_metastatic", "% Metastatic at diagnosis"),
    ("median_tti", "Median time to treatment"),
    ("inc_rate", "Age-adjusted incidence rate (per 100,000)"),
    ("inc_ci", "95% Confidence Interval (incidence)"),
    ("mort_rate", "Age-adjusted mortality rate (per 100,000)"),
    ("mort_ci", "95% Confidence Interval (mortality)"),
    ("gleason", "Gleason Score (Prostate cancer only)"),
]

MEASURE_ACCESS_PATIENT_CHOICES = [
    ("pcp", "Primary care providers"),
    ("onc", "Oncology providers"),
    ("ext_care", "Extended cancer care providers"),
    ("mammo_fac", "Mammogram facilities"),
    ("coc", "CoC-accredited Academic Comprehensive Cancer Programs (ACAD)"),
    ("nci", "NCI-designated cancer centers"),
    ("tt_adj_density", "Travel time-adjusted provider/facility density per 100,000 population (from centroid of patient's census block group)"),
    ("tt_nearest", "Travel time to nearest facility (from centroid of patient's census block group)"),
]

MEASURE_COMMUNITY_CHOICES = [
    ("pop_total", "Total population"),
    ("sex_dist", "Sex distribution"),
    ("median_age", "Median age (years)"),
    ("race_eth", "Race/Ethnicity"),
    ("med_hh_income", "Median household income ($)"),
    ("poverty_pct", "% of households below poverty level"),
    ("snap_pct", "% of households receiving Food stamps/SNAP"),
    ("gini", "GINI Index"),
    ("redlined_pct", "% of population living in formerly redlined neighborhoods"),
    ("smoking", "Current cigarette smoking (Adults only)"),
    ("obesity", "Obesity (Adults only)"),
    ("no_leisure_pa", "No leisure-time physical activity (Adults only)"),
    ("crc_screen", "Colorectal cancer screening (age 45-75)"),
    ("breast_screen", "Breast cancer screening (age 50-74)"),
    ("cervical_screen", "Cervical cancer screening"),
    ("routine_checkup", "% visited doctor for routine checkup within past year (Adults)"),
    ("no_transport", "% lack reliable transportation in past 12 months (Adults)"),
    ("no_insurance", "% uninsured age 18-64 (Adults)"),
    ("adi", "Social Vulnerability Index / ADI (all subcomponents)"),
]

STRAT_VAR_CHOICES = [
    ("sex", "Sex"),
    ("race_eth", "Race/Ethnicity"),
    ("age_broad", "Age - Broad Categories (0-49, 50-64, 65-84, 85+)"),
    ("age_narrow", "Age - Narrow Categories"),
    ("hpsa", "Living in Health Professional Shortage Area (HPSA)"),
    ("redlined", "Living in Formerly Redlined Neighborhood"),
    ("metro", "Metro vs. Non-metro"),
    ("insurance", "Patient Insurance Status at Diagnosis"),
    ("site", "Cancer Site"),
    ("stage", "Cancer Stage"),
    ("receptor3", "Receptor Status (3 categories: HER2+ combined regardless of ER status)"),
    ("receptor4", "Receptor Status (4 categories)"),
]

# Geography scope choices (Filters step)
GEOGRAPHY_SCOPE_CHOICES = [
    ("all_ohio", "All Ohio counties (88)"),
    ("neo15", "Northeast Ohio catchment area (15 counties)"),
]

# Diagnosis quarter choices are populated dynamically in FiltersForm.__init__.
DX_QUARTER_FALLBACK_CHOICES = [(f"{year}q{quarter}", f"{year}q{quarter}") for year in range(2010, 2023) for quarter in range(1, 5)]


class GeographicLevelForm(forms.Form):
    geographic_level = forms.ChoiceField(
        choices=GEO_CHOICES,
        widget=forms.RadioSelect,
        initial="none",
        label="Choose a geographic level"
    )


SEER_20_AGE_GROUP_CHOICES = [
    ("age_00", "00 years"),
    ("age_01_04", "01-04 years"),
    ("age_05_09", "05-09 years"),
    ("age_10_14", "10-14 years"),
    ("age_15_19", "15-19 years"),
    ("age_20_24", "20-24 years"),
    ("age_25_29", "25-29 years"),
    ("age_30_34", "30-34 years"),
    ("age_35_39", "35-39 years"),
    ("age_40_44", "40-44 years"),
    ("age_45_49", "45-49 years"),
    ("age_50_54", "50-54 years"),
    ("age_55_59", "55-59 years"),
    ("age_60_64", "60-64 years"),
    ("age_65_69", "65-69 years"),
    ("age_70_74", "70-74 years"),
    ("age_75_79", "75-79 years"),
    ("age_80_84", "80-84 years"),
    ("age_85_89", "85-89 years"),
    ("age_90_plus", "90+ years"),
]

ZCTA_PLACE_AGE_GROUP_CHOICES = [
    ("age_00_04", "0-4 years"),
    ("age_05_09", "05-09 years"),
    ("age_10_14", "10-14 years"),
    ("age_15_19", "15-19 years"),
    ("age_20_24", "20-24 years"),
    ("age_25_29", "25-29 years"),
    ("age_30_34", "30-34 years"),
    ("age_35_39", "35-39 years"),
    ("age_40_44", "40-44 years"),
    ("age_45_49", "45-49 years"),
    ("age_50_54", "50-54 years"),
    ("age_55_59", "55-59 years"),
    ("age_60_64", "60-64 years"),
    ("age_65_69", "65-69 years"),
    ("age_70_74", "70-74 years"),
    ("age_75_79", "75-79 years"),
    ("age_80_84", "80-84 years"),
    ("age_85_plus", "85+ years"),
]

class FiltersForm(forms.Form):
    counties = forms.MultipleChoiceField(required=False, label="Counties", widget=forms.CheckboxSelectMultiple)
    sex = forms.ChoiceField(choices=SEX_CHOICES, widget=forms.RadioSelect, initial="all", label="Sex")
    age_groups = forms.MultipleChoiceField(
        choices=SEER_20_AGE_GROUP_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Age (at diagnosis) groups",
    )
    race_ethnicity = forms.MultipleChoiceField(
        choices=RACE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Race/Ethnicity"
    )

    geography = forms.ChoiceField(
        choices=GEOGRAPHY_SCOPE_CHOICES,
        required=False,
        initial="all_ohio",
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Geography"
    )

    @classmethod
    def get_age_group_choices_for_geography(cls, geographic_level):
        return ZCTA_PLACE_AGE_GROUP_CHOICES if geographic_level in {"zcta", "place"} else SEER_20_AGE_GROUP_CHOICES

    @classmethod
    def get_race_choices_for_geography(cls, geographic_level):
        return ZCTA_PLACE_RACE_CHOICES if geographic_level in {"zcta", "place"} else BRIDGED_RACE_CHOICES

    def __init__(self, *args, **kwargs):
        geographic_level = kwargs.pop("geographic_level", "none")
        super().__init__(*args, **kwargs)
        self.geographic_level = geographic_level
        self.fields["age_groups"].choices = self.get_age_group_choices_for_geography(geographic_level)
        self.fields["race_ethnicity"].choices = self.get_race_choices_for_geography(geographic_level)

        diagnosis_quarter_choices = get_diagnosis_quarter_choices() or tuple(DX_QUARTER_FALLBACK_CHOICES)
        default_dx_start, default_dx_end = get_default_diagnosis_quarter_range()
        self.fields["dx_start"].choices = diagnosis_quarter_choices
        self.fields["dx_end"].choices = diagnosis_quarter_choices
        self.fields["dx_start"].initial = default_dx_start
        self.fields["dx_end"].initial = default_dx_end
        if not self.is_bound:
            self.initial.setdefault("dx_start", default_dx_start)
            self.initial.setdefault("dx_end", default_dx_end)

        try:
            county_choices = [
                (f"county:{row.geoid}", row.name)
                for row in UICounty.objects.order_by("name")
            ]
        except Exception:
            county_choices = [
                (f"county:{geoid}", f"{name} County")
                for geoid, name in sorted(OHIO_COUNTY_NAMES.items(), key=lambda item: item[1])
            ]

        self.fields["geography"].choices = (
            GEOGRAPHY_SCOPE_CHOICES
            + [("counties", "Selected counties")]
        )
        self.fields["counties"].choices = [(value.split(":", 1)[1], label) for value, label in county_choices]
        # Preserve saved selections from the former single-county dropdown.
        scope_key = self.add_prefix("geography")
        scope = self.data.get(scope_key) if self.is_bound else self.initial.get("geography")
        if isinstance(scope, str) and scope.startswith("county:"):
            county = scope.split(":", 1)[1]
            if self.is_bound:
                self.data = self.data.copy()
                self.data[scope_key] = "counties"
                counties_key = self.add_prefix("counties")
                if hasattr(self.data, "setlist"):
                    self.data.setlist(counties_key, [county])
                else:
                    self.data[counties_key] = [county]
            else:
                self.initial.update(geography="counties", counties=[county])

    dx_start = forms.ChoiceField(
        choices=DX_QUARTER_FALLBACK_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        label="From (quarter)"
    )

    dx_end = forms.ChoiceField(
        choices=DX_QUARTER_FALLBACK_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        label="To (quarter)"
    )

    cancer_types = forms.MultipleChoiceField(
        choices=[],
        required=False,
        label="Cancer Type(s)",
    )

    stage = forms.MultipleChoiceField(
        choices=STAGE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        initial=["localized", "regional", "metastatic", "unknown"],
        label="Stage"
    )

    exclude_multiple_primaries = forms.BooleanField(required=False, label="Exclude patients with multiple primary cancers")

    # def __init__(self, *args, **kwargs):
    #     super().__init__(*args, **kwargs)
    #
        # sites = (
        #     NaaccrData.objects
        #     .exclude(primary_site__isnull=True)
        #     .exclude(primary_site="")
        #     .values_list("primary_site", flat=True)
        #     .distinct()
        #     .order_by("primary_site")
        # )
        #
        # # choices: (value, label)
        # self.fields["cancer_types"].choices = [(s, s) for s in sites]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("geography") == "counties" and not cleaned.get("counties"):
            self.add_error("counties", "Select at least one county.")
        if self.geographic_level in {"place", "zcta"} and cleaned.get("geography") not in ("", None, "all_ohio"):
            self.add_error("geography", "County restrictions require a Place/ZCTA crosswalk. Select all Ohio or compare counties/tracts.")
        s = cleaned.get("dx_start")
        e = cleaned.get("dx_end")
        if s and e:
            start_key = diagnosis_quarter_sort_key(s)
            end_key = diagnosis_quarter_sort_key(e)
            if start_key and end_key and start_key > end_key:
                self.add_error("dx_end", "Diagnosis quarter 'to' must be >= diagnosis quarter 'from'.")
        return cleaned

class MeasuresForm(forms.Form):
    # Leaf options only; categories are rendered in the template (not selectable).

    DISEASE_LEAVES = [
        ("case_count", "Case count"),

        ("pct_advanced", "% Advanced at diagnosis"),
        ("pct_advanced_ci", "95% Confidence Interval (% Advanced)"),

        ("pct_metastatic", "% Metastatic at diagnosis"),
        ("pct_metastatic_ci", "95% Confidence Interval (% Metastatic)"),

        ("median_tti", "Median time to treatment"),
        ("median_tti_iqr", "Interquartile Range (IQR)"),

        ("crude_inc_rate", "Crude incidence rate (per 100,000)"),
        ("crude_inc_ci", "95% Confidence Interval (crude incidence)"),

        ("crude_mort_rate", "Crude mortality rate (per 100,000)"),
        ("crude_mort_ci", "95% Confidence Interval (crude mortality)"),

        ("inc_rate", "Age-adjusted incidence rate (per 100,000)"),
        ("inc_ci", "95% Confidence Interval (incidence)"),

        ("mort_rate", "Age-adjusted mortality rate (per 100,000)"),
        ("mort_ci", "95% Confidence Interval (mortality)"),

        ("gleason", "Mean Gleason Score (Prostate cancer only)"),
        ("gleason_ci", "95% Confidence Interval (Mean Gleason Score)"),
    ]

    ACCESS_PATIENT_LEAVES = [
        ("pcp", "Primary care providers"),
        ("onc", "Oncology providers"),
        ("ext_care", "Extended cancer care providers"),
        ("mammo_fac", "Mammogram facilities"),
        ("coc", "CoC-accredited Academic Comprehensive Cancer Programs (ACAD)"),
        ("nci", "NCI-designated cancer centers"),
    ]

    CANCER_PREVENTION_LEAVES = [
        ("smoking", "Current cigarette smoking (% of adults)"),
        ("obesity", "Obesity (% of adults)"),
        ("binge_drinking", "Binge drinking (% of adults)"),
        ("no_leisure_pa", "No leisure-time physical activity (% of adults)"),
        ("short_sleep", "Short sleep duration (% of adults)"),
        ("crc_screen", "% Colorectal cancer screening (age 45-75)"),
        ("breast_screen", "% Breast cancer screening (age 50-74)"),
        ("cervical_screen", "% Cervical cancer screening (age 21-65)"),
    ]

    HEALTH_STATUS_LEAVES = [
        ("poor_health", "Fair or poor self-rated health status (% of adults)"),
        ("phys_distress", "Frequent physical distress (% of adults)"),
        ("mental_distress", "Frequent mental distress (% of adults)"),
        ("food_insecurity", "Food insecurity in the past 12 months (% of adults)"),
        ("social_isolation", "Feeling socially isolated (% of adults)"),
        ("any_disability", "Any disability (% of adults)"),
        ("mobility_disability", "Mobility disability (% of adults)"),
        ("selfcare_disability", "Self-care disability (% of adults)"),
        ("independent_living_disability", "Independent living disability (% of adults)"),
    ]

    SURVEY_ACCESS_LEAVES = [
        ("routine_checkup", "Visits to doctor for routine checkup in past year (% of adults)"),
        ("no_transport", "Lack of reliable transportation in past 12 months (% of adults)"),
        ("no_insurance", "Current lack of health insurance (% of adults age 18-64)"),
        ("dentist", "Visited dentist or dental clinic in past year (% of adults)"),
    ]

    # Community characteristics (ACS-style) leaf options
    COMMUNITY_BASIC_LEAVES = [
        ("pop_total", "Total population"),
        ("sex_dist", "Sex distribution"),
        ("median_age", "Median age (years)"),
        ("race_eth", "Race/Ethnicity"),
    ]
    COMMUNITY_EXT_LEAVES = [
        ("age_dist", "Age distribution (0–14, 15–39, 40–49, 50–64, 65–79, 80+)"),
        ("marital_status", "Marital status (age 15+)"),
        ("educ_attain", "Educational attainment (age 25+)"),
        ("lang_home", "Distribution of language spoken at home"),
        ("limited_english", "% of residents >= age 5 who speak English less than very well"),
        ("citizenship", "Citizenship status (age 1+)"),
        ("rurality", "Rurality (RUCC / RUCA code)"),
    ]
    COMMUNITY_ECON_LEAVES = [
        ("med_hh_income", "Median household income ($)"),
        ("per_capita_income", "Per capita income ($)"),
        ("poverty_pct", "% of households below poverty level"),
        ("income_pov_ratio", "Family income-to-poverty ratio distribution"),
        ("snap_pct", "% of households receiving Food stamps/SNAP"),
        ("employment_16plus", "Employment status for population >=16 years"),
        ("utility_shutoff_threat", "Utility services shut-off threat in the past 12 months among adults"),
        ("housing_insecurity", "Housing insecurity in the past 12 months among adults"),
        ("occupation_dist", "Occupational category distribution"),
        ("gini", "GINI Index"),
        ("redlined_pct", "% of population living in formerly redlined neighborhoods"),
        ("svi_adi", "Social Vulnerability Index / ADI"),
    ]
    COMMUNITY_HOUSING_LEAVES = [
        ("housing_unoccupied", "Vacant housing units (%)"),
        ("renting_pct", "Renting (%)"),
        ("occupants_per_room", "% of occupied housing units with >1 occupant per room"),
        ("median_year_built", "Median year built"),
        ("median_home_value", "Median value of owner-occupied housing units ($)"),
        ("median_housing_costs", "Median monthly housing costs ($)"),
        ("plumbing_complete", "Occupied housing units with complete plumbing (%)"),
        ("kitchen_complete", "Occupied housing units with complete kitchen facilities (%)"),
    ]
    COMMUNITY_HHCHAR_LEAVES = [
        ("female_headed", "% of households with female householder, no spouse present"),
        ("grandparents_care", "% of households with grandparents caring for children"),
        ("internet_access", "Household internet access by type (categories may overlap)"),
        ("moved_last_year", "Moved in past year (% of population age 1+)"),
    ]

    COMMUNITY_TIMEFRAME_CHOICES = [
        ("most_recent", "Most recent available"),
        ("historical", "Historical (to align with date range in cancer case query specified in Filters tab)"),
    ]

    disease_measures = forms.MultipleChoiceField(
        choices=DISEASE_LEAVES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        initial=["case_count"],
        label="Disease-focused"
    )

    access_patient_measures = forms.MultipleChoiceField(
        choices=ACCESS_PATIENT_LEAVES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Access to Care for Cancer Patients"
    )

    cancer_prevention = forms.MultipleChoiceField(
        choices=CANCER_PREVENTION_LEAVES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Cancer Prevention"
    )

    noncancer_health_status = forms.MultipleChoiceField(
        choices=HEALTH_STATUS_LEAVES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Non-cancer community Health Status Measures (Adults only)"
    )

    access_comm_tract = forms.MultipleChoiceField(
        choices=ACCESS_PATIENT_LEAVES + SURVEY_ACCESS_LEAVES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Access to care for communities (Census Tract)"
    )

    access_comm_zcta_place = forms.MultipleChoiceField(
        choices=SURVEY_ACCESS_LEAVES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Access to care for communities (ZCTA / Place)"
    )

    access_comm_county = forms.MultipleChoiceField(
        choices=ACCESS_PATIENT_LEAVES + SURVEY_ACCESS_LEAVES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Access to care for communities (County)"
    )


    community_timeframes = forms.MultipleChoiceField(
        choices=COMMUNITY_TIMEFRAME_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        initial=["most_recent"],
        label="Select timeframe for community characteristics",
    )

    community_characteristics = forms.MultipleChoiceField(
        choices=(
            COMMUNITY_BASIC_LEAVES
            + COMMUNITY_EXT_LEAVES
            + COMMUNITY_ECON_LEAVES
            + COMMUNITY_HOUSING_LEAVES
            + COMMUNITY_HHCHAR_LEAVES
        ),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Community Characteristics"
    )

    # Geo-stratified display options from the Measures-page wireframe.
    # These are intentionally separate BooleanFields, rather than adding more
    # leaf measures, because they control whether companion CI / age-adjusted
    # columns should be displayed for a selected subsection.
    ci_checkbox_widget = forms.CheckboxInput(attrs={"class": "form-check-input"})

    cancer_risk_factors_ci = forms.BooleanField(
        required=False,
        label="Display 95% Confidence Intervals",
        widget=ci_checkbox_widget,
    )
    cancer_screening_ci = forms.BooleanField(
        required=False,
        label="Display 95% Confidence Intervals",
        widget=ci_checkbox_widget,
    )
    noncancer_health_status_ci = forms.BooleanField(
        required=False,
        label="Display 95% Confidence Intervals",
        widget=ci_checkbox_widget,
    )

    access_comm_tract_survey_ci = forms.BooleanField(
        required=False,
        label="Display 95% Confidence Intervals",
        widget=ci_checkbox_widget,
    )
    access_comm_zcta_place_survey_ci = forms.BooleanField(
        required=False,
        label="Display 95% Confidence Intervals",
        widget=ci_checkbox_widget,
    )
    access_comm_place_survey_age_adjusted = forms.BooleanField(
        required=False,
        label="Display age-adjusted measures",
        widget=ci_checkbox_widget,
    )
    access_comm_county_survey_ci = forms.BooleanField(
        required=False,
        label="Display 95% Confidence Intervals",
        widget=ci_checkbox_widget,
    )
    access_comm_county_survey_age_adjusted = forms.BooleanField(
        required=False,
        label="Display age-adjusted measures",
        widget=ci_checkbox_widget,
    )

    community_basic_ci = forms.BooleanField(
        required=False,
        label="Display 95% Confidence Intervals",
        widget=ci_checkbox_widget,
    )
    community_extended_ci = forms.BooleanField(
        required=False,
        label="Display 95% Confidence Intervals",
        widget=ci_checkbox_widget,
    )
    community_economic_ci = forms.BooleanField(
        required=False,
        label="Display 95% Confidence Intervals",
        widget=ci_checkbox_widget,
    )
    community_housing_ci = forms.BooleanField(
        required=False,
        label="Display 95% Confidence Intervals",
        widget=ci_checkbox_widget,
    )
    community_household_ci = forms.BooleanField(
        required=False,
        label="Display 95% Confidence Intervals",
        widget=ci_checkbox_widget,
    )


    MEASURE_SELECTION_FIELDS = (
        "disease_measures",
        "access_patient_measures",
        "cancer_prevention",
        "noncancer_health_status",
        "access_comm_tract",
        "access_comm_zcta_place",
        "access_comm_county",
        "community_characteristics",
    )

    def __init__(self, *args, **kwargs):
        geographic_level = kwargs.pop("geographic_level", None)
        super().__init__(*args, **kwargs)

        # FR41 availability matrix:
        #   County -> show county age-adjusted option
        #   Place  -> show place age-adjusted option
        #   Tract/ZCTA -> no age-adjusted option
        allowed_age_adjusted_field = {
            "county": "access_comm_county_survey_age_adjusted",
            "place": "access_comm_place_survey_age_adjusted",
        }.get(geographic_level)
        for field_name in (
            "access_comm_county_survey_age_adjusted",
            "access_comm_place_survey_age_adjusted",
        ):
            if field_name != allowed_age_adjusted_field:
                self.fields.pop(field_name, None)

    def clean(self):
        cleaned = super().clean()
        # FR16: Historic Redlining is deferred to a later version. Ignore a
        # stale session or crafted POST value in addition to disabling it in
        # the Measures UI.
        cleaned["community_characteristics"] = [
            token
            for token in cleaned.get("community_characteristics", [])
            if token != "redlined_pct"
        ]
        has_measure = any(cleaned.get(field) for field in self.MEASURE_SELECTION_FIELDS)
        if not has_measure:
            raise forms.ValidationError("One or more measures must be chosen in order to proceed.")
        return cleaned

class StratificationForm(forms.Form):
    row_variable = forms.ChoiceField(choices=[("", "None")] + STRAT_VAR_CHOICES, required=False, initial="", label="Row")
    col_variable = forms.ChoiceField(choices=[("", "None")] + STRAT_VAR_CHOICES, required=False, initial="", label="Column")
    table_variable = forms.ChoiceField(choices=[("", "None")] + STRAT_VAR_CHOICES, required=False, initial="", label="Table")
    output_type = forms.ChoiceField(
        choices=[("table", "Table")],
        widget=forms.HiddenInput,
        initial="table",
        required=False,
        label="Compare measures across groups"
    )

    def __init__(self, *args, geographic_level="none", **kwargs):
        super().__init__(*args, **kwargs)
        self.geographic_level = geographic_level
        # Older dropdowns defaulted both axes to their first choice, Sex.
        # Discard that saved default while preserving intentional valid layouts.
        if (not self.is_bound
                and self.initial.get("row_variable") == "sex"
                and self.initial.get("col_variable") == "sex"
                and not self.initial.get("table_variable")):
            self.initial = dict(self.initial, row_variable="", col_variable="", table_variable="")

    @property
    def variable_groups(self):
        axes = [(name, self[name].value() or "") for name in
                ("row_variable", "col_variable", "table_variable")]
        groups = []
        for title, choices in (("Demographics", STRAT_VAR_CHOICES[:8]),
                               ("Disease Characteristics", STRAT_VAR_CHOICES[8:])):
            variables = []
            for token, label in choices:
                reason = ""
                if token in {"hpsa", "redlined", "metro"}:
                    reason = "Stratified calculations are not yet available"
                if token == "metro" and self.geographic_level not in ("county", "tract"):
                    reason = "County and census tract comparisons only"
                unavailable = bool(reason)
                variables.append({
                    "token": token, "label": label, "unavailable": unavailable, "unavailable_reason": reason,
                    "placements": [{"name": name, "selected": value == token,
                                    "label": self.fields[name].label} for name, value in axes],
                })
            groups.append({"title": title, "variables": variables})
        return groups

    def clean(self):
        cleaned = super().clean()
        selected = [cleaned.get(name) for name in
                    ("row_variable", "col_variable", "table_variable") if cleaned.get(name)]
        if len(selected) != len(set(selected)):
            raise forms.ValidationError("Assign each variable to only one of Row, Column, or Table.")
        if {"age_broad", "age_narrow"}.issubset(selected):
            raise forms.ValidationError("Choose either broad or narrow age categories, not both.")
        if {"receptor3", "receptor4"}.issubset(selected):
            raise forms.ValidationError("Choose either the 3-category or 4-category receptor grouping, not both.")
        if "metro" in selected and self.geographic_level not in ("county", "tract"):
            raise forms.ValidationError("Metro vs. Non-metro is available only for county or census tract comparisons.")
        if set(selected) & {"hpsa", "redlined", "metro"}:
            raise forms.ValidationError("Stratified calculations for HPSA, redlining, and metro status are not yet available.")
        cleaned["output_type"] = "table"
        return cleaned



class MeasuresSelectionForm(forms.Form):
    YEAR_CHOICES = [
        ("2023", "2023"),
        # ("2022", "2022"),
        # ("2021", "2021"),
        # ("2020", "2020"),
    ]

    GEOGRAPHY_CHOICES = [
        ("county", "County"),
        ("tract", "Census Tract"),
        ("zcta", "ZIP Code Tabulation Area"),
    ]

    year = forms.ChoiceField(
        choices=YEAR_CHOICES,
        required=True,
        label="Year",
    )

    geographic_level = forms.ChoiceField(
        choices=GEOGRAPHY_CHOICES,
        required=True,
        label="Geographic level",
    )
