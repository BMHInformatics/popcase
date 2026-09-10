from django.db import models

class NaaccrData(models.Model):
    mid = models.TextField(primary_key=True, db_column="Patient ID Number")

    sex = models.TextField(db_column="Sex", null=True, blank=True)
    age_at_dx = models.TextField(db_column="Age at Diagnosis", null=True, blank=True)
    primary_site = models.TextField(db_column="Primary Site", null=True, blank=True)
    hist_o3 = models.TextField(db_column="Histologic Type ICD-O-3", null=True, blank=True)
    race1 = models.TextField(db_column="Race 1", null=True, blank=True)
    hispanic_origin = models.TextField(db_column="Spanish/Hispanic Origin", null=True, blank=True)
    behavior = models.TextField(db_column="Behavior Code ICD-O-3", null=True, blank=True)
    sequence_number = models.TextField(db_column="Sequence Number_Central", null=True, blank=True)
    vital_status = models.TextField(db_column="Vital Status", null=True, blank=True)
    last_contact = models.TextField(db_column="Date of Last Contact", null=True, blank=True)
    last_contact_year = models.TextField(db_column="Date of Last Contact (Year)", null=True, blank=True)
    last_contact_month = models.TextField(db_column="Date of Last Contact (Month)", null=True, blank=True)
    last_contact_day = models.TextField(db_column="Date of Last Contact (Day)", null=True, blank=True)
    birth_date = models.TextField(db_column="Date of Birth", null=True, blank=True)
    cause_of_death = models.TextField(db_column="Cause of Death", null=True, blank=True)
    icd_revision = models.TextField(db_column="ICD Revision Number", null=True, blank=True)
    stg_grp = models.TextField(db_column="Summary Stage 2018", null=True, blank=True)
    dx_year = models.TextField(db_column="Date of Diagnosis (Year)", null=True, blank=True)
    dx_date = models.TextField(db_column="Date of Diagnosis", null=True, blank=True)
    er_summ = models.TextField(db_column="Estrogen Receptor Summary", null=True, blank=True)
    her_summ = models.TextField(db_column="HER2 Overall Summary", null=True, blank=True)
    ssf1 = models.TextField(db_column="CS Site-Specific Factor 1", null=True, blank=True)
    ssf2 = models.TextField(db_column="CS Site-Specific Factor 2", null=True, blank=True)
    ssf3 = models.TextField(db_column="CS Site-Specific Factor 3", null=True, blank=True)
    ssf4 = models.TextField(db_column="CS Site-Specific Factor 4", null=True, blank=True)
    ssf5 = models.TextField(db_column="CS Site-Specific Factor 5", null=True, blank=True)
    ssf6 = models.TextField(db_column="CS Site-Specific Factor 6", null=True, blank=True)
    ssf7 = models.TextField(db_column="CS Site-Specific Factor 7", null=True, blank=True)
    ssf8 = models.TextField(db_column="CS Site-Specific Factor 8", null=True, blank=True)
    ssf9 = models.TextField(db_column="CS Site-Specific Factor 9", null=True, blank=True)
    ssf10 = models.TextField(db_column="CS Site-Specific Factor10", null=True, blank=True)
    ssf11 = models.TextField(db_column="CS Site-Specific Factor11", null=True, blank=True)
    ssf12 = models.TextField(db_column="CS Site-Specific Factor12", null=True, blank=True)
    ssf13 = models.TextField(db_column="CS Site-Specific Factor13", null=True, blank=True)
    ssf14 = models.TextField(db_column="CS Site-Specific Factor14", null=True, blank=True)
    ssf15 = models.TextField(db_column="CS Site-Specific Factor15", null=True, blank=True)
    ssf16 = models.TextField(db_column="CS Site-Specific Factor16", null=True, blank=True)
    ssf17 = models.TextField(db_column="CS Site-Specific Factor17", null=True, blank=True)
    ssf18 = models.TextField(db_column="CS Site-Specific Factor18", null=True, blank=True)
    ssf19 = models.TextField(db_column="CS Site-Specific Factor19", null=True, blank=True)
    ssf20 = models.TextField(db_column="CS Site-Specific Factor20", null=True, blank=True)
    ssf21 = models.TextField(db_column="CS Site-Specific Factor21", null=True, blank=True)
    ssf22 = models.TextField(db_column="CS Site-Specific Factor22", null=True, blank=True)
    ssf23 = models.TextField(db_column="CS Site-Specific Factor23", null=True, blank=True)
    ssf24 = models.TextField(db_column="CS Site-Specific Factor24", null=True, blank=True)
    ssf25 = models.TextField(db_column="CS Site-Specific Factor25", null=True, blank=True)

    class Meta:
        managed = False
        db_table = "naaccr_data"

class NaaccrPatientCensusLinking(models.Model):
    pat_id = models.TextField(db_column="Patient ID Number")
    year = models.TextField(db_column="year")
    geographic_level = models.TextField(db_column="geographic_level")
    geoid = models.TextField(db_column="geoid")
    geo_id = models.TextField(db_column="GEO_ID")

    class Meta:
        managed = False
        db_table = "naaccr_patient_census_linking"


class Acs5YrB01001(models.Model):
    year = models.TextField()
    geographic_level = models.TextField()
    geo_id = models.TextField(db_column="GEO_ID", primary_key=True)
    name = models.TextField(db_column="NAME", null=True, blank=True)

    total_population = models.IntegerField(db_column="B01001_001E", null=True, blank=True)
    total_population_moe = models.IntegerField(db_column="B01001_001M", null=True, blank=True)
    total_male = models.IntegerField(db_column="B01001_002E", null=True, blank=True)
    total_male_moe = models.IntegerField(db_column="B01001_002M", null=True, blank=True)
    total_female = models.IntegerField(db_column="B01001_026E", null=True, blank=True)
    total_female_moe = models.IntegerField(db_column="B01001_026M", null=True, blank=True)

    # Male age bands
    m_under5 = models.IntegerField(db_column="B01001_003E", null=True, blank=True)
    m_5_9 = models.IntegerField(db_column="B01001_004E", null=True, blank=True)
    m_10_14 = models.IntegerField(db_column="B01001_005E", null=True, blank=True)
    m_15_17 = models.IntegerField(db_column="B01001_006E", null=True, blank=True)
    m_18_19 = models.IntegerField(db_column="B01001_007E", null=True, blank=True)
    m_20 = models.IntegerField(db_column="B01001_008E", null=True, blank=True)
    m_21 = models.IntegerField(db_column="B01001_009E", null=True, blank=True)
    m_22_24 = models.IntegerField(db_column="B01001_010E", null=True, blank=True)
    m_25_29 = models.IntegerField(db_column="B01001_011E", null=True, blank=True)
    m_30_34 = models.IntegerField(db_column="B01001_012E", null=True, blank=True)
    m_35_39 = models.IntegerField(db_column="B01001_013E", null=True, blank=True)
    m_40_44 = models.IntegerField(db_column="B01001_014E", null=True, blank=True)
    m_45_49 = models.IntegerField(db_column="B01001_015E", null=True, blank=True)
    m_50_54 = models.IntegerField(db_column="B01001_016E", null=True, blank=True)
    m_55_59 = models.IntegerField(db_column="B01001_017E", null=True, blank=True)
    m_60_61 = models.IntegerField(db_column="B01001_018E", null=True, blank=True)
    m_62_64 = models.IntegerField(db_column="B01001_019E", null=True, blank=True)
    m_65_66 = models.IntegerField(db_column="B01001_020E", null=True, blank=True)
    m_67_69 = models.IntegerField(db_column="B01001_021E", null=True, blank=True)
    m_70_74 = models.IntegerField(db_column="B01001_022E", null=True, blank=True)
    m_75_79 = models.IntegerField(db_column="B01001_023E", null=True, blank=True)
    m_80_84 = models.IntegerField(db_column="B01001_024E", null=True, blank=True)
    m_85_plus = models.IntegerField(db_column="B01001_025E", null=True, blank=True)

    # Female age bands
    f_under5 = models.IntegerField(db_column="B01001_027E", null=True, blank=True)
    f_5_9 = models.IntegerField(db_column="B01001_028E", null=True, blank=True)
    f_10_14 = models.IntegerField(db_column="B01001_029E", null=True, blank=True)
    f_15_17 = models.IntegerField(db_column="B01001_030E", null=True, blank=True)
    f_18_19 = models.IntegerField(db_column="B01001_031E", null=True, blank=True)
    f_20 = models.IntegerField(db_column="B01001_032E", null=True, blank=True)
    f_21 = models.IntegerField(db_column="B01001_033E", null=True, blank=True)
    f_22_24 = models.IntegerField(db_column="B01001_034E", null=True, blank=True)
    f_25_29 = models.IntegerField(db_column="B01001_035E", null=True, blank=True)
    f_30_34 = models.IntegerField(db_column="B01001_036E", null=True, blank=True)
    f_35_39 = models.IntegerField(db_column="B01001_037E", null=True, blank=True)
    f_40_44 = models.IntegerField(db_column="B01001_038E", null=True, blank=True)
    f_45_49 = models.IntegerField(db_column="B01001_039E", null=True, blank=True)
    f_50_54 = models.IntegerField(db_column="B01001_040E", null=True, blank=True)
    f_55_59 = models.IntegerField(db_column="B01001_041E", null=True, blank=True)
    f_60_61 = models.IntegerField(db_column="B01001_042E", null=True, blank=True)
    f_62_64 = models.IntegerField(db_column="B01001_043E", null=True, blank=True)
    f_65_66 = models.IntegerField(db_column="B01001_044E", null=True, blank=True)
    f_67_69 = models.IntegerField(db_column="B01001_045E", null=True, blank=True)
    f_70_74 = models.IntegerField(db_column="B01001_046E", null=True, blank=True)
    f_75_79 = models.IntegerField(db_column="B01001_047E", null=True, blank=True)
    f_80_84 = models.IntegerField(db_column="B01001_048E", null=True, blank=True)
    f_85_plus = models.IntegerField(db_column="B01001_049E", null=True, blank=True)

    class Meta:
        managed = False
        db_table = "acs_5yr_B01001"


class TigerCounty(models.Model):
    geoid = models.TextField(primary_key=True)
    name = models.TextField()
    namelsad = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "tiger_county_shapefiles"

    def __str__(self):
        return f"{self.year} | {self.geoid} | {self.name}"


class UIStage(models.Model):
    codes = models.TextField()
    description = models.TextField()
    uiname = models.TextField()
    truevalue = models.TextField()

    class Meta:
        managed = False
        db_table = "ui_stage"


class UIPsite(models.Model):
    codes = models.TextField()
    description = models.TextField()
    uiname = models.TextField()
    truevalue = models.TextField()

    class Meta:
        managed = False
        db_table = "ui_psite"


class UISex(models.Model):
    codes = models.TextField()
    description = models.TextField()
    uiname = models.TextField()
    truevalue = models.TextField()

    class Meta:
        managed = False
        db_table = "ui_sex"


class UIRace(models.Model):
    codes = models.TextField()
    description = models.TextField()
    uiname = models.TextField()
    truevalue = models.TextField()

    class Meta:
        managed = False
        db_table = "ui_race"


class UIDxagegroup(models.Model):
    codes = models.TextField()
    description = models.TextField()
    uiname = models.TextField()
    truevalue = models.TextField()

    class Meta:
        managed = False
        db_table = "ui_dxagegroup"


class AcsB19013(models.Model):
    geo_id = models.TextField(primary_key=True, db_column="GEO_ID")
    median_household_income = models.IntegerField(
        db_column="B19013_001E", null=True
    )
    median_household_income_moe = models.IntegerField(
        db_column="B19013_001M", null=True
    )

    class Meta:
        managed = False
        db_table = "acs_5yr_B19013"


class AcsC16001(models.Model):
    geo_id = models.TextField(primary_key=True, db_column="GEO_ID")
    total_population_5plus = models.IntegerField(
        db_column="C16001_001E", null=True
    )
    total_population_5plus_moe = models.IntegerField(
        db_column="C16001_001M", null=True
    )
    limited_english = models.IntegerField(
        db_column="C16001_004E", null=True
    )
    limited_english_moe = models.IntegerField(
        db_column="C16001_004M", null=True
    )

    class Meta:
        managed = False
        db_table = "acs_5yr_C16001"


class TravelTimeTract(models.Model):
    tract_geoid = models.TextField(primary_key=True, db_column="geoid")
    count_x = models.FloatField(null=True, db_column="count.x")
    car_wt = models.FloatField(null=True, db_column="car_wt")
    weighted_sa_car = models.FloatField(null=True, db_column="weighted_SA_car")
    count_y = models.FloatField(null=True, db_column="count.y")
    walk_wt = models.FloatField(null=True, db_column="walk_wt")
    weighted_sa_walk = models.FloatField(null=True, db_column="weighted_SA_walk")
    count = models.FloatField(null=True, db_column="count")
    transit_wt = models.FloatField(null=True, db_column="transit_wt")
    weighted_sa_transit = models.FloatField(null=True, db_column="weighted_SA_transit")
    weighted_sa_final = models.FloatField(null=True, db_column="weighted_SA_final")
    source_file = models.TextField(null=True, blank=True)
    id = models.BigIntegerField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "travel_tract_2020"


class CDCPlacesTract2024(models.Model):
    tract_fips = models.TextField(db_column="TractFIPS", primary_key=True)

    mammography_screening = models.FloatField(db_column="MAMMOUSE_CrudePrev", null=True)
    mammography_screening_ci = models.TextField(db_column="MAMMOUSE_Crude95CI", null=True, blank=True)
    lack_transportation = models.FloatField(db_column="LACKTRPT_CrudePrev", null=True)
    lack_transportation_ci = models.TextField(db_column="LACKTRPT_Crude95CI", null=True, blank=True)
    routine_checkup = models.FloatField(db_column="CHECKUP_CrudePrev", null=True)
    routine_checkup_ci = models.TextField(db_column="CHECKUP_Crude95CI", null=True, blank=True)
    uninsured = models.FloatField(db_column="ACCESS2_CrudePrev", null=True)
    uninsured_ci = models.TextField(db_column="ACCESS2_Crude95CI", null=True, blank=True)

    smoking = models.FloatField(db_column="CSMOKING_CrudePrev", null=True)
    smoking_ci = models.TextField(db_column="CSMOKING_Crude95CI", null=True, blank=True)
    obesity = models.FloatField(db_column="OBESITY_CrudePrev", null=True)
    obesity_ci = models.TextField(db_column="OBESITY_Crude95CI", null=True, blank=True)
    binge_drinking = models.FloatField(db_column="BINGE_CrudePrev", null=True)
    binge_drinking_ci = models.TextField(db_column="BINGE_Crude95CI", null=True, blank=True)
    no_leisure_pa = models.FloatField(db_column="LPA_CrudePrev", null=True)
    no_leisure_pa_ci = models.TextField(db_column="LPA_Crude95CI", null=True, blank=True)
    short_sleep = models.FloatField(db_column="SLEEP_CrudePrev", null=True)
    short_sleep_ci = models.TextField(db_column="SLEEP_Crude95CI", null=True, blank=True)

    colorectal_screening = models.FloatField(db_column="COLON_SCREEN_CrudePrev", null=True)
    colorectal_screening_ci = models.TextField(db_column="COLON_SCREEN_Crude95CI", null=True, blank=True)
    dental = models.FloatField(db_column="DENTAL_CrudePrev", null=True)
    dental_ci = models.TextField(db_column="DENTAL_Crude95CI", null=True, blank=True)

    poor_health = models.FloatField(db_column="GHLTH_CrudePrev", null=True)
    poor_health_ci = models.TextField(db_column="GHLTH_Crude95CI", null=True, blank=True)
    physical_distress = models.FloatField(db_column="PHLTH_CrudePrev", null=True)
    physical_distress_ci = models.TextField(db_column="PHLTH_Crude95CI", null=True, blank=True)
    mental_distress = models.FloatField(db_column="MHLTH_CrudePrev", null=True)
    mental_distress_ci = models.TextField(db_column="MHLTH_Crude95CI", null=True, blank=True)
    food_insecurity = models.FloatField(db_column="FOODINSECU_CrudePrev", null=True)
    food_insecurity_ci = models.TextField(db_column="FOODINSECU_Crude95CI", null=True, blank=True)
    social_isolation = models.FloatField(db_column="ISOLATION_CrudePrev", null=True)
    social_isolation_ci = models.TextField(db_column="ISOLATION_Crude95CI", null=True, blank=True)
    any_disability = models.FloatField(db_column="DISABILITY_CrudePrev", null=True)
    any_disability_ci = models.TextField(db_column="DISABILITY_Crude95CI", null=True, blank=True)
    mobility_disability = models.FloatField(db_column="MOBILITY_CrudePrev", null=True)
    mobility_disability_ci = models.TextField(db_column="MOBILITY_Crude95CI", null=True, blank=True)
    selfcare_disability = models.FloatField(db_column="SELFCARE_CrudePrev", null=True)
    selfcare_disability_ci = models.TextField(db_column="SELFCARE_Crude95CI", null=True, blank=True)
    independent_living_disability = models.FloatField(db_column="INDEPLIVE_CrudePrev", null=True)
    independent_living_disability_ci = models.TextField(db_column="INDEPLIVE_Crude95CI", null=True, blank=True)

    class Meta:
        managed = False
        db_table = "cdc_places_tract_data_2024"


class CDCPlacesCounty2024(models.Model):
    county_fips = models.TextField(db_column="CountyFIPS", primary_key=True)

    class Meta:
        managed = False
        db_table = "cdc_places_county_data_2024"


class CDCPlacesZCTA2024(models.Model):
    zcta = models.TextField(db_column="ZCTA5", primary_key=True)

    class Meta:
        managed = False
        db_table = "cdc_places_zcta_data_2024"


class CDCPlacesPlace2024(models.Model):
    place_fips = models.TextField(db_column="PlaceFIPS", primary_key=True)

    class Meta:
        managed = False
        db_table = "cdc_places_place_data_2024"

class UICounty(models.Model):
    geoid = models.TextField(primary_key=True)
    name = models.TextField()

    class Meta:
        managed = False
        db_table = "popcaseui.ui_county"


class TigerTractShapefile(models.Model):
    year = models.TextField()
    statefp = models.TextField()
    countyfp = models.TextField()
    tractce = models.TextField()
    geoid = models.TextField(primary_key=True)
    name = models.TextField(null=True, blank=True)
    namelsad = models.TextField(null=True, blank=True)
    mtfcc = models.TextField(null=True, blank=True)
    funcstat = models.TextField(null=True, blank=True)
    aland = models.BigIntegerField(null=True, blank=True)
    awater = models.BigIntegerField(null=True, blank=True)
    intptlat = models.TextField(null=True, blank=True)
    intptlon = models.TextField(null=True, blank=True)
    geometry = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "tiger_tract_shapefiles"


class FdaMammographyFacility(models.Model):
    facility_name = models.TextField(db_column="Facility_Name", null=True, blank=True)
    address1 = models.TextField(db_column="Address1", null=True, blank=True)
    city = models.TextField(db_column="city", null=True, blank=True)
    state = models.TextField(db_column="state", null=True, blank=True)
    latlong = models.TextField(db_column="latlong", null=True, blank=True)
    comma = models.TextField(db_column="comma", null=True, blank=True)
    lat = models.FloatField(db_column="lat", null=True, blank=True)
    long = models.FloatField(db_column="long", null=True, blank=True)
    geom = models.TextField(db_column="geom", null=True, blank=True)
    id = models.BigIntegerField(primary_key=True, db_column="id")

    class Meta:
        managed = False
        db_table = "fda_mammography_facilities"
