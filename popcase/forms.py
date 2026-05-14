from django import forms

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

RACE_CHOICES = [
    ("all", "All"),
    ("nh_white", "Non-Hispanic White"),
    ("nh_black", "Non-Hispanic Black"),
    ("nh_aian", "Non-Hispanic AI/AN"),
    ("nh_api", "Non-Hispanic API"),
    ("hisp_any", "Hispanic (any race)"),
]

STAGE_CHOICES = [
    ("all", "All"),
    ("in_situ", "In situ"),
    ("localized", "Localized"),
    ("regional", "Regional"),
    ("metastatic", "Metastatic"),
    ("unknown", "Unknown"),
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
    ("case_count", "Case Count"),
    ("pct_advanced", "% Advanced at diagnosis (Regional or metastatic spread)"),
    ("pct_metastatic", "% Metastatic at diagnosis"),
    ("median_tti", "Median time to treatment initiation"),
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
    ("median_age", "Median age"),
    ("race_eth", "Race/Ethnicity"),
    ("med_hh_income", "Median household income"),
    ("poverty_pct", "% of households below poverty level"),
    ("snap_pct", "% of households receiving Food stamps/SNAP"),
    ("gini", "GINI Index"),
    ("redlined_pct", "% of population living in formerly-redlined neighborhoods"),
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

# Diagnosis year choices (Filters step)
# MVP: 2011–2022 (you can later populate dynamically from DB)
DX_YEAR_CHOICES = [("", "Any")] + [(str(y), str(y)) for y in range(2011, 2023)]


class GeographicLevelForm(forms.Form):
    geographic_level = forms.ChoiceField(
        choices=GEO_CHOICES,
        widget=forms.RadioSelect,
        initial="none",
        label="Choose a geographic level"
    )


class FiltersForm(forms.Form):
    sex = forms.ChoiceField(choices=SEX_CHOICES, widget=forms.RadioSelect, initial="all", label="Sex")
    age_from = forms.IntegerField(required=False, min_value=0, max_value=120, label="Age from", widget=forms.NumberInput(attrs={"class": "form-control", "inputmode": "numeric"}))
    age_to = forms.IntegerField(required=False, min_value=0, max_value=120, label="Age to", widget=forms.NumberInput(attrs={"class": "form-control", "inputmode": "numeric"}))
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

    dx_start = forms.ChoiceField(
        choices=DX_YEAR_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Diagnosis year from"
    )

    dx_end = forms.ChoiceField(
        choices=DX_YEAR_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Diagnosis year to"
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
        a, b = cleaned.get("age_from"), cleaned.get("age_to")
        if a is not None and b is not None and a > b:
            self.add_error("age_to", "Age 'to' must be >= age 'from'.")
        s = cleaned.get("dx_start")
        e = cleaned.get("dx_end")
        if s and e:
            try:
                if int(s) > int(e):
                    self.add_error("dx_end", "Diagnosis year 'to' must be >= diagnosis year 'from'.")
            except ValueError:
                pass
        return cleaned

class MeasuresForm(forms.Form):
    # Leaf options only; categories are rendered in the template (not selectable).

    DISEASE_LEAVES = [
        ("case_count", "Case Count"),

        ("pct_advanced", "% Advanced at diagnosis"),
        ("pct_advanced_ci", "95% Confidence Interval (% Advanced)"),

        ("pct_metastatic", "% Metastatic at diagnosis"),
        ("pct_metastatic_ci", "95% Confidence Interval (% Metastatic)"),

        ("median_tti", "Median time to treatment initiation"),
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
        ("smoking", "Current cigarette smoking"),
        ("obesity", "Obesity"),
        ("binge_drinking", "Binge drinking"),
        ("no_leisure_pa", "No leisure-time physical activity"),
        ("short_sleep", "Short sleep duration"),
        ("crc_screen", "Colorectal cancer screening (age 45-75)"),
        ("breast_screen", "Breast cancer screening (age 50-74)"),
        ("cervical_screen", "Cervical cancer screening"),
    ]

    HEALTH_STATUS_LEAVES = [
        ("poor_health", "Fair or poor self-rated health status"),
        ("phys_distress", "Frequent physical distress"),
        ("mental_distress", "Frequent mental distress"),
        ("food_insecurity", "Food insecurity in the past 12 months"),
        ("social_isolation", "Feeling socially isolated"),
        ("any_disability", "Any disability"),
        ("mobility_disability", "Mobility disability"),
        ("selfcare_disability", "Self-care disability"),
        ("independent_living_disability", "Independent living disability"),
    ]

    SURVEY_ACCESS_LEAVES = [
        ("routine_checkup", "% who visited doctor for routine checkup within the past year among adults"),
        ("no_transport", "% with lack of reliable transportation in the past 12 months among adults"),
        ("no_insurance", "% with current lack of health insurance among adults aged 18-64 years"),
        ("dentist", "% who visited dentist or dental clinic in the past year among adults"),
    ]

    # Community characteristics (ACS-style) leaf options
    COMMUNITY_BASIC_LEAVES = [
        ("pop_total", "Total population"),
        ("sex_dist", "Sex distribution"),
        ("median_age", "Median age"),
        ("race_eth", "Race/Ethnicity"),
    ]
    COMMUNITY_EXT_LEAVES = [
        ("age_dist", "Age distribution (__ groups)"),
        ("marital_status", "Marital status"),
        ("educ_attain", "Educational attainment"),
        ("lang_home", "Distribution of language spoken at home"),
        ("limited_english", "% of residents >= age 5 who speak English less than very well"),
        ("citizenship", "Citizenship status"),
        ("rurality", "Rurality (RUCC / RUCA code)"),
    ]
    COMMUNITY_ECON_LEAVES = [
        ("med_hh_income", "Median household income"),
        ("per_capita_income", "Per capita income"),
        ("poverty_pct", "% of households below poverty level"),
        ("income_pov_ratio", "Income to poverty-level ratio"),
        ("snap_pct", "% of households receiving Food stamps/SNAP"),
        ("employment_16plus", "Employment status for population >=16 years"),
        ("utility_shutoff_threat", "Utility services shut-off threat in the past 12 months among adults"),
        ("occupation_dist", "Occupational category distribution"),
        ("gini", "GINI Index"),
        ("redlined_pct", "% of population living in formerly-redlined neighborhoods"),
        ("svi_adi", "Social Vulnerability Index (ADI, including all subcomponents)"),
    ]
    COMMUNITY_HOUSING_LEAVES = [
        ("housing_unoccupied", "% of housing units unoccupied"),
        ("renting_pct", "% Renting"),
        ("median_year_built", "Median Year Structure Built"),
        ("median_housing_costs", "Median monthly housing costs (rent or mortgage + fees + utilities + taxes, etc.)"),
        ("occupants_per_room", "Occupants per room"),
        ("plumbing_complete", "% with complete plumbing facilities"),
        ("kitchen_complete", "% with complete kitchen facilities"),
        ("median_home_value", "Median value of occupied housing units"),
    ]
    COMMUNITY_HHCHAR_LEAVES = [
        ("female_headed", "% Female-headed households"),
        ("grandparents_care", "% of households with grandparents caring for children"),
        ("internet_access", "% with internet access by primary type (dialup, high-speed, mobile only)"),
        ("moved_last_year", "% who have moved in last year"),
    ]

    COMMUNITY_TIMEFRAME_CHOICES = [
        ("most_recent", "Most recent available"),
        ("historical", "Historical (to align with date range in cancer case query specified in Filters tab)"),
    ]

    disease_measures = forms.MultipleChoiceField(
        choices=DISEASE_LEAVES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
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
    access_comm_tract_survey_age_adjusted = forms.BooleanField(
        required=False,
        label="Display age-adjusted measures",
        widget=ci_checkbox_widget,
    )
    access_comm_zcta_place_survey_ci = forms.BooleanField(
        required=False,
        label="Display 95% Confidence Intervals",
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


class StratificationForm(forms.Form):
    row_variable = forms.ChoiceField(choices=STRAT_VAR_CHOICES, required=False, label="Row")
    col_variable = forms.ChoiceField(choices=STRAT_VAR_CHOICES, required=False, label="Column")
    output_type = forms.ChoiceField(
        choices=[("table", "Table")],
        widget=forms.RadioSelect,
        initial="table",
        label="Compare measures across groups"
    )



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
