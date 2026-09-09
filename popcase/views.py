from typing import Dict, Any
from functools import lru_cache
import csv
import json
import re

from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.urls import reverse_lazy
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_http_methods

from .forms import (
    GeographicLevelForm,
    FiltersForm,
    MeasuresForm,
    StratificationForm,
)

from popcase.services import (
    get_incidence_by_geography,
    get_total_incidence,
    get_cancer_type_tree,
    get_cancer_type_leaf_label,
    is_hidden_cancer_type_leaf,
    build_geo_dataset,
    get_default_diagnosis_quarter_range,
    SUPPORT_MEASURE_OUTPUT_SPECS,
    CI_DISPLAY_OPTION_TO_TOKENS,
    AGE_ADJUST_DISPLAY_OPTION_TO_TOKENS,
    _normalize_support_measure_tokens,
)

from .models import NaaccrPatientCensusLinking
from .acs_measures import (
    ACS_MEASURES, COMPONENT_COLUMNS, HEADER_MAP as ACS_HEADER_MAP,
    COLUMN_GROUPS as ACS_COLUMN_GROUPS,
)


STEPS = ["geographic-level", "filters", "measures", "stratification"]
PREVIEW_ROW_LIMIT = 250
PREVIEW_ROW_LIMIT_CHOICES = (10, 50, 100, 250)
SUPPORTED_DISEASE_MEASURES = {
    "case_count",
    "pct_advanced",
    "pct_advanced_ci",
    "pct_metastatic",
    "pct_metastatic_ci",
    "median_tti",
    "median_tti_iqr",
    "crude_inc_rate",
    "crude_inc_ci",
    "crude_mort_rate",
    "crude_mort_ci",
    "inc_rate",
    "inc_ci",
    "mort_rate",
    "mort_ci",
    "gleason",
    "gleason_ci",
}
NORMALIZED_TOTAL_LEVELS = {None, "", "none", "total", "do_not_compare", "no_compare"}

CI_DISPLAY_OPTION_FIELDS = {
    "cancer_risk_factors_ci",
    "cancer_screening_ci",
    "noncancer_health_status_ci",
    "access_comm_tract_survey_ci",
    "access_comm_zcta_place_survey_ci",
    "access_comm_county_survey_ci",
    "community_basic_ci",
    "community_extended_ci",
    "community_economic_ci",
    "community_housing_ci",
    "community_household_ci",
}

AGE_ADJUST_DISPLAY_OPTION_FIELDS = {
    "access_comm_place_survey_age_adjusted",
    "access_comm_county_survey_age_adjusted",
}

TRACT_HEADER_MAP = {
    "label": "Location",
    "geoid": "Geographic ID",
    "tract_geoid": "Census Tract",
    "case_count": "Case count",
    "n_total_staged_unstaged": "N",
    "pct_advanced": "% Advanced",
    "adv_ci_lower": "% Advanced CI 95% (L)",
    "adv_ci_upper": "% Advanced CI 95% (U)",
    "pct_metastatic": "% Metastatic",
    "meta_ci_lower": "% Metastatic CI 95% (L)",
    "meta_ci_upper": "% Metastatic CI 95% (U)",
    "median_tti": "Median time to treatment",
    "median_tti_iqr_lower": "Median time to treatment (25th percentile)",
    "median_tti_iqr_upper": "Median time to treatment (75th percentile)",
    "crude_incidence_per_100k": "Crude incidence /100,000",
    "crude_inc_ci_lower_per_100k": "Crude incidence CI 95% (L) /100,000",
    "crude_inc_ci_upper_per_100k": "Crude incidence CI 95% (U) /100,000",
    "crude_mortality_per_100k": "Crude mortality /100,000",
    "crude_mort_ci_lower_per_100k": "Crude mortality CI 95% (L) /100,000",
    "crude_mort_ci_upper_per_100k": "Crude mortality CI 95% (U) /100,000",
    "age_adjusted_per_100k": "Age-adjusted incidence /100,000",
    "inc_ci_lower_per_100k": "Age-adjusted incidence CI 95% (L) /100,000",
    "inc_ci_upper_per_100k": "Age-adjusted incidence CI 95% (U) /100,000",
    "age_adjusted_mortality_per_100k": "Age-adjusted mortality /100,000",
    "mort_ci_lower_per_100k": "Age-adjusted mortality CI 95% (L) /100,000",
    "mort_ci_upper_per_100k": "Age-adjusted mortality CI 95% (U) /100,000",
    "mean_gleason_score": "Mean Gleason Score",
    "gleason_ci_lower": "Mean Gleason Score CI 95% (L)",
    "gleason_ci_upper": "Mean Gleason Score CI 95% (U)",
    "total_population": "Total population",
    "median_household_income": "Median household income",
    "limited_english_pct": "% speak English less than very well",
    "mammography_screening_pct": "Mammography screening (%)",
    "routine_checkup_pct": "Routine checkup (%)",
    "lack_transportation_pct": "Lack reliable transportation (%)",
    "uninsured_pct": "Uninsured age 18-64 (%)",
    "primary_care_access_score": "Primary care providers per 100,000 (travel time-adjusted)",
    "male_population": "Male population",
    "female_population": "Female population",
    "male_pct": "Male (%)",
    "male_pct_ci_lower": "Male (%) CI 95% (L)",
    "male_pct_ci_upper": "Male (%) CI 95% (U)",
    "female_pct": "Female (%)",
    "female_pct_ci_lower": "Female (%) CI 95% (L)",
    "female_pct_ci_upper": "Female (%) CI 95% (U)",
    "median_age": "Median age (approx)",
    "nearest_mammography_distance_miles": "Nearest mammography facility (miles)",
    "mammography_facility_count_20mi": "Mammography facilities within 20 miles",
    "mammography_access_score": "Mammography access score",
    "white_alone_pct": "White alone (%)",
    "white_alone_ci_lower": "White alone (%) CI 95% (L)",
    "white_alone_ci_upper": "White alone (%) CI 95% (U)",
    "black_alone_pct": "Black alone (%)",
    "black_alone_ci_lower": "Black alone (%) CI 95% (L)",
    "black_alone_ci_upper": "Black alone (%) CI 95% (U)",
    "aian_alone_pct": "AI/AN alone (%)",
    "aian_alone_ci_lower": "AI/AN alone (%) CI 95% (L)",
    "aian_alone_ci_upper": "AI/AN alone (%) CI 95% (U)",
    "asian_alone_pct": "Asian alone (%)",
    "asian_alone_ci_lower": "Asian alone (%) CI 95% (L)",
    "asian_alone_ci_upper": "Asian alone (%) CI 95% (U)",
    "nhpi_alone_pct": "NHPI alone (%)",
    "nhpi_alone_ci_lower": "NHPI alone (%) CI 95% (L)",
    "nhpi_alone_ci_upper": "NHPI alone (%) CI 95% (U)",
    "other_race_alone_pct": "Other race alone (%)",
    "other_race_alone_ci_lower": "Other race alone (%) CI 95% (L)",
    "other_race_alone_ci_upper": "Other race alone (%) CI 95% (U)",
    "multiracial_pct": "Two or more races (%)",
    "multiracial_ci_lower": "Two or more races (%) CI 95% (L)",
    "multiracial_ci_upper": "Two or more races (%) CI 95% (U)",
    "nh_white_pct": "NH White (%)",
    "nh_white_ci_lower": "NH White (%) CI 95% (L)",
    "nh_white_ci_upper": "NH White (%) CI 95% (U)",
    "hispanic_pct": "Hispanic (%)",
    "hispanic_ci_lower": "Hispanic (%) CI 95% (L)",
    "hispanic_ci_upper": "Hispanic (%) CI 95% (U)",
}

TRACT_NUMERIC_COLS = [
    "case_count",
    "n_total_staged_unstaged",
    "pct_advanced",
    "adv_ci_lower",
    "adv_ci_upper",
    "pct_metastatic",
    "meta_ci_lower",
    "meta_ci_upper",
    "median_tti",
    "median_tti_iqr_lower",
    "median_tti_iqr_upper",
    "crude_incidence_per_100k",
    "crude_inc_ci_lower_per_100k",
    "crude_inc_ci_upper_per_100k",
    "crude_mortality_per_100k",
    "crude_mort_ci_lower_per_100k",
    "crude_mort_ci_upper_per_100k",
    "age_adjusted_per_100k",
    "inc_ci_lower_per_100k",
    "inc_ci_upper_per_100k",
    "age_adjusted_mortality_per_100k",
    "mort_ci_lower_per_100k",
    "mort_ci_upper_per_100k",
    "mean_gleason_score",
    "gleason_ci_lower",
    "gleason_ci_upper",
    "total_population",
    "median_household_income",
    "limited_english_pct",
    "mammography_screening_pct",
    "routine_checkup_pct",
    "lack_transportation_pct",
    "uninsured_pct",
    "primary_care_access_score",
    "male_population",
    "female_population",
    "male_pct",
    "male_pct_ci_lower",
    "male_pct_ci_upper",
    "female_pct",
    "female_pct_ci_lower",
    "female_pct_ci_upper",
    "median_age",
    "nearest_mammography_distance_miles",
    "mammography_facility_count_20mi",
    "mammography_access_score",
    "white_alone_pct",
    "white_alone_ci_lower",
    "white_alone_ci_upper",
    "black_alone_pct",
    "black_alone_ci_lower",
    "black_alone_ci_upper",
    "aian_alone_pct",
    "aian_alone_ci_lower",
    "aian_alone_ci_upper",
    "asian_alone_pct",
    "asian_alone_ci_lower",
    "asian_alone_ci_upper",
    "nhpi_alone_pct",
    "nhpi_alone_ci_lower",
    "nhpi_alone_ci_upper",
    "other_race_alone_pct",
    "other_race_alone_ci_lower",
    "other_race_alone_ci_upper",
    "multiracial_pct",
    "multiracial_ci_lower",
    "multiracial_ci_upper",
    "nh_white_pct",
    "nh_white_ci_lower",
    "nh_white_ci_upper",
    "hispanic_pct",
    "hispanic_ci_lower",
    "hispanic_ci_upper",
]


# Headers for the geo-stratified display-option columns added on the Measures page.
# Some of these are placeholders until their source tables/fields are connected in services.py.
SUPPORT_DISPLAY_HEADER_MAP = {
    "smoking_pct": "Current cigarette smoking (%)",
    "smoking_ci_lower": "Current cigarette smoking CI 95% (L)",
    "smoking_ci_upper": "Current cigarette smoking CI 95% (U)",
    "obesity_pct": "Obesity (%)",
    "obesity_ci_lower": "Obesity CI 95% (L)",
    "obesity_ci_upper": "Obesity CI 95% (U)",
    "binge_drinking_pct": "Binge drinking (%)",
    "binge_drinking_ci_lower": "Binge drinking CI 95% (L)",
    "binge_drinking_ci_upper": "Binge drinking CI 95% (U)",
    "no_leisure_pa_pct": "No leisure-time physical activity (%)",
    "no_leisure_pa_ci_lower": "No leisure-time physical activity CI 95% (L)",
    "no_leisure_pa_ci_upper": "No leisure-time physical activity CI 95% (U)",
    "short_sleep_pct": "Short sleep duration (%)",
    "short_sleep_ci_lower": "Short sleep duration CI 95% (L)",
    "short_sleep_ci_upper": "Short sleep duration CI 95% (U)",

    "crc_screening_pct": "Colorectal cancer screening (%)",
    "crc_screening_ci_lower": "Colorectal cancer screening CI 95% (L)",
    "crc_screening_ci_upper": "Colorectal cancer screening CI 95% (U)",
    "mammography_screening_ci_lower": "Mammography screening CI 95% (L)",
    "mammography_screening_ci_upper": "Mammography screening CI 95% (U)",
    "cervical_screening_pct": "Cervical cancer screening (%)",
    "cervical_screening_ci_lower": "Cervical cancer screening CI 95% (L)",
    "cervical_screening_ci_upper": "Cervical cancer screening CI 95% (U)",

    "poor_health_pct": "Fair or poor self-rated health status (%)",
    "poor_health_ci_lower": "Fair or poor self-rated health status CI 95% (L)",
    "poor_health_ci_upper": "Fair or poor self-rated health status CI 95% (U)",
    "phys_distress_pct": "Frequent physical distress (%)",
    "phys_distress_ci_lower": "Frequent physical distress CI 95% (L)",
    "phys_distress_ci_upper": "Frequent physical distress CI 95% (U)",
    "mental_distress_pct": "Frequent mental distress (%)",
    "mental_distress_ci_lower": "Frequent mental distress CI 95% (L)",
    "mental_distress_ci_upper": "Frequent mental distress CI 95% (U)",
    "food_insecurity_pct": "Food insecurity in the past 12 months (%)",
    "food_insecurity_ci_lower": "Food insecurity CI 95% (L)",
    "food_insecurity_ci_upper": "Food insecurity CI 95% (U)",
    "social_isolation_pct": "Feeling socially isolated (%)",
    "social_isolation_ci_lower": "Social isolation CI 95% (L)",
    "social_isolation_ci_upper": "Social isolation CI 95% (U)",
    "any_disability_pct": "Any disability (%)",
    "any_disability_ci_lower": "Any disability CI 95% (L)",
    "any_disability_ci_upper": "Any disability CI 95% (U)",
    "mobility_disability_pct": "Mobility disability (%)",
    "mobility_disability_ci_lower": "Mobility disability CI 95% (L)",
    "mobility_disability_ci_upper": "Mobility disability CI 95% (U)",
    "selfcare_disability_pct": "Self-care disability (%)",
    "selfcare_disability_ci_lower": "Self-care disability CI 95% (L)",
    "selfcare_disability_ci_upper": "Self-care disability CI 95% (U)",
    "independent_living_disability_pct": "Independent living disability (%)",
    "independent_living_disability_ci_lower": "Independent living disability CI 95% (L)",
    "independent_living_disability_ci_upper": "Independent living disability CI 95% (U)",

    "routine_checkup_ci_lower": "Routine checkup CI 95% (L)",
    "routine_checkup_ci_upper": "Routine checkup CI 95% (U)",
    "routine_checkup_age_adjusted_pct": "Routine checkup age-adjusted (%)",
    "lack_transportation_ci_lower": "Lack reliable transportation CI 95% (L)",
    "lack_transportation_ci_upper": "Lack reliable transportation CI 95% (U)",
    "lack_transportation_age_adjusted_pct": "Lack reliable transportation age-adjusted (%)",
    "uninsured_ci_lower": "Uninsured age 18-64 CI 95% (L)",
    "uninsured_ci_upper": "Uninsured age 18-64 CI 95% (U)",
    "uninsured_age_adjusted_pct": "Uninsured age 18-64 age-adjusted (%)",
    "dentist_pct": "Visited dentist/dental clinic in past year (%)",
    "dentist_ci_lower": "Dentist visit CI 95% (L)",
    "dentist_ci_upper": "Dentist visit CI 95% (U)",
    "dentist_age_adjusted_pct": "Dentist visit age-adjusted (%)",

    "total_population_ci_lower": "Total population CI 95% (L)",
    "total_population_ci_upper": "Total population CI 95% (U)",
    "sex_distribution": "Sex distribution",
    "sex_distribution_ci_lower": "Sex distribution CI 95% (L)",
    "sex_distribution_ci_upper": "Sex distribution CI 95% (U)",
    "median_age_ci_lower": "Median age CI 95% (L)",
    "median_age_ci_upper": "Median age CI 95% (U)",
    "race_ethnicity": "Race/Ethnicity",
    "race_eth_ci_lower": "Race/Ethnicity CI 95% (L)",
    "race_eth_ci_upper": "Race/Ethnicity CI 95% (U)",
    "age_distribution": "Age distribution",
    "age_distribution_ci_lower": "Age distribution CI 95% (L)",
    "age_distribution_ci_upper": "Age distribution CI 95% (U)",
    "marital_status": "Marital status",
    "marital_status_ci_lower": "Marital status CI 95% (L)",
    "marital_status_ci_upper": "Marital status CI 95% (U)",
    "educational_attainment": "Educational attainment",
    "educational_attainment_ci_lower": "Educational attainment CI 95% (L)",
    "educational_attainment_ci_upper": "Educational attainment CI 95% (U)",
    "language_home": "Distribution of language spoken at home",
    "language_home_ci_lower": "Language spoken at home CI 95% (L)",
    "language_home_ci_upper": "Language spoken at home CI 95% (U)",
    "limited_english_ci_lower": "% speak English less than very well CI 95% (L)",
    "limited_english_ci_upper": "% speak English less than very well CI 95% (U)",
    "citizenship_status": "Citizenship status",
    "citizenship_status_ci_lower": "Citizenship status CI 95% (L)",
    "citizenship_status_ci_upper": "Citizenship status CI 95% (U)",
    "rurality": "Rurality (RUCC / RUCA code)",
    "rurality_description": "Rurality Description",
    "rurality_ci_lower": "Rurality CI 95% (L)",
    "rurality_ci_upper": "Rurality CI 95% (U)",
    "median_household_income_ci_lower": "Median household income CI 95% (L)",
    "median_household_income_ci_upper": "Median household income CI 95% (U)",
    "per_capita_income": "Per capita income",
    "per_capita_income_ci_lower": "Per capita income CI 95% (L)",
    "per_capita_income_ci_upper": "Per capita income CI 95% (U)",
    "poverty_pct": "% households below poverty level",
    "poverty_ci_lower": "Poverty CI 95% (L)",
    "poverty_ci_upper": "Poverty CI 95% (U)",
    "income_poverty_ratio": "Income to poverty-level ratio",
    "income_poverty_ratio_ci_lower": "Income to poverty-level ratio CI 95% (L)",
    "income_poverty_ratio_ci_upper": "Income to poverty-level ratio CI 95% (U)",
    "snap_pct": "Food stamps/SNAP (%) [CDC PLACES]",
    "snap_ci_lower": "Food stamps/SNAP CI 95% (L)",
    "snap_ci_upper": "Food stamps/SNAP CI 95% (U)",
    "employment_16plus": "Employment status for population >=16 years",
    "employment_labor_force_pct": "Labor force (%)",
    "employment_labor_force_ci_lower": "Labor force CI 95% (L)",
    "employment_labor_force_ci_upper": "Labor force CI 95% (U)",
    "employment_employed_pct": "Employed (%)",
    "employment_employed_ci_lower": "Employed CI 95% (L)",
    "employment_employed_ci_upper": "Employed CI 95% (U)",
    "employment_unemployed_pct": "Unemployed (%)",
    "employment_unemployed_ci_lower": "Unemployed CI 95% (L)",
    "employment_unemployed_ci_upper": "Unemployed CI 95% (U)",
    "employment_not_in_labor_force_pct": "Not in labor force (%)",
    "employment_not_in_labor_force_ci_lower": "Not in labor force CI 95% (L)",
    "employment_not_in_labor_force_ci_upper": "Not in labor force CI 95% (U)",
    "utility_shutoff_threat_pct": "Utility services shut-off threat (%)",
    "utility_shutoff_threat_ci_lower": "Utility shut-off threat CI 95% (L)",
    "utility_shutoff_threat_ci_upper": "Utility shut-off threat CI 95% (U)",
    "housing_insecurity_pct": "Housing insecurity (%)",
    "housing_insecurity_ci_lower": "Housing insecurity CI 95% (L)",
    "housing_insecurity_ci_upper": "Housing insecurity CI 95% (U)",
    "occupation_distribution": "Occupational category distribution",
    "occupation_management_business_science_arts_pct": "Occupation: management/business/science/arts (%)",
    "occupation_management_business_science_arts_ci_lower": "Occupation: management/business/science/arts CI 95% (L)",
    "occupation_management_business_science_arts_ci_upper": "Occupation: management/business/science/arts CI 95% (U)",
    "occupation_service_pct": "Occupation: service (%)",
    "occupation_service_ci_lower": "Occupation: service CI 95% (L)",
    "occupation_service_ci_upper": "Occupation: service CI 95% (U)",
    "occupation_sales_office_pct": "Occupation: sales/office (%)",
    "occupation_sales_office_ci_lower": "Occupation: sales/office CI 95% (L)",
    "occupation_sales_office_ci_upper": "Occupation: sales/office CI 95% (U)",
    "occupation_natural_resources_construction_maintenance_pct": "Occupation: natural resources/construction/maintenance (%)",
    "occupation_natural_resources_construction_maintenance_ci_lower": "Occupation: natural resources/construction/maintenance CI 95% (L)",
    "occupation_natural_resources_construction_maintenance_ci_upper": "Occupation: natural resources/construction/maintenance CI 95% (U)",
    "occupation_production_transportation_material_moving_pct": "Occupation: production/transportation/material moving (%)",
    "occupation_production_transportation_material_moving_ci_lower": "Occupation: production/transportation/material moving CI 95% (L)",
    "occupation_production_transportation_material_moving_ci_upper": "Occupation: production/transportation/material moving CI 95% (U)",
    "gini_index": "GINI Index",
    "gini_ci_lower": "GINI Index CI 95% (L)",
    "gini_ci_upper": "GINI Index CI 95% (U)",
    "redlined_pct": "% of population living in formerly redlined neighborhoods",
    "ranked_historic_redlining_index": "Ranked Historic Redlining Index",
    "redlined_ci_lower": "% of population living in formerly redlined neighborhoods CI 95% (L)",
    "redlined_ci_upper": "% of population living in formerly redlined neighborhoods CI 95% (U)",
    "svi_adi": "% population in ADI deciles 9-10",
    "adi_pct_deciles_9_10": "% population in ADI deciles 9-10",
    "adi_population_deciles_9_10": "Population in ADI deciles 9-10",
    "adi_total_population": "ADI denominator population",
    "svi_adi_ci_lower": "ADI/SVI CI 95% (L)",
    "svi_adi_ci_upper": "ADI/SVI CI 95% (U)",

    "housing_unoccupied_pct": "% housing units unoccupied",
    "housing_unoccupied_ci_lower": "Housing units unoccupied CI 95% (L)",
    "housing_unoccupied_ci_upper": "Housing units unoccupied CI 95% (U)",
    "renting_pct": "% Renting",
    "renting_ci_lower": "Renting CI 95% (L)",
    "renting_ci_upper": "Renting CI 95% (U)",
    "median_year_built": "Median Year Structure Built",
    "median_year_built_ci_lower": "Median Year Structure Built CI 95% (L)",
    "median_year_built_ci_upper": "Median Year Structure Built CI 95% (U)",
    "median_housing_costs": "Median monthly housing costs",
    "median_housing_costs_ci_lower": "Median housing costs CI 95% (L)",
    "median_housing_costs_ci_upper": "Median housing costs CI 95% (U)",
    "occupants_per_room": "Occupants per room",
    "occupants_per_room_ci_lower": "Occupants per room CI 95% (L)",
    "occupants_per_room_ci_upper": "Occupants per room CI 95% (U)",
    "plumbing_complete_pct": "% with complete plumbing facilities",
    "plumbing_complete_ci_lower": "Complete plumbing facilities CI 95% (L)",
    "plumbing_complete_ci_upper": "Complete plumbing facilities CI 95% (U)",
    "kitchen_complete_pct": "% with complete kitchen facilities",
    "kitchen_complete_ci_lower": "Complete kitchen facilities CI 95% (L)",
    "kitchen_complete_ci_upper": "Complete kitchen facilities CI 95% (U)",
    "median_home_value": "Median value of occupied housing units",
    "median_home_value_ci_lower": "Median home value CI 95% (L)",
    "median_home_value_ci_upper": "Median home value CI 95% (U)",

    "female_headed_pct": "% Female-headed households",
    "female_headed_ci_lower": "Female-headed households CI 95% (L)",
    "female_headed_ci_upper": "Female-headed households CI 95% (U)",
    "grandparents_care_pct": "% households with grandparents caring for children",
    "grandparents_care_ci_lower": "Grandparents caring for children CI 95% (L)",
    "grandparents_care_ci_upper": "Grandparents caring for children CI 95% (U)",
    "internet_access_pct": "% with internet access",
    "internet_access_ci_lower": "Internet access CI 95% (L)",
    "internet_access_ci_upper": "Internet access CI 95% (U)",
    "moved_last_year_pct": "% who moved in last year",
    "moved_last_year_ci_lower": "Moved in last year CI 95% (L)",
    "moved_last_year_ci_upper": "Moved in last year CI 95% (U)",
}
SUPPORT_DISPLAY_HEADER_MAP.update(ACS_HEADER_MAP)
TRACT_HEADER_MAP.update(SUPPORT_DISPLAY_HEADER_MAP)
TRACT_NUMERIC_COLS = list(dict.fromkeys(TRACT_NUMERIC_COLS + list(SUPPORT_DISPLAY_HEADER_MAP.keys())))

# Geography-agnostic aliases used by the results page and CSV exporter.
# The old TRACT_* names are kept only as backward-compatible aliases.
DATASET_HEADER_MAP = TRACT_HEADER_MAP
DATASET_NUMERIC_COLS = TRACT_NUMERIC_COLS

# These are aggregate placeholder columns created only to trigger display-option logic.
# They do not represent a single measurable estimate/CI and should not be displayed
# or exported. The real component columns (male/female %, race-specific %, etc.)
# are displayed instead.
DATASET_EXCLUDE_COLUMNS = {
    "provider_data_note",  # Also hides this retired column in cached datasets.
    "n_total_staged_unstaged",
    "sex_distribution",
    "sex_distribution_ci_lower",
    "sex_distribution_ci_upper",
    "race_ethnicity",
    "race_eth_ci_lower",
    "race_eth_ci_upper",
    "employment_16plus",
    "occupation_distribution",
}
# Distribution measures expose their real components, never an empty aggregate.
for _token in ACS_MEASURES:
    _spec = SUPPORT_MEASURE_OUTPUT_SPECS[_token]
    DATASET_EXCLUDE_COLUMNS.update(key for key in _spec[:3] if key not in COMPONENT_COLUMNS[_token])



DATASET_COLUMN_GROUPS = {
    "white_alone_pct": "Race/Ethnicity",
    "white_alone_ci_lower": "Race/Ethnicity",
    "white_alone_ci_upper": "Race/Ethnicity",
    "black_alone_pct": "Race/Ethnicity",
    "black_alone_ci_lower": "Race/Ethnicity",
    "black_alone_ci_upper": "Race/Ethnicity",
    "aian_alone_pct": "Race/Ethnicity",
    "aian_alone_ci_lower": "Race/Ethnicity",
    "aian_alone_ci_upper": "Race/Ethnicity",
    "asian_alone_pct": "Race/Ethnicity",
    "asian_alone_ci_lower": "Race/Ethnicity",
    "asian_alone_ci_upper": "Race/Ethnicity",
    "nhpi_alone_pct": "Race/Ethnicity",
    "nhpi_alone_ci_lower": "Race/Ethnicity",
    "nhpi_alone_ci_upper": "Race/Ethnicity",
    "other_race_alone_pct": "Race/Ethnicity",
    "other_race_alone_ci_lower": "Race/Ethnicity",
    "other_race_alone_ci_upper": "Race/Ethnicity",
    "multiracial_pct": "Race/Ethnicity",
    "multiracial_ci_lower": "Race/Ethnicity",
    "multiracial_ci_upper": "Race/Ethnicity",
    "nh_white_pct": "Race/Ethnicity",
    "nh_white_ci_lower": "Race/Ethnicity",
    "nh_white_ci_upper": "Race/Ethnicity",
    "hispanic_pct": "Race/Ethnicity",
    "hispanic_ci_lower": "Race/Ethnicity",
    "hispanic_ci_upper": "Race/Ethnicity",

    "employment_labor_force_pct": "Employment status for population >=16 years",
    "employment_labor_force_ci_lower": "Employment status for population >=16 years",
    "employment_labor_force_ci_upper": "Employment status for population >=16 years",
    "employment_employed_pct": "Employment status for population >=16 years",
    "employment_employed_ci_lower": "Employment status for population >=16 years",
    "employment_employed_ci_upper": "Employment status for population >=16 years",
    "employment_unemployed_pct": "Employment status for population >=16 years",
    "employment_unemployed_ci_lower": "Employment status for population >=16 years",
    "employment_unemployed_ci_upper": "Employment status for population >=16 years",
    "employment_not_in_labor_force_pct": "Employment status for population >=16 years",
    "employment_not_in_labor_force_ci_lower": "Employment status for population >=16 years",
    "employment_not_in_labor_force_ci_upper": "Employment status for population >=16 years",

    "occupation_management_business_science_arts_pct": "Occupational category distribution",
    "occupation_management_business_science_arts_ci_lower": "Occupational category distribution",
    "occupation_management_business_science_arts_ci_upper": "Occupational category distribution",
    "occupation_service_pct": "Occupational category distribution",
    "occupation_service_ci_lower": "Occupational category distribution",
    "occupation_service_ci_upper": "Occupational category distribution",
    "occupation_sales_office_pct": "Occupational category distribution",
    "occupation_sales_office_ci_lower": "Occupational category distribution",
    "occupation_sales_office_ci_upper": "Occupational category distribution",
    "occupation_natural_resources_construction_maintenance_pct": "Occupational category distribution",
    "occupation_natural_resources_construction_maintenance_ci_lower": "Occupational category distribution",
    "occupation_natural_resources_construction_maintenance_ci_upper": "Occupational category distribution",
    "occupation_production_transportation_material_moving_pct": "Occupational category distribution",
    "occupation_production_transportation_material_moving_ci_lower": "Occupational category distribution",
    "occupation_production_transportation_material_moving_ci_upper": "Occupational category distribution",
}

DATASET_COLUMN_GROUPS.update(ACS_COLUMN_GROUPS)

DISEASE_MEASURE_OUTPUT_COLUMNS = {
    "case_count": ["case_count"],
    "pct_advanced": ["pct_advanced"],
    "pct_advanced_ci": ["adv_ci_lower", "adv_ci_upper"],
    "pct_metastatic": ["pct_metastatic"],
    "pct_metastatic_ci": ["meta_ci_lower", "meta_ci_upper"],
    "median_tti": ["median_tti"],
    "median_tti_iqr": ["median_tti_iqr_lower", "median_tti_iqr_upper"],
    "crude_inc_rate": ["crude_incidence_per_100k"],
    "crude_inc_ci": ["crude_inc_ci_lower_per_100k", "crude_inc_ci_upper_per_100k"],
    "crude_mort_rate": ["crude_mortality_per_100k"],
    "crude_mort_ci": ["crude_mort_ci_lower_per_100k", "crude_mort_ci_upper_per_100k"],
    "inc_rate": ["age_adjusted_per_100k"],
    "inc_ci": ["inc_ci_lower_per_100k", "inc_ci_upper_per_100k"],
    "mort_rate": ["age_adjusted_mortality_per_100k"],
    "mort_ci": ["mort_ci_lower_per_100k", "mort_ci_upper_per_100k"],
    "gleason": ["mean_gleason_score"],
    "gleason_ci": ["gleason_ci_lower", "gleason_ci_upper"],
}

SUPPORT_COMPONENT_OUTPUT_COLUMNS = {
    "sex_distribution": [
        "male_population", "female_population", "male_pct", "female_pct",
        "male_pct_ci_lower", "male_pct_ci_upper", "female_pct_ci_lower", "female_pct_ci_upper",
    ],
    "race_eth": [
        "white_alone_pct", "white_alone_ci_lower", "white_alone_ci_upper",
        "black_alone_pct", "black_alone_ci_lower", "black_alone_ci_upper",
        "aian_alone_pct", "aian_alone_ci_lower", "aian_alone_ci_upper",
        "asian_alone_pct", "asian_alone_ci_lower", "asian_alone_ci_upper",
        "nhpi_alone_pct", "nhpi_alone_ci_lower", "nhpi_alone_ci_upper",
        "other_race_alone_pct", "other_race_alone_ci_lower", "other_race_alone_ci_upper",
        "multiracial_pct", "multiracial_ci_lower", "multiracial_ci_upper",
        "nh_white_pct", "nh_white_ci_lower", "nh_white_ci_upper",
        "hispanic_pct", "hispanic_ci_lower", "hispanic_ci_upper",
    ],
    "employment_16plus": [
        "employment_labor_force_pct", "employment_labor_force_ci_lower", "employment_labor_force_ci_upper",
        "employment_employed_pct", "employment_employed_ci_lower", "employment_employed_ci_upper",
        "employment_unemployed_pct", "employment_unemployed_ci_lower", "employment_unemployed_ci_upper",
        "employment_not_in_labor_force_pct", "employment_not_in_labor_force_ci_lower", "employment_not_in_labor_force_ci_upper",
    ],
    "occupation_dist": [
        "occupation_management_business_science_arts_pct", "occupation_management_business_science_arts_ci_lower", "occupation_management_business_science_arts_ci_upper",
        "occupation_service_pct", "occupation_service_ci_lower", "occupation_service_ci_upper",
        "occupation_sales_office_pct", "occupation_sales_office_ci_lower", "occupation_sales_office_ci_upper",
        "occupation_natural_resources_construction_maintenance_pct", "occupation_natural_resources_construction_maintenance_ci_lower", "occupation_natural_resources_construction_maintenance_ci_upper",
        "occupation_production_transportation_material_moving_pct", "occupation_production_transportation_material_moving_ci_lower", "occupation_production_transportation_material_moving_ci_upper",
    ],
    "rurality": ["rurality", "rurality_description", "rurality_ci_lower", "rurality_ci_upper"],
    "pcp_access_score": ["primary_care_access_score", "primary_care_providers_per_100k"],
    "onc": ["oncology_providers_per_100k"],
    "ext_care": ["extended_cancer_care_providers_per_100k"],
    "mammo_access": ["mammography_facilities_per_100k", "nearest_mammography_distance_miles", "mammography_facility_count_20mi", "mammography_access_score"],
}
TRACT_HEADER_MAP.update({
    "primary_care_providers_per_100k": "Primary care providers per 100,000 in county",
    "oncology_providers_per_100k": "Oncology providers per 100,000 in county",
    "extended_cancer_care_providers_per_100k": "Extended cancer care providers per 100,000 in county",
    "mammography_facilities_per_100k": "Mammogram facilities per 100,000 in county",
})
SUPPORT_COMPONENT_OUTPUT_COLUMNS.update(COMPONENT_COLUMNS)
for _prefix, _label in [("routine_checkup", "Routine checkup"), ("lack_transportation", "Lack reliable transportation"), ("uninsured", "Uninsured age 18-64"), ("dentist", "Dentist visit")]:
    for _suffix, _side in [("lower", "L"), ("upper", "U")]:
        TRACT_HEADER_MAP[f"{_prefix}_age_adjusted_ci_{_suffix}"] = f"{_label} age-adjusted CI 95% ({_side})"


class PopcaseLoginView(auth_views.LoginView):
    template_name = "popcase/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        return self.get_redirect_url() or reverse_lazy("popcase:wizard")


class PopcaseLogoutView(auth_views.LogoutView):
    next_page = reverse_lazy("popcase:login")


@lru_cache(maxsize=1)
def _latest_linking_year():
    return (
        NaaccrPatientCensusLinking.objects.values_list("year", flat=True)
        .order_by("-year")
        .first()
        or "2022"
    )


def _coerce_to_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return []


def _unique_in_order(values):
    return list(dict.fromkeys(v for v in values if v))


def _normalize_geographic_level(level: str) -> str:
    return "total" if level in NORMALIZED_TOTAL_LEVELS else level


def _serialize_payload(value):
    return json.dumps(value or {}, sort_keys=True, separators=(",", ":"))


def _deserialize_payload(value):
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


def _get_age_group_labels(age_groups, geographic_level: str):
    choices = dict(FiltersForm.get_age_group_choices_for_geography("tract"))
    choices.update(dict(FiltersForm.get_age_group_choices_for_geography("place")))
    choices.update(dict(FiltersForm.get_age_group_choices_for_geography(geographic_level)))
    return [choices.get(value, value) for value in _coerce_to_list(age_groups) if value]

def _filter_disease_measures_for_geography(disease_measures, geographic_level: str):
    # The SDD defines the same Disease-Focused measure choices for County,
    # Place, ZCTA, and Census Tract. Geography changes the age-adjustment
    # method, not which crude or age-adjusted rate choices are available.
    return _coerce_to_list(disease_measures)


def _get_preview_row_limit(request):
    try:
        requested_limit = int(request.GET.get("rows", PREVIEW_ROW_LIMIT))
    except (TypeError, ValueError):
        return PREVIEW_ROW_LIMIT

    if requested_limit in PREVIEW_ROW_LIMIT_CHOICES:
        return requested_limit
    return PREVIEW_ROW_LIMIT


@lru_cache(maxsize=128)
def _build_results_payload_cached(
    geographic_level: str,
    dx_start: str,
    dx_end: str,
    filters_json: str,
    disease_measures_tuple: tuple,
    support_measures_tuple: tuple,
    display_options_tuple: tuple,
    community_timeframes_tuple: tuple,
    latest_year: str,
):
    filters = _deserialize_payload(filters_json)
    disease_measures = list(disease_measures_tuple)
    support_measures = list(support_measures_tuple)
    display_options = list(display_options_tuple)
    community_timeframes = list(community_timeframes_tuple)

    incidence = []
    total_incidence = None
    dataset_rows = []
    result_mode = "none"

    has_dataset_request = bool(SUPPORTED_DISEASE_MEASURES.intersection(disease_measures) or support_measures)

    if geographic_level in {"tract", "county", "zcta", "place"} and has_dataset_request:
        dataset_rows = build_geo_dataset(
            geographic_level=geographic_level,
            year_range=(dx_start, dx_end),
            filters=filters,
            disease_measures=disease_measures,
            support_measures=support_measures,
            display_options=display_options,
            community_timeframes=community_timeframes,
            incidence_year=latest_year,
        ) or []
        result_mode = "dataset"
    elif geographic_level == "total":
        total_incidence = get_total_incidence(year=latest_year, filters=filters)
        result_mode = "incidence" if total_incidence else "none"
    else:
        incidence = get_incidence_by_geography(
            year=latest_year,
            geographic_level=geographic_level,
            filters=filters,
        ) or []
        result_mode = "incidence" if incidence else "none"

    return {
        "incidence": incidence,
        "total_incidence": total_incidence,
        "dataset_rows": dataset_rows,
        "result_mode": result_mode,
    }



def _get_measure_selections(measures_state: dict, geographic_level: str):
    disease_measures = _coerce_to_list(measures_state.get("disease_measures"))
    cancer_prevention_measures = _coerce_to_list(measures_state.get("cancer_prevention"))
    health_status_measures = _coerce_to_list(measures_state.get("noncancer_health_status"))
    community_measures = _coerce_to_list(measures_state.get("community_characteristics"))

    access_field_by_geo = {
        "tract": "access_comm_tract",
        "county": "access_comm_county",
        "zcta": "access_comm_zcta_place",
        "place": "access_comm_zcta_place",
    }
    access_measures = _coerce_to_list(measures_state.get(access_field_by_geo.get(geographic_level)))
    support_measures = _unique_in_order(
        cancer_prevention_measures
        + health_status_measures
        + access_measures
        + community_measures
    )

    return disease_measures, support_measures


def _ordered_selected_tokens(selected_tokens, choice_groups):
    selected = set(_coerce_to_list(selected_tokens))
    ordered = []
    for choices in choice_groups:
        for token, _ in choices:
            if token in selected and token not in ordered:
                ordered.append(token)
    for token in _coerce_to_list(selected_tokens):
        if token not in ordered:
            ordered.append(token)
    return ordered


def _support_columns_for_token(token, display_options):
    normalized_tokens = _normalize_support_measure_tokens([token])
    columns = []
    for normalized in normalized_tokens:
        if normalized in SUPPORT_COMPONENT_OUTPUT_COLUMNS:
            columns.extend(SUPPORT_COMPONENT_OUTPUT_COLUMNS[normalized])
            continue

        spec = SUPPORT_MEASURE_OUTPUT_SPECS.get(normalized)
        if not spec:
            continue

        value_key, ci_low_key, ci_high_key, age_adjusted_key = spec
        columns.append(value_key)
        columns.extend([ci_low_key, ci_high_key])
        if age_adjusted_key:
            columns.append(age_adjusted_key)
            columns.extend(age_adjusted_key.replace("_pct", suffix) for suffix in ("_ci_lower", "_ci_upper"))
    return columns


def _build_preferred_dataset_columns(disease_measures, support_measures, display_options, geographic_level):
    disease_order = _ordered_selected_tokens(disease_measures, [MeasuresForm.DISEASE_LEAVES])

    access_choices = {
        "tract": MeasuresForm.ACCESS_PATIENT_LEAVES + MeasuresForm.SURVEY_ACCESS_LEAVES,
        "county": MeasuresForm.ACCESS_PATIENT_LEAVES + MeasuresForm.SURVEY_ACCESS_LEAVES,
        "zcta": MeasuresForm.SURVEY_ACCESS_LEAVES,
        "place": MeasuresForm.SURVEY_ACCESS_LEAVES,
    }.get(geographic_level, [])
    support_order = _ordered_selected_tokens(
        support_measures,
        [
            MeasuresForm.CANCER_PREVENTION_LEAVES,
            MeasuresForm.HEALTH_STATUS_LEAVES,
            access_choices,
            MeasuresForm.COMMUNITY_BASIC_LEAVES,
            MeasuresForm.COMMUNITY_EXT_LEAVES,
            MeasuresForm.COMMUNITY_ECON_LEAVES,
            MeasuresForm.COMMUNITY_HOUSING_LEAVES,
            MeasuresForm.COMMUNITY_HHCHAR_LEAVES,
        ],
    )

    columns = []
    for token in disease_order:
        columns.extend(DISEASE_MEASURE_OUTPUT_COLUMNS.get(token, []))
    for token in support_order:
        columns.extend(_support_columns_for_token(token, display_options))
    return list(dict.fromkeys(columns))

def _get_display_options(measures_state: dict, geographic_level=None):
    display_options = []
    for field in sorted(CI_DISPLAY_OPTION_FIELDS | AGE_ADJUST_DISPLAY_OPTION_FIELDS):
        if measures_state.get(field):
            display_options.append(field)
    valid_age_adjusted_field = {
        "county": "access_comm_county_survey_age_adjusted",
        "place": "access_comm_place_survey_age_adjusted",
    }.get(geographic_level)
    display_options = [
        field
        for field in display_options
        if field not in AGE_ADJUST_DISPLAY_OPTION_FIELDS or field == valid_age_adjusted_field
    ]
    return display_options


def _get_community_timeframes(measures_state: dict):
    """Return selected community-characteristics timeframe modes.

    The Measures page defaults to the most recent community data source.
    If the field is missing from an older session, preserve that default.
    """
    selected = _coerce_to_list((measures_state or {}).get("community_timeframes"))
    selected = [x for x in selected if x in {"most_recent", "historical"}]
    return selected or ["most_recent"]


def _session_get(request, key: str, default=None):
    return request.session.get("popcase_wizard", {}).get(key, default)


def _session_set(request, key: str, value):
    wizard = request.session.get("popcase_wizard", {})
    wizard[key] = value
    request.session["popcase_wizard"] = wizard
    request.session.modified = True


def _clean_session_value(value):
    if isinstance(value, dict):
        return {k: _clean_session_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean_session_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_clean_session_value(v) for v in value)
    if isinstance(value, str):
        s = value.strip()
        if s.lower() in {"none", "null"}:
            return None
        return s
    return value


def _build_cancer_type_leaf_choices(leaf_meta: dict):
    def sort_key(k: str):
        m = leaf_meta.get(k, {})
        return (
            (m.get("Sites") or ""),
            (m.get("Site_sub") or ""),
            (m.get("Site_sub_sub") or ""),
        )

    keys = sorted(
        (k for k, meta in leaf_meta.items() if not is_hidden_cancer_type_leaf(meta)),
        key=sort_key,
    )
    return [
        (k, get_cancer_type_leaf_label(leaf_meta.get(k, {})))
        for k in keys
    ]

def _build_cancer_type_labels(selected_leaf_keys):
    if not selected_leaf_keys:
        return []

    _, leaf_meta = get_cancer_type_tree()

    def _pretty_label(k: str) -> str:
        meta = leaf_meta.get(k) or {}
        if not meta:
            return k
        return get_cancer_type_leaf_label(meta)

    return _unique_in_order(_pretty_label(k) for k in selected_leaf_keys)

SEX_SPECIFIC_CANCER_SEX = {
    "cervix uteri": "F",
    "corpus uteri": "F",
    "uterus, nos": "F",
    "uteros, nos": "F",
    "ovary": "F",
    "vagina": "F",
    "vulva": "F",
    "other female genital organs": "F",
    "prostate": "M",
    "testis": "M",
    "penis": "M",
    "other male genital organs": "M",
}


def _get_selected_sex_specific_cancers(selected_leaf_keys):
    if not selected_leaf_keys:
        return []

    _, leaf_meta = get_cancer_type_tree()
    matched = []

    for k in selected_leaf_keys:
        meta = leaf_meta.get(k) or {}

        candidates = [
            (meta.get("Site_sub_sub") or "").strip(),
            (meta.get("Site_sub") or "").strip(),
            (meta.get("Sites") or "").strip(),
        ]

        for label in candidates:
            norm = label.lower()
            if norm in SEX_SPECIFIC_CANCER_SEX:
                matched.append({
                    "label": label,
                    "sex": SEX_SPECIFIC_CANCER_SEX[norm],
                })
                break

    deduped = []
    seen = set()
    for item in matched:
        key = (item["label"].lower(), item["sex"])
        if key not in seen:
            deduped.append(item)
            seen.add(key)

    return deduped


def _wizard_context(request, current_step: str) -> Dict[str, Any]:
    geo = _session_get(request, "geographic_level", "none")
    is_geo_strat = geo not in ("none", "patient")
    is_patient_level = geo == "patient"

    filters_state = _session_get(request, "filters", {}) or {}
    selected_leaf_keys = filters_state.get("cancer_types") or []
    selected_sex_specific_cancers = _get_selected_sex_specific_cancers(selected_leaf_keys)

    _, leaf_meta = get_cancer_type_tree()
    prostate_selected = any(
        (leaf_meta.get(k, {}).get("Site_sub_sub") or "").strip().lower() == "prostate"
        or (leaf_meta.get(k, {}).get("Site_sub") or "").strip().lower() == "prostate"
        for k in selected_leaf_keys
        if k in leaf_meta
    )

    measures_state = _session_get(request, "measures", {}) or {}
    gleason_selected = "gleason" in _coerce_to_list(measures_state.get("disease_measures"))

    return {
        "steps": STEPS,
        "current_step": current_step,
        "geographic_level": geo,
        "is_geo_strat": is_geo_strat,
        "is_patient_level": is_patient_level,
        "prostate_selected": prostate_selected,
        "gleason_selected": gleason_selected,
        "selected_sex_specific_cancers": selected_sex_specific_cancers,
        "has_sex_specific_cancers": bool(selected_sex_specific_cancers),
    }


def home(request):
    if request.user.is_authenticated:
        return redirect("popcase:wizard_step", step="geographic-level")
    return redirect("popcase:login")


@login_required(login_url="popcase:login")
@require_http_methods(["GET", "POST"])
def wizard_step(request, step: str = "geographic-level"):
    if step not in STEPS:
        return redirect("popcase:wizard_step", step="geographic-level")

    form_map = {
        "geographic-level": GeographicLevelForm,
        "filters": FiltersForm,
        "measures": MeasuresForm,
        "stratification": StratificationForm,
    }
    tmpl_map = {
        "geographic-level": "popcase/wizard/geographic_level.html",
        "filters": "popcase/wizard/filters.html",
        "measures": "popcase/wizard/measures.html",
        "stratification": "popcase/wizard/stratification.html",
    }

    FormClass = form_map[step]
    initial = _session_get(request, step, {})

    cancer_tree = leaf_meta = leaf_choices = None
    if step == "filters":
        cancer_tree, leaf_meta = get_cancer_type_tree()
        leaf_choices = _build_cancer_type_leaf_choices(leaf_meta)

    form_kwargs = {"initial": None if request.method == "POST" else initial}
    if step == "filters":
        form_kwargs["geographic_level"] = _normalize_geographic_level(_session_get(request, "geographic_level", "none"))
    if step == "measures":
        form_kwargs["geographic_level"] = _normalize_geographic_level(_session_get(request, "geographic_level", "none"))
    form = FormClass(request.POST or None, **form_kwargs)
    if step == "filters":
        form.fields["cancer_types"].choices = leaf_choices

    if request.method == "POST" and "prev" in request.POST:
        prev_step = STEPS[max(0, STEPS.index(step) - 1)]
        return redirect("popcase:wizard_step", step=prev_step)

    if request.method == "POST" and form.is_valid():
        cleaned_data = _clean_session_value(form.cleaned_data)

        if step == "measures":
            geographic_level = _normalize_geographic_level(_session_get(request, "geographic_level", "county"))

            if geographic_level == "place":
                cleaned_data["community_characteristics"] = [
                    value
                    for value in cleaned_data.get("community_characteristics", [])
                    if value not in ["svi_adi", "rurality"]
                ]

            if geographic_level == "zcta":
                cleaned_data["community_characteristics"] = [
                    value
                    for value in cleaned_data.get("community_characteristics", [])
                    if value != "rurality"
                ]

            cleaned_data["disease_measures"] = _filter_disease_measures_for_geography(
                cleaned_data.get("disease_measures", []),
                geographic_level,
            )

        _session_set(request, step, cleaned_data)

        if step == "geographic-level":
            _session_set(request, "geographic_level", form.cleaned_data.get("geographic_level", "none"))

        if step != STEPS[-1]:
            next_step = STEPS[STEPS.index(step) + 1]
            return redirect("popcase:wizard_step", step=next_step)

        return redirect("popcase:results")

    ctx = _wizard_context(request, step)
    ctx["form"] = form

    if step == "filters":
        ctx["cancer_tree"] = cancer_tree
        ctx["cancer_leaf_meta"] = leaf_meta

    return render(request, tmpl_map[step], ctx)



@login_required(login_url="popcase:login")
def results(request):
    wizard = request.session.get("popcase_wizard", {})
    filters = wizard.get("filters", {}) or {}
    geographic_level = _normalize_geographic_level(wizard.get("geographic_level", "county"))
    measures_state = wizard.get("measures", {}) or {}

    disease_measures, support_measures = _get_measure_selections(measures_state, geographic_level)
    display_options = _get_display_options(measures_state, geographic_level)
    community_timeframes = _get_community_timeframes(measures_state)
    disease_measures = _filter_disease_measures_for_geography(disease_measures, geographic_level)
    year = str(_latest_linking_year())
    default_dx_start, default_dx_end = get_default_diagnosis_quarter_range()
    dx_start = (filters.get("dx_start") or default_dx_start).strip() or default_dx_start
    dx_end = (filters.get("dx_end") or default_dx_end).strip() or default_dx_end
    cancer_type_labels = _build_cancer_type_labels(filters.get("cancer_types") or [])
    age_group_labels = _get_age_group_labels(filters.get("age_groups"), geographic_level)

    if geographic_level == "tract" and set(disease_measures) & {"crude_inc_rate", "crude_inc_ci", "inc_rate", "inc_ci", "crude_mort_rate", "crude_mort_ci", "mort_rate", "mort_ci"}:
        from .incidence_rates import validate_tract_rate_period
        from .rate_statistics import RateDataUnavailable
        try:
            validate_tract_rate_period(dict(filters, dx_start=dx_start, dx_end=dx_end), year)
        except RateDataUnavailable as exc:
            messages.error(request, str(exc))
            return redirect("popcase:wizard_step", step="filters")

    payload = _build_results_payload_cached(
        geographic_level=geographic_level,
        dx_start=dx_start,
        dx_end=dx_end,
        filters_json=_serialize_payload(filters),
        disease_measures_tuple=tuple(sorted(_coerce_to_list(disease_measures))),
        support_measures_tuple=tuple(sorted(_coerce_to_list(support_measures))),
        display_options_tuple=tuple(sorted(_coerce_to_list(display_options))),
        community_timeframes_tuple=tuple(sorted(_coerce_to_list(community_timeframes))),
        latest_year=year,
    )

    incidence = payload["incidence"]
    total_incidence = payload["total_incidence"]
    dataset_rows = payload["dataset_rows"]
    result_mode = payload["result_mode"]

    dataset_preview_limit = _get_preview_row_limit(request)
    dataset_total_rows = len(dataset_rows)
    dataset_is_truncated = dataset_total_rows > dataset_preview_limit
    dataset_rows_preview = dataset_rows[:dataset_preview_limit]

    dynamic_header_map = _with_dynamic_community_headers(DATASET_HEADER_MAP, dataset_rows)
    dynamic_numeric_cols = list(dict.fromkeys(DATASET_NUMERIC_COLS + [
        key
        for row in dataset_rows
        for key, val in row.items()
        if key not in DATASET_NUMERIC_COLS and isinstance(val, (int, float))
    ]))

    preferred_dataset_columns = _build_preferred_dataset_columns(
        disease_measures,
        support_measures,
        display_options,
        geographic_level,
    )
    dataset_columns = _build_dataset_columns(dataset_rows, dynamic_header_map, preferred_dataset_columns)
    dataset_header_rows, dataset_column_classes = _build_dataset_header_rows(
        dataset_columns,
        dynamic_header_map,
        dynamic_numeric_cols,
    )

    context = {
        "wizard_state": wizard,
        "filters": filters,
        "year": year,
        "geographic_level": geographic_level,
        "dx_start": dx_start,
        "dx_end": dx_end,
        "incidence": incidence,
        "total_incidence": total_incidence,
        "dataset_rows": dataset_rows_preview,
        "dataset_total_rows": dataset_total_rows,
        "dataset_preview_limit": dataset_preview_limit,
        "dataset_preview_limit_choices": PREVIEW_ROW_LIMIT_CHOICES,
        "dataset_is_truncated": dataset_is_truncated,
        "result_mode": result_mode,
        "disease_measures": disease_measures,
        "mortality_requested": bool(set(disease_measures) & {"crude_mort_rate", "crude_mort_ci", "mort_rate", "mort_ci"}),
        "display_options": display_options,
        "community_timeframes": community_timeframes,
        "cancer_type_labels": cancer_type_labels,
        "age_group_labels": age_group_labels,
        "dataset_title": f"Selected measures by {geographic_level.title()}",
        "dataset_header_map": dynamic_header_map,
        "dataset_numeric_cols": dynamic_numeric_cols,
        # Backward-compatible context aliases for older templates/custom tags.
        "tract_header_map": dynamic_header_map,
        "tract_numeric_cols": dynamic_numeric_cols,
        "dataset_columns": dataset_columns,
        "dataset_header_rows": dataset_header_rows,
        "dataset_column_classes": dataset_column_classes,
        "dataset_exclude_columns": DATASET_EXCLUDE_COLUMNS,
    }
    return render(request, "popcase/results.html", context)




def _with_dynamic_community_headers(base_header_map, rows):
    """Add readable labels for dynamically suffixed community data columns."""
    header_map = dict(base_header_map)
    if not rows:
        return header_map

    base_labels = dict(base_header_map)
    suffix_re = re.compile(r"^(?P<base>.+)__(?P<src>acs|svi|rucc|ruca)_(?P<period>[0-9_]+)$")
    source_labels = {"acs": "ACS-5", "svi": "SVI", "rucc": "RUCC", "ruca": "RUCA"}

    for row in rows:
        for key in row.keys():
            if key in header_map:
                continue
            m = suffix_re.match(key)
            if not m:
                continue
            base_key = m.group("base")
            source = m.group("src")
            period = m.group("period").replace("_", "-")
            base_label = base_labels.get(base_key, base_key.replace("_", " ").title())
            header_map[key] = f"{base_label} ({source_labels.get(source, source.upper())} {period})"
    return header_map


@login_required(login_url="popcase:login")
def reset_wizard_step(request, step: str):
    """Clear only one wizard page from the session and return to that page.

    This intentionally does not remove the rest of popcase_wizard, so users can
    reset Geography, Filters, Measures, or Stratification independently.
    """
    if step not in STEPS:
        return redirect("popcase:wizard_step", step="geographic-level")

    wizard = request.session.get("popcase_wizard", {}) or {}

    # The geographic-level page stores both the page payload and a convenience
    # key used throughout the wizard context/results logic. Clear both, but do
    # not touch filters, measures, or stratification.
    if step == "geographic-level":
        wizard.pop("geographic-level", None)
        wizard.pop("geographic_level", None)
    else:
        wizard.pop(step, None)

    request.session["popcase_wizard"] = wizard
    request.session.modified = True
    return redirect("popcase:wizard_step", step=step)

@login_required(login_url="popcase:login")
def reset_wizard(request):
    request.session.pop("popcase_wizard", None)
    request.session.modified = True
    return redirect("popcase:wizard_step", step="geographic-level")



@login_required(login_url="popcase:login")
def export_geo_dataset_csv(request):
    wizard = request.session.get("popcase_wizard", {})
    filters = wizard.get("filters", {}) or {}
    geographic_level = _normalize_geographic_level(wizard.get("geographic_level", "county"))
    measures_state = wizard.get("measures", {}) or {}
    disease_measures, support_measures = _get_measure_selections(measures_state, geographic_level)
    display_options = _get_display_options(measures_state, geographic_level)
    community_timeframes = _get_community_timeframes(measures_state)
    disease_measures = _filter_disease_measures_for_geography(disease_measures, geographic_level)

    default_dx_start, default_dx_end = get_default_diagnosis_quarter_range()
    dx_start = (filters.get("dx_start") or default_dx_start).strip() or default_dx_start
    dx_end = (filters.get("dx_end") or default_dx_end).strip() or default_dx_end
    latest_year = str(_latest_linking_year())

    if geographic_level == "total":
        rows = []
        filename = f"popcase_results_total_{dx_start}_{dx_end}.csv"
    else:
        payload = _build_results_payload_cached(
            geographic_level=geographic_level,
            dx_start=dx_start,
            dx_end=dx_end,
            filters_json=_serialize_payload(filters),
            disease_measures_tuple=tuple(sorted(_coerce_to_list(disease_measures))),
            support_measures_tuple=tuple(sorted(_coerce_to_list(support_measures))),
            display_options_tuple=tuple(sorted(_coerce_to_list(display_options))),
            community_timeframes_tuple=tuple(sorted(_coerce_to_list(community_timeframes))),
            latest_year=latest_year,
        )
        rows = payload["dataset_rows"] or []
        filename = f"popcase_results_{geographic_level}_{dx_start}_{dx_end}.csv"

    response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    response["Content-Disposition"] = f"attachment; filename={filename}"
    response.write("\ufeff")

    dynamic_header_map = _with_dynamic_community_headers(DATASET_HEADER_MAP, rows)
    preferred_dataset_columns = _build_preferred_dataset_columns(
        disease_measures,
        support_measures,
        display_options,
        geographic_level,
    )
    columns = _build_dataset_columns(rows, dynamic_header_map, preferred_dataset_columns)
    if not columns:
        columns = ["label"]

    writer = csv.writer(response)
    writer.writerow([dynamic_header_map.get(col, col) for col in columns])

    for row in rows:
        writer.writerow(["" if row.get(col) is None else row.get(col, "") for col in columns])

    return response


def _dataset_sort_type(column, numeric_cols):
    return "number" if column in numeric_cols else "string"


def _dataset_header_cell(column, header_map, numeric_cols, col_index, row_span=1, extra_classes=""):
    classes = ["sortable"]
    if column in numeric_cols:
        classes.append("text-end")
    if column == "label":
        classes.extend(["bg-light", "results-sticky-column"])
    if extra_classes:
        classes.extend(extra_classes.split())

    return {
        "key": column,
        "label": header_map.get(column, column),
        "sort_type": _dataset_sort_type(column, numeric_cols),
        "col_index": col_index,
        "col_span": 1,
        "row_span": row_span,
        "classes": " ".join(dict.fromkeys(classes)),
    }


def _build_dataset_header_rows(columns, header_map, numeric_cols):
    has_groups = any(column in DATASET_COLUMN_GROUPS for column in columns)
    column_classes = {}
    header_rows = {"has_groups": has_groups, "top": [], "leaf": []}
    if not columns:
        return header_rows, column_classes

    if not has_groups:
        header_rows["top"] = [
            _dataset_header_cell(column, header_map, numeric_cols, index)
            for index, column in enumerate(columns)
        ]
        return header_rows, column_classes

    index = 0
    while index < len(columns):
        column = columns[index]
        group_label = DATASET_COLUMN_GROUPS.get(column)

        if not group_label:
            header_rows["top"].append(
                _dataset_header_cell(column, header_map, numeric_cols, index, row_span=2)
            )
            index += 1
            continue

        start_index = index
        grouped_columns = []
        while index < len(columns) and DATASET_COLUMN_GROUPS.get(columns[index]) == group_label:
            grouped_columns.append(columns[index])
            index += 1

        header_rows["top"].append({
            "key": None,
            "label": group_label,
            "col_span": len(grouped_columns),
            "row_span": 1,
            "classes": "results-column-group results-group-start",
        })
        for offset, grouped_column in enumerate(grouped_columns):
            extra_classes = "results-group-start" if offset == 0 else ""
            if offset == 0:
                column_classes[grouped_column] = "results-group-start"
            header_rows["leaf"].append(
                _dataset_header_cell(
                    grouped_column,
                    header_map,
                    numeric_cols,
                    start_index + offset,
                    extra_classes=extra_classes,
                )
            )

    return header_rows, column_classes

def _build_dataset_columns(rows, header_map, preferred_columns=None):
    if not rows:
        return []

    present = set()

    for row in rows:
        present.update(row.keys())

    present -= DATASET_EXCLUDE_COLUMNS
    ordered = []

    def add_column(col):
        if col in DATASET_EXCLUDE_COLUMNS:
            return
        if col in present and col not in ordered:
            ordered.append(col)

    def add_preferred_column(col):
        add_column(col)
        prefix = f"{col}__"
        for candidate in sorted(present):
            if candidate.startswith(prefix) and candidate not in ordered:
                ordered.append(candidate)

    for col in ("label", "geoid", "tract_geoid"):
        add_column(col)

    for col in preferred_columns or []:
        add_preferred_column(col)

    for col in header_map.keys():
        add_preferred_column(col)

    for row in rows:
        for col in row.keys():
            add_column(col)

    return ordered
