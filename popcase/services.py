import re
import csv
import math
import ast
import json
from math import sqrt
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from collections import defaultdict
from typing import Iterable, Tuple, Union

from django.db.models import Count, Q
from django.db.models.functions import Cast
from django.db.models import IntegerField
from django.db import connection, connections

from popcase.models import (
    NaaccrData,
    NaaccrPatientCensusLinking,
    Acs5YrB01001,
    AcsB19013,
    AcsC16001,
    TravelTimeTract,
    CDCPlacesTract2024,
    CDCPlacesCounty2024,
    CDCPlacesZCTA2024,
    CDCPlacesPlace2024,
    TigerTractShapefile,
    FdaMammographyFacility,
)

# ---------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------

OHIO_COUNTY_NAMES = {
    "39001": "Adams", "39003": "Allen", "39005": "Ashland", "39007": "Ashtabula",
    "39009": "Athens", "39011": "Auglaize", "39013": "Belmont", "39015": "Brown",
    "39017": "Butler", "39019": "Carroll", "39021": "Champaign", "39023": "Clark",
    "39025": "Clermont", "39027": "Clinton", "39029": "Columbiana", "39031": "Coshocton",
    "39033": "Crawford", "39035": "Cuyahoga", "39037": "Darke", "39039": "Defiance",
    "39041": "Delaware", "39043": "Erie", "39045": "Fairfield", "39047": "Fayette",
    "39049": "Franklin", "39051": "Fulton", "39053": "Gallia", "39055": "Geauga",
    "39057": "Greene", "39059": "Guernsey", "39061": "Hamilton", "39063": "Hancock",
    "39065": "Hardin", "39067": "Harrison", "39069": "Henry", "39071": "Highland",
    "39073": "Hocking", "39075": "Holmes", "39077": "Huron", "39079": "Jackson",
    "39081": "Jefferson", "39083": "Knox", "39085": "Lake", "39087": "Lawrence",
    "39089": "Licking", "39091": "Logan", "39093": "Lorain", "39095": "Lucas",
    "39097": "Madison", "39099": "Mahoning", "39101": "Marion", "39103": "Medina",
    "39105": "Meigs", "39107": "Mercer", "39109": "Miami", "39111": "Monroe",
    "39113": "Montgomery", "39115": "Morgan", "39117": "Morrow", "39119": "Muskingum",
    "39121": "Noble", "39123": "Ottawa", "39125": "Paulding", "39127": "Perry",
    "39129": "Pickaway", "39131": "Pike", "39133": "Portage", "39135": "Preble",
    "39137": "Putnam", "39139": "Richland", "39141": "Ross", "39143": "Sandusky",
    "39145": "Scioto", "39147": "Seneca", "39149": "Shelby", "39151": "Stark",
    "39153": "Summit", "39155": "Trumbull", "39157": "Tuscarawas", "39159": "Union",
    "39161": "Van Wert", "39163": "Vinton", "39165": "Warren", "39167": "Washington",
    "39169": "Wayne", "39171": "Williams", "39173": "Wood", "39175": "Wyandot",
}

SEX_LABEL_TO_CODE = {
    "male": "1",
    "m": "1",
    "female": "2",
    "f": "2",
}

SEX_FILTER_TO_B01001_TOTAL_FIELD = {
    "male": "total_male",
    "female": "total_female",
}

COLLAPSED_US2000_STD_WEIGHTS = {
    "00_04": 13818 + 55317,
    "05_09": 72533,
    "10_14": 73032,
    "15_19": 72169,
    "20_24": 66478,
    "25_29": 64529,
    "30_34": 71052,
    "35_39": 80762,
    "40_44": 88124,
    "45_49": 86379,
    "50_54": 72179,
    "55_59": 62716,
    "60_64": 48454,
    "65_69": 38793,
    "70_74": 28728,
    "75_79": 18565,
    "80_84": 11631,
    "85+": 15508,
}

# ---------------------------------------------------------
# US 2000 STANDARD POPULATION WEIGHTS (19 groups)
# 00–17 as-is, 18+19 combined into 85+
# Values per 1,000,000
# ---------------------------------------------------------

US2000_STD_WEIGHTS = {
    "00": 13818,
    "01": 55317,
    "02": 72533,
    "03": 73032,
    "04": 72169,
    "05": 66478,
    "06": 64529,
    "07": 71052,
    "08": 80762,
    "09": 88124,
    "10": 86379,
    "11": 72179,
    "12": 62716,
    "13": 48454,
    "14": 38793,
    "15": 28728,
    "16": 18565,
    "17": 11631,
    "85+": 15508,
}

# ---------------------------------------------------------
# NORTHEAST OHIO (15-county) catchment scope
# ---------------------------------------------------------
NEO_15_COUNTY_GEOIDS = {
    "39007", "39019", "39029", "39035", "39055",
    "39085", "39093", "39099", "39103", "39133",
    "39151", "39153", "39155", "39157", "39169",
}

# ---------------------------------------------------------
# CANCER TYPE TREE (3-tier UI)
# ---------------------------------------------------------


def _is_neo15_scope(filters):
    geo_scope = (filters.get("geography") or "all_ohio").strip().lower()
    return geo_scope in ("neo15", "neo_15", "catchment15", "catchment_15")


def _geoid_in_scope(geographic_level: str, geoid: str, filters: dict) -> bool:
    if not _is_neo15_scope(filters):
        return True

    if not geoid:
        return False

    g = str(geoid).strip()

    if geographic_level == "county":
        return g in NEO_15_COUNTY_GEOIDS

    if geographic_level == "tract":
        return len(g) >= 5 and g[:5] in NEO_15_COUNTY_GEOIDS

    # No county crosswalk implemented here for ZCTA/place yet,
    # so leave them unchanged for now.
    return True


def _filter_lookup_to_scope(lookup: dict, geographic_level: str, filters: dict) -> dict:
    if not lookup:
        return {}
    return {
        k: v
        for k, v in lookup.items()
        if _geoid_in_scope(geographic_level, k, filters)
    }


def _safe_strip(x):
    return (x or "").strip()


def _normalize_requested_sex(filters: dict):
    sex = (filters.get("sex") or "").strip().lower()
    return sex if sex in ("male", "female") else None


def _normalize_geoid_for_level_from_geo_id(geo_id, geographic_level):
    if not geo_id:
        return None
    s = str(geo_id).strip()
    if geographic_level == "county":
        return s[-5:]
    if geographic_level == "tract":
        return s[-11:]
    if geographic_level == "zcta":
        return s[-5:]
    if geographic_level == "state":
        return s[-2:]
    return s


def _normalize_geoid_for_level_value(geoid, geographic_level):
    if geoid is None:
        return None
    s = str(geoid).strip()
    if geographic_level == "zcta":
        return s[-5:]
    return s


def _sex_specific_cancer_sex_from_filters(filters: dict):
    selected = filters.get("cancer_types") or []
    if not selected:
        return None

    _, leaf_meta = load_cancer_logic()

    sex_specific_map = {
        "cervix uteri": "female",
        "corpus uteri": "female",
        "uteros, nos": "female",
        "uterus, nos": "female",
        "ovary": "female",
        "vagina": "female",
        "vulva": "female",
        "other female genital organs": "female",
        "prostate": "male",
        "testis": "male",
        "penis": "male",
        "other male genital organs": "male",
    }

    found = set()

    for leaf_key in selected:
        meta = leaf_meta.get(leaf_key) or {}
        labels = [
            (meta.get("Site_sub_sub") or "").strip().lower(),
            (meta.get("Site_sub") or "").strip().lower(),
            (meta.get("Sites") or "").strip().lower(),
        ]
        for label in labels:
            if label in sex_specific_map:
                found.add(sex_specific_map[label])
                break

    if len(found) == 1:
        return next(iter(found))
    return None


def _should_use_sex_specific_denominator(filters: dict):
    requested_sex = _normalize_requested_sex(filters)
    cancer_required_sex = _sex_specific_cancer_sex_from_filters(filters)

    if requested_sex and cancer_required_sex and requested_sex == cancer_required_sex:
        return requested_sex
    return None


def _population_total_field_for_incidence(filters: dict):
    sex_specific = _should_use_sex_specific_denominator(filters)
    if sex_specific in SEX_FILTER_TO_B01001_TOTAL_FIELD:
        return SEX_FILTER_TO_B01001_TOTAL_FIELD[sex_specific]
    return "total_population"


def _collapsed_age_bin(age):
    if age is None:
        return None
    age = int(age)

    if 0 <= age <= 4:
        return "00_04"
    if 5 <= age <= 9:
        return "05_09"
    if 10 <= age <= 14:
        return "10_14"
    if 15 <= age <= 19:
        return "15_19"
    if 20 <= age <= 24:
        return "20_24"
    if 25 <= age <= 29:
        return "25_29"
    if 30 <= age <= 34:
        return "30_34"
    if 35 <= age <= 39:
        return "35_39"
    if 40 <= age <= 44:
        return "40_44"
    if 45 <= age <= 49:
        return "45_49"
    if 50 <= age <= 54:
        return "50_54"
    if 55 <= age <= 59:
        return "55_59"
    if 60 <= age <= 64:
        return "60_64"
    if 65 <= age <= 69:
        return "65_69"
    if 70 <= age <= 74:
        return "70_74"
    if 75 <= age <= 79:
        return "75_79"
    if 80 <= age <= 84:
        return "80_84"
    if age >= 85:
        return "85+"

    return None


def _sum_row_fields(row, field_names):
    total = 0.0
    for f in field_names:
        total += float(row.get(f) or 0)
    return total


def _get_sex_specific_collapsed_population_lookup(year, geographic_level, sex):
    prefix = "m_" if sex == "male" else "f_"

    age_field_map = {
        "00_04": [f"{prefix}under5"],
        "05_09": [f"{prefix}5_9"],
        "10_14": [f"{prefix}10_14"],
        "15_19": [f"{prefix}15_17", f"{prefix}18_19"],
        "20_24": [f"{prefix}20", f"{prefix}21", f"{prefix}22_24"],
        "25_29": [f"{prefix}25_29"],
        "30_34": [f"{prefix}30_34"],
        "35_39": [f"{prefix}35_39"],
        "40_44": [f"{prefix}40_44"],
        "45_49": [f"{prefix}45_49"],
        "50_54": [f"{prefix}50_54"],
        "55_59": [f"{prefix}55_59"],
        "60_64": [f"{prefix}60_61", f"{prefix}62_64"],
        "65_69": [f"{prefix}65_66", f"{prefix}67_69"],
        "70_74": [f"{prefix}70_74"],
        "75_79": [f"{prefix}75_79"],
        "80_84": [f"{prefix}80_84"],
        "85+": [f"{prefix}85_plus"],
    }

    needed_fields = ["geo_id"]
    for fields in age_field_map.values():
        needed_fields.extend(fields)

    lookup = {}
    for row in (
        Acs5YrB01001.objects
        .filter(year=str(year), geographic_level=geographic_level)
        .values(*needed_fields)
        .iterator(chunk_size=5000)
    ):
        geoid = _normalize_geoid_for_level_from_geo_id(row["geo_id"], geographic_level)
        if not geoid:
            continue

        lookup[geoid] = {
            age_bin: _sum_row_fields(row, fields)
            for age_bin, fields in age_field_map.items()
        }

    return lookup


def _compute_sex_specific_age_adjusted_ci_by_geo(year, geographic_level, filtered_pat_ids, sex):
    if not filtered_pat_ids:
        return {}

    with connection.cursor() as cur:
        cur.execute(f"""
            SELECT
                l.geoid,
                d."Age at Diagnosis"::int AS age_dx
            FROM naaccr_data d
            JOIN naaccr_patient_census_linking l
                ON d."Patient ID Number" = l."Patient ID Number"
            WHERE l.year = %s
              AND l.geographic_level = %s
              AND l."Patient ID Number" IN ({",".join(["%s"] * len(filtered_pat_ids))})
        """, [str(year), geographic_level] + filtered_pat_ids)
        rows = cur.fetchall()

    case_lookup = defaultdict(lambda: defaultdict(int))
    for geoid_raw, age_dx in rows:
        geoid = _normalize_geoid_for_level_value(geoid_raw, geographic_level)
        age_bin = _collapsed_age_bin(age_dx)
        if not geoid or not age_bin:
            continue
        case_lookup[geoid][age_bin] += 1

    pop_lookup = _get_sex_specific_collapsed_population_lookup(year, geographic_level, sex)

    out = {}
    scale = 100000.0 / 1_000_000.0

    for geoid, age_cases in case_lookup.items():
        total = 0.0
        var_sum = 0.0

        for age_bin, weight in COLLAPSED_US2000_STD_WEIGHTS.items():
            pop = pop_lookup.get(geoid, {}).get(age_bin)
            d = age_cases.get(age_bin, 0)

            if pop and pop > 0:
                total += weight * (d / pop)
                var_sum += (weight ** 2) * (d / (pop ** 2))

        rate = total * scale
        se = math.sqrt(var_sum) * scale if var_sum > 0 else 0.0

        if total > 0:
            lo = max(0.0, rate - 1.96 * se)
            hi = rate + 1.96 * se
            out[geoid] = (round(rate, 1), round(lo, 1), round(hi, 1))
        else:
            out[geoid] = (None, None, None)

    return out

# =========================================================
# 1️⃣ Load Structured Cancer Logic CSV
# =========================================================
@lru_cache(maxsize=1)
def load_cancer_logic():
    """
    Loads structured cancer logic from CSV.

    CSV Columns Required:
        Sites
        Site_sub
        Site_sub_sub
        psite_include
        psite_exclude
        hist_include
        hist_exclude
        dx_year
        er
        her2
        ssf16
    """
    csv_path = Path(__file__).resolve().parent / "cancer_site_logic.csv"
    if not csv_path.exists():
        return [], {}

    rows = []
    tree = {}
    leaf_meta = {}

    with csv_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f)

        for row in reader:
            sites = (row.get("Sites") or "").strip()
            sub = (row.get("Site_sub") or "").strip()
            subsub = (row.get("Site_sub_sub") or "").strip()

            if not sites or not sub:
                continue

            leaf_key = "|||".join([
                sites.strip(),
                sub.strip(),
                (subsub or "").strip()
            ])
            leaf_label = subsub if subsub else sub

            leaf_meta[leaf_key] = row

            tree.setdefault(sites, {}).setdefault(sub, {}).setdefault(subsub, []).append(
                (leaf_key, leaf_label)
            )

            rows.append(row)

    return tree, leaf_meta


# =========================================================
# 2️⃣ Cancer Logic Engine
# =========================================================

def apply_cancer_logic(base_qs, logic_row):
    qs = base_qs
    qs = _apply_psite_include(qs, logic_row.get("psite_include"))
    qs = _apply_psite_exclude(qs, logic_row.get("psite_exclude"))
    qs = _apply_hist_include(qs, logic_row.get("hist_include"))
    qs = _apply_hist_exclude(qs, logic_row.get("hist_exclude"))
    qs = _apply_dx_year(qs, logic_row.get("dx_year"))
    qs = _apply_er(qs, logic_row.get("er"))
    qs = _apply_her2(qs, logic_row.get("her2"))
    qs = _apply_ssf16(qs, logic_row.get("ssf16"))
    return qs


def get_cancer_type_tree():
    tree, leaf_meta = load_cancer_logic()
    formatted_tree = {}

    for leaf_key, meta in leaf_meta.items():
        sites = (meta.get("Sites") or "").strip()
        sub = (meta.get("Site_sub") or "").strip()
        subsub = (meta.get("Site_sub_sub") or "").strip()

        if not sites or not sub:
            continue

        formatted_tree.setdefault(sites, {}).setdefault(sub, {}).setdefault(subsub, []).append(
            (leaf_key, subsub if subsub else sub)
        )

    final_tree = {}
    for sites, subs in formatted_tree.items():
        final_tree[sites] = {}
        for sub, children in subs.items():
            has_subsub = any(k != "" for k in children.keys())
            final_tree[sites][sub] = {
                "has_subsub": has_subsub,
                "children": children,
            }

    return final_tree, leaf_meta


# =========================================================
# 3️⃣ PSITE LOGIC
# =========================================================

def _apply_psite_include(qs, logic):
    if not logic:
        return qs

    data = _safe_literal_eval(logic)
    if not data:
        return qs

    q = Q()
    for code in data.get("in", []):
        q |= Q(primary_site=code)

    for a, b in data.get("between", []):
        q |= Q(primary_site__gte=a, primary_site__lte=b)

    return qs.filter(q)


def _apply_psite_exclude(qs, logic):
    if not logic:
        return qs

    data = _safe_literal_eval(logic)
    if not data:
        return qs

    for code in data.get("in", []):
        qs = qs.exclude(primary_site=code)

    for a, b in data.get("between", []):
        qs = qs.exclude(primary_site__gte=a, primary_site__lte=b)

    return qs


# =========================================================
# 4️⃣ HISTOLOGY LOGIC
# =========================================================

def _apply_hist_include(qs, logic):
    if not logic:
        return qs

    data = _safe_literal_eval(logic)
    if not data:
        return qs

    q = Q()
    for code in data.get("in", []):
        q |= Q(hist_o3=code)

    for a, b in data.get("between", []):
        q |= Q(hist_o3__gte=a, hist_o3__lte=b)

    return qs.filter(q)


def _apply_hist_exclude(qs, logic):
    if not logic:
        return qs

    data = _safe_literal_eval(logic)
    if not data:
        return qs

    for code in data.get("in", []):
        qs = qs.exclude(hist_o3=code)

    for a, b in data.get("between", []):
        qs = qs.exclude(hist_o3__gte=a, hist_o3__lte=b)

    return qs


# =========================================================
# 5️⃣ DX YEAR LOGIC
# =========================================================

def _apply_dx_year(qs, logic):
    if not logic:
        return qs

    data = _safe_literal_eval(logic)
    if not data:
        return qs

    if "lt" in data:
        qs = qs.filter(dx_year__lt=str(data["lt"]))
    if "lte" in data:
        qs = qs.filter(dx_year__lte=str(data["lte"]))
    if "gt" in data:
        qs = qs.filter(dx_year__gt=str(data["gt"]))
    if "gte" in data:
        qs = qs.filter(dx_year__gte=str(data["gte"]))

    return qs


# =========================================================
# 6️⃣ ER / HER2 / SSF LOGIC
# =========================================================

def _apply_er(qs, logic):
    if not logic:
        return qs
    data = _safe_literal_eval(logic)
    if not data:
        return qs
    return qs.filter(er_summ__in=data.get("in", []))


def _apply_her2(qs, logic):
    if not logic:
        return qs
    data = _safe_literal_eval(logic)
    if not data:
        return qs
    return qs.filter(her_summ__in=data.get("in", []))


def _apply_ssf16(qs, logic):
    if not logic:
        return qs
    data = _safe_literal_eval(logic)
    if not data:
        return qs
    return qs.filter(ssf16__in=data.get("in", []))


# ---------------------------------------------------------
# FILTERS
# ---------------------------------------------------------

def apply_naaccr_filters(qs, filters: dict):
    if not filters:
        return qs

    sex = (
        filters.get("sex")
        or filters.get("Sex")
        or filters.get("sex_filter")
        or filters.get("sex_selection")
    )
    if sex and str(sex).strip().lower() not in ("all", ""):
        s = str(sex).strip()
        s_norm = s.lower()
        if s_norm in SEX_LABEL_TO_CODE:
            sex_code = SEX_LABEL_TO_CODE[s_norm]
        elif s in ("1", "2"):
            sex_code = s
        else:
            sex_code = s
        qs = qs.filter(sex=sex_code)

    age_from = filters.get("age_from")
    age_to = filters.get("age_to")
    if age_from is not None or age_to is not None:
        qs = qs.annotate(age_dx_int=Cast("age_at_dx", IntegerField()))
        if age_from not in (None, ""):
            qs = qs.filter(age_dx_int__gte=int(age_from))
        if age_to not in (None, ""):
            qs = qs.filter(age_dx_int__lte=int(age_to))

    dx_start = (filters.get("dx_start") or "").strip()
    dx_end = (filters.get("dx_end") or "").strip()

    if dx_start:
        qs = qs.filter(dx_year__gte=dx_start)
    if dx_end:
        qs = qs.filter(dx_year__lte=dx_end)

    geo_scope = (filters.get("geography") or "all_ohio").strip().lower()
    if geo_scope in ("neo15", "neo_15", "catchment15", "catchment_15"):
        neo_pat_ids = (
            NaaccrPatientCensusLinking.objects
            .filter(geographic_level="county", geoid__in=NEO_15_COUNTY_GEOIDS)
            .values_list("pat_id", flat=True)
            .distinct()
        )
        qs = qs.filter(mid__in=neo_pat_ids)

    race_val = (
        filters.get("race")
        or filters.get("race_ethnicity")
        or filters.get("Race")
        or filters.get("race_filter")
        or filters.get("race_selection")
    )
    if race_val is None:
        race_tokens = []
    elif isinstance(race_val, (list, tuple, set)):
        race_tokens = [str(x).strip() for x in race_val if str(x).strip()]
    else:
        race_tokens = [str(race_val).strip()] if str(race_val).strip() else []
    race_tokens = [t for t in race_tokens if t.lower() != "all"]

    UI_RACE_TOKEN_TO_CODES = {
        "nh_white": ["01"],
        "nh_black": ["02"],
        "nh_aian": ["03"],
        "nh_asian": ["04", "05", "06", "07", "08", "10", "15", "16"],
        "nh_nhpi": ["09", "12", "13", "14"],
        "nh_other": ["96"],
        "unknown": ["98", "99"],
        "nh_unknown": ["98", "99"],
    }
    LABEL_TO_CODES = {
        "White": ["01"],
        "Black": ["02"],
        "American Indian": ["03"],
        "Asian": ["04", "05", "06", "07", "08", "10", "15", "16", "96"],
        "Other": ["96"],
        "Unknown": ["98", "99"],
    }

    if race_tokens:
        codes = []
        for tok in race_tokens:
            codes.extend(UI_RACE_TOKEN_TO_CODES.get(tok, []))
            codes.extend(LABEL_TO_CODES.get(tok, []))
        codes = sorted(set(codes))
        if codes:
            qs = qs.filter(race1__in=codes)

    selected = filters.get("cancer_types") or []
    if selected:
        _, leaf_meta = load_cancer_logic()
        cancer_qs = qs.none()

        for leaf_key in selected:
            meta = leaf_meta.get(leaf_key)
            if not meta:
                continue
            filtered = apply_cancer_logic(qs, meta)
            cancer_qs = cancer_qs.union(filtered)

        qs = cancer_qs

    return qs


# ---------------------------------------------------------
# INCIDENCE
# ---------------------------------------------------------

def _get_incidence_by_geography_uncached(year, geographic_level, filters):
    year = str(year)
    filters = filters or {}

    filtered_qs = apply_naaccr_filters(NaaccrData.objects.all(), filters)
    filtered_pat_ids = list(filtered_qs.values_list("mid", flat=True))

    if not filtered_pat_ids:
        return []

    case_counts = (
        NaaccrPatientCensusLinking.objects
        .filter(
            year=year,
            geographic_level=geographic_level,
            pat_id__in=filtered_pat_ids,
        )
        .values("geoid")
        .annotate(case_count=Count("pat_id", distinct=True))
        .order_by()
    )

    case_lookup = {
        _normalize_geoid_for_level_value(r["geoid"], geographic_level): r["case_count"]
        for r in case_counts
    }
    if not case_lookup:
        return []

    sex_specific_denominator = _should_use_sex_specific_denominator(filters)

    if sex_specific_denominator:
        aa_stats = _compute_sex_specific_age_adjusted_ci_by_geo(
            year=year,
            geographic_level=geographic_level,
            filtered_pat_ids=filtered_pat_ids,
            sex=sex_specific_denominator,
        )
    else:
        aa_stats = {}
        if geographic_level == "tract":
            aa_stats = _compute_age_adjusted_ci_by_tract(year, filtered_pat_ids)
        elif geographic_level == "county":
            aa_stats = _compute_age_adjusted_ci_by_county(year, filtered_pat_ids)
        elif geographic_level == "zcta":
            aa_stats = _compute_age_adjusted_ci_by_zcta(year, filtered_pat_ids)

    pop_field = _population_total_field_for_incidence(filters)

    pop_lookup = {}
    for row in (
        Acs5YrB01001.objects
        .filter(year=year, geographic_level=geographic_level)
        .values("geo_id", pop_field)
    ):
        pop = row.get(pop_field)
        if not pop:
            continue

        geoid = _normalize_geoid_for_level_from_geo_id(row["geo_id"], geographic_level)
        if not geoid:
            continue

        pop_lookup[geoid] = pop

    results = []
    for geoid, case_count in case_lookup.items():
        pop = pop_lookup.get(geoid)
        if not pop:
            continue

        if geographic_level == "county":
            nm = OHIO_COUNTY_NAMES.get(geoid)
            label = f"{nm} County" if nm else f"County {geoid}"
        elif geographic_level == "tract":
            label = f"Census Tract {geoid}"
        elif geographic_level == "zcta":
            label = f"ZIP {geoid}"
        else:
            label = geoid

        crude_rate, crude_lo, crude_hi = _rate_ci_from_count(case_count, pop)
        age_adj, age_lo, age_hi = aa_stats.get(geoid, (None, None, None))

        results.append({
            "geoid": geoid,
            "label": label,
            "case_count": case_count,
            "population": int(pop),
            "incidence_per_100k": crude_rate,
            "crude_incidence_per_100k": crude_rate,
            "crude_incidence_ci_lower": crude_lo,
            "crude_incidence_ci_upper": crude_hi,
            "age_adjusted_per_100k": age_adj if age_adj is not None else crude_rate,
            "age_adjusted_ci_lower": age_lo,
            "age_adjusted_ci_upper": age_hi,
        })

    results.sort(key=lambda x: x["incidence_per_100k"], reverse=True)
    return results


@lru_cache(maxsize=128)
def _get_incidence_by_geography_cached(year, geographic_level, filters_json):
    return _get_incidence_by_geography_uncached(
        year=year,
        geographic_level=geographic_level,
        filters=_deserialize_cache_payload(filters_json),
    )


def get_incidence_by_geography(year, geographic_level, filters):
    return _get_incidence_by_geography_cached(
        str(year),
        geographic_level,
        _serialize_cache_payload(filters or {}),
    )


def _get_total_incidence_uncached(year: str, filters: dict):
    year = str(year)
    filters = filters or {}

    filtered_qs = apply_naaccr_filters(NaaccrData.objects.all(), filters)
    filtered_pat_ids = list(filtered_qs.values_list("mid", flat=True))

    if not filtered_pat_ids:
        return None

    total_cases = (
        NaaccrPatientCensusLinking.objects
        .filter(year=year, pat_id__in=filtered_pat_ids)
        .values("pat_id")
        .distinct()
        .count()
    )

    pop_field = _population_total_field_for_incidence(filters)

    population = (
        Acs5YrB01001.objects
        .filter(year=year, geographic_level="state")
        .values_list(pop_field, flat=True)
        .first()
    )

    if not population or population == 0:
        return None

    return {
        "geoid": "TOTAL",
        "label": "Total",
        "case_count": total_cases,
        "population": int(population),
        "incidence_per_100k": round((total_cases / population) * 100000, 1),
    }

@lru_cache(maxsize=64)
def _get_total_incidence_cached(year: str, filters_json: str):
    return _get_total_incidence_uncached(year=year, filters=_deserialize_cache_payload(filters_json))


def get_total_incidence(year: str, filters: dict):
    return _get_total_incidence_cached(str(year), _serialize_cache_payload(filters or {}))



def build_tract_dataset(
    year_range=("2011", "2022"),
    filters=None,
    disease_measures=None,
    incidence_year=None,
    support_measures=None,
    display_options=None,
    community_timeframes=None,
):
    """Backward-compatible wrapper for older code paths.

    The implementation is now geography-agnostic; new code should call
    build_geo_dataset(geographic_level=...) instead.
    """
    return build_geo_dataset(
        geographic_level="tract",
        year_range=year_range,
        filters=filters,
        disease_measures=disease_measures,
        support_measures=support_measures,
        display_options=display_options,
        community_timeframes=community_timeframes,
        incidence_year=incidence_year,
    )

def _as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return []


SUPPORT_MEASURE_OUTPUT_SPECS = {
    "smoking": ("smoking_pct", "smoking_ci_lower", "smoking_ci_upper", None),
    "obesity": ("obesity_pct", "obesity_ci_lower", "obesity_ci_upper", None),
    "binge_drinking": ("binge_drinking_pct", "binge_drinking_ci_lower", "binge_drinking_ci_upper", None),
    "no_leisure_pa": ("no_leisure_pa_pct", "no_leisure_pa_ci_lower", "no_leisure_pa_ci_upper", None),
    "short_sleep": ("short_sleep_pct", "short_sleep_ci_lower", "short_sleep_ci_upper", None),

    "crc_screen": ("crc_screening_pct", "crc_screening_ci_lower", "crc_screening_ci_upper", None),
    "breast_screen": ("mammography_screening_pct", "mammography_screening_ci_lower", "mammography_screening_ci_upper", None),
    "cervical_screen": ("cervical_screening_pct", "cervical_screening_ci_lower", "cervical_screening_ci_upper", None),

    "poor_health": ("poor_health_pct", "poor_health_ci_lower", "poor_health_ci_upper", None),
    "phys_distress": ("phys_distress_pct", "phys_distress_ci_lower", "phys_distress_ci_upper", None),
    "mental_distress": ("mental_distress_pct", "mental_distress_ci_lower", "mental_distress_ci_upper", None),
    "food_insecurity": ("food_insecurity_pct", "food_insecurity_ci_lower", "food_insecurity_ci_upper", None),
    "social_isolation": ("social_isolation_pct", "social_isolation_ci_lower", "social_isolation_ci_upper", None),
    "any_disability": ("any_disability_pct", "any_disability_ci_lower", "any_disability_ci_upper", None),
    "mobility_disability": ("mobility_disability_pct", "mobility_disability_ci_lower", "mobility_disability_ci_upper", None),
    "selfcare_disability": ("selfcare_disability_pct", "selfcare_disability_ci_lower", "selfcare_disability_ci_upper", None),
    "independent_living_disability": ("independent_living_disability_pct", "independent_living_disability_ci_lower", "independent_living_disability_ci_upper", None),

    "routine_checkup": ("routine_checkup_pct", "routine_checkup_ci_lower", "routine_checkup_ci_upper", "routine_checkup_age_adjusted_pct"),
    "no_transport": ("lack_transportation_pct", "lack_transportation_ci_lower", "lack_transportation_ci_upper", "lack_transportation_age_adjusted_pct"),
    "no_insurance": ("uninsured_pct", "uninsured_ci_lower", "uninsured_ci_upper", "uninsured_age_adjusted_pct"),
    "dentist": ("dentist_pct", "dentist_ci_lower", "dentist_ci_upper", "dentist_age_adjusted_pct"),

    "pop_total": ("total_population", "total_population_ci_lower", "total_population_ci_upper", None),
    "sex_distribution": ("sex_distribution", "sex_distribution_ci_lower", "sex_distribution_ci_upper", None),
    "median_age": ("median_age", "median_age_ci_lower", "median_age_ci_upper", None),
    "race_eth": ("race_ethnicity", "race_eth_ci_lower", "race_eth_ci_upper", None),

    "age_dist": ("age_distribution", "age_distribution_ci_lower", "age_distribution_ci_upper", None),
    "marital_status": ("marital_status", "marital_status_ci_lower", "marital_status_ci_upper", None),
    "educ_attain": ("educational_attainment", "educational_attainment_ci_lower", "educational_attainment_ci_upper", None),
    "lang_home": ("language_home", "language_home_ci_lower", "language_home_ci_upper", None),
    "limited_english_pct": ("limited_english_pct", "limited_english_ci_lower", "limited_english_ci_upper", None),
    "citizenship": ("citizenship_status", "citizenship_status_ci_lower", "citizenship_status_ci_upper", None),
    "rurality": ("rurality", "rurality_ci_lower", "rurality_ci_upper", None),

    "med_hh_income": ("median_household_income", "median_household_income_ci_lower", "median_household_income_ci_upper", None),
    "per_capita_income": ("per_capita_income", "per_capita_income_ci_lower", "per_capita_income_ci_upper", None),
    "poverty_pct": ("poverty_pct", "poverty_ci_lower", "poverty_ci_upper", None),
    "income_pov_ratio": ("income_poverty_ratio", "income_poverty_ratio_ci_lower", "income_poverty_ratio_ci_upper", None),
    "snap_pct": ("snap_pct", "snap_ci_lower", "snap_ci_upper", None),
    "employment_16plus": ("employment_16plus", "employment_16plus_ci_lower", "employment_16plus_ci_upper", None),
    "utility_shutoff_threat": ("utility_shutoff_threat_pct", "utility_shutoff_threat_ci_lower", "utility_shutoff_threat_ci_upper", None),
    "housing_insecurity": ("housing_insecurity_pct", "housing_insecurity_ci_lower", "housing_insecurity_ci_upper", None),
    "occupation_dist": ("occupation_distribution", "occupation_distribution_ci_lower", "occupation_distribution_ci_upper", None),
    "gini": ("gini_index", "gini_ci_lower", "gini_ci_upper", None),
    "redlined_pct": ("redlined_pct", "redlined_ci_lower", "redlined_ci_upper", None),
    "svi_adi": ("svi_adi", "svi_adi_ci_lower", "svi_adi_ci_upper", None),

    "housing_unoccupied": ("housing_unoccupied_pct", "housing_unoccupied_ci_lower", "housing_unoccupied_ci_upper", None),
    "renting_pct": ("renting_pct", "renting_ci_lower", "renting_ci_upper", None),
    "median_year_built": ("median_year_built", "median_year_built_ci_lower", "median_year_built_ci_upper", None),
    "median_housing_costs": ("median_housing_costs", "median_housing_costs_ci_lower", "median_housing_costs_ci_upper", None),
    "occupants_per_room": ("occupants_per_room", "occupants_per_room_ci_lower", "occupants_per_room_ci_upper", None),
    "plumbing_complete": ("plumbing_complete_pct", "plumbing_complete_ci_lower", "plumbing_complete_ci_upper", None),
    "kitchen_complete": ("kitchen_complete_pct", "kitchen_complete_ci_lower", "kitchen_complete_ci_upper", None),
    "median_home_value": ("median_home_value", "median_home_value_ci_lower", "median_home_value_ci_upper", None),

    "female_headed": ("female_headed_pct", "female_headed_ci_lower", "female_headed_ci_upper", None),
    "grandparents_care": ("grandparents_care_pct", "grandparents_care_ci_lower", "grandparents_care_ci_upper", None),
    "internet_access": ("internet_access_pct", "internet_access_ci_lower", "internet_access_ci_upper", None),
    "moved_last_year": ("moved_last_year_pct", "moved_last_year_ci_lower", "moved_last_year_ci_upper", None),
}

CI_DISPLAY_OPTION_TO_TOKENS = {
    "cancer_risk_factors_ci": {"smoking", "obesity", "binge_drinking", "no_leisure_pa", "short_sleep"},
    "cancer_screening_ci": {"crc_screen", "breast_screen", "cervical_screen"},
    "noncancer_health_status_ci": {"poor_health", "phys_distress", "mental_distress", "food_insecurity", "social_isolation", "any_disability", "mobility_disability", "selfcare_disability", "independent_living_disability"},
    "access_comm_tract_survey_ci": {"routine_checkup", "no_transport", "no_insurance", "dentist"},
    "access_comm_zcta_place_survey_ci": {"routine_checkup", "no_transport", "no_insurance", "dentist"},
    "access_comm_county_survey_ci": {"routine_checkup", "no_transport", "no_insurance", "dentist"},
    "community_basic_ci": {"pop_total", "sex_distribution", "median_age", "race_eth"},
    "community_extended_ci": {"age_dist", "marital_status", "educ_attain", "lang_home", "limited_english_pct", "citizenship", "rurality"},
    "community_economic_ci": {"med_hh_income", "per_capita_income", "poverty_pct", "income_pov_ratio", "snap_pct", "employment_16plus", "utility_shutoff_threat", "housing_insecurity", "occupation_dist", "gini", "redlined_pct", "svi_adi"},
    "community_housing_ci": {"housing_unoccupied", "renting_pct", "median_year_built", "median_housing_costs", "occupants_per_room", "plumbing_complete", "kitchen_complete", "median_home_value"},
    "community_household_ci": {"female_headed", "grandparents_care", "internet_access", "moved_last_year"},
}

AGE_ADJUST_DISPLAY_OPTION_TO_TOKENS = {
    "access_comm_tract_survey_age_adjusted": {"routine_checkup", "no_transport", "no_insurance", "dentist"},
    "access_comm_county_survey_age_adjusted": {"routine_checkup", "no_transport", "no_insurance", "dentist"},
}


def _normalize_support_measure_tokens(tokens):
    aliases = {
        "pop_total": "pop_total",
        "total_population": "pop_total",

        "med_hh_income": "med_hh_income",
        "median_household_income": "med_hh_income",

        "limited_english": "limited_english_pct",
        "limited_english_pct": "limited_english_pct",
        "english_less_than_very_well": "limited_english_pct",

        "sex_distribution": "sex_distribution",
        "sex_dist": "sex_distribution",
        "male_female_distribution": "sex_distribution",

        "median_age": "median_age",

        "breast_screen": "breast_screen",
        "mammography_screening": "breast_screen",
        "mammography": "breast_screen",

        "routine_checkup": "routine_checkup",
        "checkup": "routine_checkup",

        "no_transport": "no_transport",
        "lack_transportation": "no_transport",
        "transportation": "no_transport",

        "no_insurance": "no_insurance",
        "uninsured": "no_insurance",

        "pcp": "pcp_access_score",
        "primary_care": "pcp_access_score",
        "primary_care_providers": "pcp_access_score",
        "tt_nearest": "pcp_access_score",
        "travel_time": "pcp_access_score",
        "primary_care_travel_time": "pcp_access_score",
        "pcp_access_score": "pcp_access_score",

        "mammo_fac": "mammo_access",
        "mammogram_facilities": "mammo_access",
        "mammography_facilities": "mammo_access",
        "mammography_facility_access": "mammo_access",
        "mammography_access": "mammo_access",
        "mammogram_facility_proximity": "mammo_access",
        "mammo_access": "mammo_access",

        "race_eth": "race_eth",
        "race_ethnicity": "race_eth",
    }

    # Preserve all currently selectable support-measure tokens, including
    # display-only placeholders whose data source will be connected later.
    for token in SUPPORT_MEASURE_OUTPUT_SPECS:
        aliases.setdefault(token, token)

    out = []
    seen = set()
    for tok in _as_list(tokens):
        tok = str(tok).strip()
        canon = aliases.get(tok)
        if canon and canon not in seen:
            out.append(canon)
            seen.add(canon)
    return out


def _tract_from_geo_id(geo_id):
    if not geo_id:
        return None
    return str(geo_id).strip()[-11:]


def _safe_pct(numer, denom):
    if numer is None or denom in (None, 0):
        return None
    try:
        return round((float(numer) / float(denom)) * 100.0, 1)
    except Exception:
        return None


def _safe_float(x):
    try:
        if x in (None, ""):
            return None
        return float(x)
    except Exception:
        return None

def _quote_identifier(identifier):
    return '"' + str(identifier).replace('"', '""') + '"'


def _split_schema_table(table_name):
    """Return (schema, table) for either 'table' or 'schema.table'."""
    raw = str(table_name).strip()
    if "." in raw:
        schema, table = raw.split(".", 1)
        return schema.strip('"'), table.strip('"')
    return None, raw.strip('"')


def _quote_table_identifier(table_name):
    schema, table = _split_schema_table(table_name)
    if schema:
        return f"{_quote_identifier(schema)}.{_quote_identifier(table)}"
    return _quote_identifier(table)


@lru_cache(maxsize=256)
def _get_table_column_map(table_name):
    schema, table = _split_schema_table(table_name)
    with connection.cursor() as cur:
        if schema:
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = %s
                  AND table_name = %s
                """,
                [schema, table],
            )
        else:
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = ANY(current_schemas(false))
                  AND table_name = %s
                """,
                [table],
            )
        rows = cur.fetchall()
    return {str(row[0]).lower(): str(row[0]) for row in rows}



# ---------------------------------------------------------
# ACS MOE / CONFIDENCE INTERVAL HELPERS
# ---------------------------------------------------------

ACS_90_TO_95 = 1.96 / 1.645



def _parse_places_ci(ci_text):
    """
    Parse CDC PLACES CI strings such as '( 8.7, 14.2)' into floats.
    Returns (lower, upper).
    """
    if ci_text in (None, ""):
        return (None, None)
    s = str(ci_text).strip().replace("(", "").replace(")", "")
    parts = [p.strip() for p in s.split(",")]
    if len(parts) != 2:
        return (None, None)
    try:
        return (float(parts[0]), float(parts[1]))
    except (TypeError, ValueError):
        return (None, None)

def _safe_num(x):
    try:
        if x in (None, ""):
            return None
        return float(x)
    except Exception:
        return None


def _acs_moe_95(moe_90):
    """
    ACS MOE columns are published at the 90% confidence level.
    Convert to an approximate 95% MOE so the output is consistent with the
    PopCASE "95% CI" display language.
    """
    moe = _safe_num(moe_90)
    return None if moe is None else moe * ACS_90_TO_95


def _acs_estimate_ci(estimate, moe_90, floor_zero=True, ndigits=2):
    est = _safe_num(estimate)
    moe95 = _acs_moe_95(moe_90)
    if est is None or moe95 is None:
        return (None, None)
    lo = est - moe95
    hi = est + moe95
    if floor_zero:
        lo = max(0.0, lo)
    return (round(lo, ndigits), round(hi, ndigits))


def _acs_pct_ci_from_num_denom(num, num_moe_90, denom, denom_moe_90, ndigits=2):
    """
    Approximate ACS percentage CI from numerator/denominator estimates and
    their 90% MOEs. Uses the ACS-style ratio MOE formula when possible, and a
    conservative fallback if the nested-subset formula becomes negative.
    """
    n = _safe_num(num)
    d = _safe_num(denom)
    n_moe = _acs_moe_95(num_moe_90)
    d_moe = _acs_moe_95(denom_moe_90)

    if n is None or d is None or d <= 0 or n_moe is None:
        return (None, None)

    pct = (n / d) * 100.0

    if d_moe is None:
        moe_pct = (n_moe / d) * 100.0
    else:
        ratio = n / d
        inner = (n_moe ** 2) - ((ratio ** 2) * (d_moe ** 2))
        if inner < 0:
            inner = (n_moe ** 2) + ((ratio ** 2) * (d_moe ** 2))
        moe_pct = (math.sqrt(inner) / d) * 100.0

    return (round(max(0.0, pct - moe_pct), ndigits), round(min(100.0, pct + moe_pct), ndigits))


def _first_existing_col(colmap, candidates):
    for c in candidates:
        if c and c.lower() in colmap:
            return colmap[c.lower()]
    return None


def _select_columns_from_table(table_name, columns, where_sql=None, where_params=None):
    """
    Safe helper for mixed-case ACS/CDC table columns. Missing optional columns
    are returned as absent rather than causing the query to fail.
    """
    colmap = _get_table_column_map(table_name)
    selected = []
    aliases = {}
    for alias, candidates in columns.items():
        col = _first_existing_col(colmap, candidates if isinstance(candidates, (list, tuple)) else [candidates])
        if col:
            aliases[alias] = col
            selected.append(col)

    if not selected:
        return [], aliases

    sql = f'SELECT {", ".join(_quote_identifier(c) for c in selected)} FROM {_quote_table_identifier(table_name)}'
    if where_sql:
        sql += f" WHERE {where_sql}"

    with connection.cursor() as cur:
        cur.execute(sql, where_params or [])
        rows = cur.fetchall()
        returned = [desc[0] for desc in cur.description]

    out = [dict(zip(returned, row)) for row in rows]
    return out, aliases


def _get_acs_b01001_tract_community_lookup(requested):
    """
    Tract-level ACS B01001 estimates + MOE-derived 95% bounds for:
      - total population
      - sex distribution
      - approximate median age
    """
    requested = set(requested or ())
    if not (requested & {"pop_total", "sex_distribution", "median_age"}):
        return {}

    table = "acs_5yr_B01001"
    colmap = _get_table_column_map(table)
    if not colmap:
        return {}

    base_cols = {
        "geo_id": ["GEO_ID", "geo_id"],
        "year": ["year"],
        "geographic_level": ["geographic_level"],
        "total": ["B01001_001E"],
        "total_moe": ["B01001_001M"],
        "male": ["B01001_002E"],
        "male_moe": ["B01001_002M"],
        "female": ["B01001_026E"],
        "female_moe": ["B01001_026M"],
    }

    age_groups = [
        ("m_under5", "B01001_003E", "B01001_003M", 0, 5), ("m_5_9", "B01001_004E", "B01001_004M", 5, 10),
        ("m_10_14", "B01001_005E", "B01001_005M", 10, 15), ("m_15_17", "B01001_006E", "B01001_006M", 15, 18),
        ("m_18_19", "B01001_007E", "B01001_007M", 18, 20), ("m_20", "B01001_008E", "B01001_008M", 20, 21),
        ("m_21", "B01001_009E", "B01001_009M", 21, 22), ("m_22_24", "B01001_010E", "B01001_010M", 22, 25),
        ("m_25_29", "B01001_011E", "B01001_011M", 25, 30), ("m_30_34", "B01001_012E", "B01001_012M", 30, 35),
        ("m_35_39", "B01001_013E", "B01001_013M", 35, 40), ("m_40_44", "B01001_014E", "B01001_014M", 40, 45),
        ("m_45_49", "B01001_015E", "B01001_015M", 45, 50), ("m_50_54", "B01001_016E", "B01001_016M", 50, 55),
        ("m_55_59", "B01001_017E", "B01001_017M", 55, 60), ("m_60_61", "B01001_018E", "B01001_018M", 60, 62),
        ("m_62_64", "B01001_019E", "B01001_019M", 62, 65), ("m_65_66", "B01001_020E", "B01001_020M", 65, 67),
        ("m_67_69", "B01001_021E", "B01001_021M", 67, 70), ("m_70_74", "B01001_022E", "B01001_022M", 70, 75),
        ("m_75_79", "B01001_023E", "B01001_023M", 75, 80), ("m_80_84", "B01001_024E", "B01001_024M", 80, 85),
        ("m_85_plus", "B01001_025E", "B01001_025M", 85, 90),
        ("f_under5", "B01001_027E", "B01001_027M", 0, 5), ("f_5_9", "B01001_028E", "B01001_028M", 5, 10),
        ("f_10_14", "B01001_029E", "B01001_029M", 10, 15), ("f_15_17", "B01001_030E", "B01001_030M", 15, 18),
        ("f_18_19", "B01001_031E", "B01001_031M", 18, 20), ("f_20", "B01001_032E", "B01001_032M", 20, 21),
        ("f_21", "B01001_033E", "B01001_033M", 21, 22), ("f_22_24", "B01001_034E", "B01001_034M", 22, 25),
        ("f_25_29", "B01001_035E", "B01001_035M", 25, 30), ("f_30_34", "B01001_036E", "B01001_036M", 30, 35),
        ("f_35_39", "B01001_037E", "B01001_037M", 35, 40), ("f_40_44", "B01001_038E", "B01001_038M", 40, 45),
        ("f_45_49", "B01001_039E", "B01001_039M", 45, 50), ("f_50_54", "B01001_040E", "B01001_040M", 50, 55),
        ("f_55_59", "B01001_041E", "B01001_041M", 55, 60), ("f_60_61", "B01001_042E", "B01001_042M", 60, 62),
        ("f_62_64", "B01001_043E", "B01001_043M", 62, 65), ("f_65_66", "B01001_044E", "B01001_044M", 65, 67),
        ("f_67_69", "B01001_045E", "B01001_045M", 67, 70), ("f_70_74", "B01001_046E", "B01001_046M", 70, 75),
        ("f_75_79", "B01001_047E", "B01001_047M", 75, 80), ("f_80_84", "B01001_048E", "B01001_048M", 80, 85),
        ("f_85_plus", "B01001_049E", "B01001_049M", 85, 90),
    ]

    cols = dict(base_cols)
    if "median_age" in requested:
        for alias, e_col, m_col, _, _ in age_groups:
            cols[alias] = [e_col]
            cols[f"{alias}_moe"] = [m_col]

    where = None
    params = []
    level_col = _first_existing_col(colmap, ["geographic_level"])
    if level_col:
        where = f'{_quote_identifier(level_col)} = %s'
        params = ["tract"]

    rows, aliases = _select_columns_from_table(table, cols, where, params)

    def val(row, alias):
        col = aliases.get(alias)
        return row.get(col) if col else None

    lookup = {}
    for row in rows:
        tract = _tract_from_geo_id(val(row, "geo_id"))
        if not tract:
            continue

        total = val(row, "total")
        total_moe = val(row, "total_moe")
        male = val(row, "male")
        male_moe = val(row, "male_moe")
        female = val(row, "female")
        female_moe = val(row, "female_moe")

        out = {}

        if "pop_total" in requested:
            out["total_population"] = total
            out["total_population_moe_90"] = total_moe
            lo, hi = _acs_estimate_ci(total, total_moe, floor_zero=True, ndigits=0)
            out["total_population_ci_lower"] = lo
            out["total_population_ci_upper"] = hi

        if "sex_distribution" in requested:
            out["male_population"] = male
            out["female_population"] = female
            out["male_pct"] = _safe_pct(male, total)
            out["female_pct"] = _safe_pct(female, total)
            out["male_pct_ci_lower"], out["male_pct_ci_upper"] = _acs_pct_ci_from_num_denom(male, male_moe, total, total_moe)
            out["female_pct_ci_lower"], out["female_pct_ci_upper"] = _acs_pct_ci_from_num_denom(female, female_moe, total, total_moe)
            out["sex_distribution"] = "Male/Female"
            out["sex_distribution_ci_lower"] = None
            out["sex_distribution_ci_upper"] = None

        if "median_age" in requested:
            # Current app estimates median age from grouped B01001 age/sex bins.
            # These bounds are approximate because ACS does not publish a direct
            # MOE for this derived grouped median in B01001.
            bins = defaultdict(lambda: [0.0, 0.0])
            for alias, _, _, lower, upper in age_groups:
                est = _safe_num(val(row, alias)) or 0.0
                moe95 = _acs_moe_95(val(row, f"{alias}_moe")) or 0.0
                bins[(lower, upper)][0] += est
                bins[(lower, upper)][1] = math.sqrt((bins[(lower, upper)][1] ** 2) + (moe95 ** 2))
            counts_est = [(lo, hi, est_moe[0]) for (lo, hi), est_moe in sorted(bins.items())]
            counts_low = [(lo, hi, max(0.0, est_moe[0] - est_moe[1])) for (lo, hi), est_moe in sorted(bins.items())]
            counts_high = [(lo, hi, est_moe[0] + est_moe[1]) for (lo, hi), est_moe in sorted(bins.items())]
            out["median_age"] = _estimate_grouped_median_age(counts_est)
            low_med = _estimate_grouped_median_age(counts_low)
            high_med = _estimate_grouped_median_age(counts_high)
            out["median_age_ci_lower"] = min(low_med, high_med) if low_med is not None and high_med is not None else None
            out["median_age_ci_upper"] = max(low_med, high_med) if low_med is not None and high_med is not None else None

        if out:
            lookup[tract] = out

    return lookup


def _get_acs_income_tract_lookup():
    table = "acs_5yr_B19013"
    cols = {
        "geo_id": ["GEO_ID", "geo_id"],
        "income": ["B19013_001E"],
        "income_moe": ["B19013_001M"],
    }
    rows, aliases = _select_columns_from_table(table, cols)

    def val(row, alias):
        col = aliases.get(alias)
        return row.get(col) if col else None

    lookup = {}
    for row in rows:
        tract = _tract_from_geo_id(val(row, "geo_id"))
        if not tract:
            continue
        income = val(row, "income")
        lo, hi = _acs_estimate_ci(income, val(row, "income_moe"), floor_zero=True, ndigits=0)
        lookup[tract] = {
            "median_household_income": income,
            "median_household_income_ci_lower": lo,
            "median_household_income_ci_upper": hi,
        }
    return lookup


def _get_acs_limited_english_tract_lookup():
    table = "acs_5yr_C16001"
    cols = {
        "geo_id": ["GEO_ID", "geo_id"],
        "denom": ["C16001_001E"],
        "denom_moe": ["C16001_001M"],
        "num": ["C16001_004E"],
        "num_moe": ["C16001_004M"],
    }
    rows, aliases = _select_columns_from_table(table, cols)

    def val(row, alias):
        col = aliases.get(alias)
        return row.get(col) if col else None

    lookup = {}
    for row in rows:
        tract = _tract_from_geo_id(val(row, "geo_id"))
        if not tract:
            continue
        num, denom = val(row, "num"), val(row, "denom")
        lookup[tract] = {
            "limited_english_pct": _safe_pct(num, denom),
        }
        lo, hi = _acs_pct_ci_from_num_denom(num, val(row, "num_moe"), denom, val(row, "denom_moe"))
        lookup[tract]["limited_english_ci_lower"] = lo
        lookup[tract]["limited_english_ci_upper"] = hi
    return lookup



def _safe_round_float(x, digits=2):
    """Return a rounded float, preserving None/blank/non-numeric values as None."""
    try:
        if x in (None, ""):
            return None
        return round(float(x), digits)
    except Exception:
        return None


def _quote_ident(name: str) -> str:
    """Safely quote a PostgreSQL identifier."""
    return '"' + str(name).replace('"', '""') + '"'


def _get_model_table_columns(model, using="default"):
    """Return physical database column names for a Django model table."""
    table_name = model._meta.db_table
    with connections[using].cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = %s
            """,
            [table_name.lower()],
        )
        cols = [row[0] for row in cur.fetchall()]

    # Some uploaded tables use mixed-case quoted names; fall back to an
    # exact-name lookup when the lowercase information_schema lookup misses.
    if not cols:
        with connections[using].cursor() as cur:
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = %s
                """,
                [table_name],
            )
            cols = [row[0] for row in cur.fetchall()]
    return cols


def _first_existing_column(columns, candidates):
    lower_map = {str(c).lower(): c for c in columns}
    for candidate in candidates:
        found = lower_map.get(str(candidate).lower())
        if found:
            return found
    return None


def _find_measure_column(columns, stem_candidates, suffix_candidates=None, contains_all=None):
    """
    Finds a column by exact candidate names first, then by loose token search.
    This makes the CDC PLACES CI lookup resilient to different ETL naming styles.
    """
    lower_map = {str(c).lower(): c for c in columns}

    exact_candidates = []
    suffix_candidates = suffix_candidates or []
    for stem in stem_candidates:
        exact_candidates.append(stem)
        for suffix in suffix_candidates:
            exact_candidates.append(f"{stem}_{suffix}")
            exact_candidates.append(f"{stem}{suffix}")

    for candidate in exact_candidates:
        found = lower_map.get(str(candidate).lower())
        if found:
            return found

    if contains_all:
        tokens = [t.lower() for t in contains_all if t]
        for col in columns:
            col_l = str(col).lower()
            if all(t in col_l for t in tokens):
                return col
    return None


def _parse_ci_text(value):
    """
    CDC/PLACES exports sometimes store CI as a single string like
    "(62.1, 68.4)". Return (lower, upper) when possible.
    """
    if value in (None, ""):
        return (None, None)
    nums = re.findall(r"-?\d+(?:\.\d+)?", str(value))
    if len(nums) >= 2:
        return (_safe_round_float(nums[0], 2), _safe_round_float(nums[1], 2))
    return (None, None)


CDC_PLACES_TRACT_MEASURE_SPECS = {
    "breast_screen": {
        "estimate_key": "breast_screen",
        "output_key": "mammography_screening_pct",
        "ci_lower_key": "mammography_screening_ci_lower",
        "ci_upper_key": "mammography_screening_ci_upper",
        "age_adjusted_key": None,
        "estimate_stems": [
            "mammography_screening", "mammography", "mammouse", "mammography_use",
            "mammo", "mammography_screening_pct",
        ],
        "lower_stems": [
            "mammography_screening_ci_lower", "mammography_screening_lci", "mammography_screening_low",
            "mammography_screening_lower", "mammography_lci", "mammography_low",
            "mammouse_lci", "mammouse_low", "mammo_lci", "mammo_low",
        ],
        "upper_stems": [
            "mammography_screening_ci_upper", "mammography_screening_uci", "mammography_screening_high",
            "mammography_screening_upper", "mammography_uci", "mammography_high",
            "mammouse_uci", "mammouse_high", "mammo_uci", "mammo_high",
        ],
        "ci_text_stems": [
            "mammography_screening_ci", "mammography_screening_95ci", "mammography_95ci",
            "mammouse_95ci", "mammo_95ci",
        ],
    },
    "routine_checkup": {
        "estimate_key": "routine_checkup",
        "output_key": "routine_checkup_pct",
        "ci_lower_key": "routine_checkup_ci_lower",
        "ci_upper_key": "routine_checkup_ci_upper",
        "age_adjusted_key": "routine_checkup_age_adjusted_pct",
        "estimate_stems": ["routine_checkup", "checkup", "checkup_pct"],
        "lower_stems": ["routine_checkup_ci_lower", "routine_checkup_lci", "routine_checkup_low", "checkup_lci", "checkup_low"],
        "upper_stems": ["routine_checkup_ci_upper", "routine_checkup_uci", "routine_checkup_high", "checkup_uci", "checkup_high"],
        "ci_text_stems": ["routine_checkup_ci", "routine_checkup_95ci", "checkup_95ci"],
        "age_adjusted_stems": ["routine_checkup_age_adjusted", "routine_checkup_age_adjusted_pct", "checkup_age_adjusted"],
    },
    "no_transport": {
        "estimate_key": "no_transport",
        "output_key": "lack_transportation_pct",
        "ci_lower_key": "lack_transportation_ci_lower",
        "ci_upper_key": "lack_transportation_ci_upper",
        "age_adjusted_key": "lack_transportation_age_adjusted_pct",
        "estimate_stems": ["lack_transportation", "no_transport", "transportation", "transportation_barrier"],
        "lower_stems": ["lack_transportation_ci_lower", "lack_transportation_lci", "lack_transportation_low", "no_transport_lci", "transportation_lci"],
        "upper_stems": ["lack_transportation_ci_upper", "lack_transportation_uci", "lack_transportation_high", "no_transport_uci", "transportation_uci"],
        "ci_text_stems": ["lack_transportation_ci", "lack_transportation_95ci", "no_transport_95ci", "transportation_95ci"],
        "age_adjusted_stems": ["lack_transportation_age_adjusted", "lack_transportation_age_adjusted_pct", "no_transport_age_adjusted"],
    },
    "no_insurance": {
        "estimate_key": "no_insurance",
        "output_key": "uninsured_pct",
        "ci_lower_key": "uninsured_ci_lower",
        "ci_upper_key": "uninsured_ci_upper",
        "age_adjusted_key": "uninsured_age_adjusted_pct",
        "estimate_stems": ["uninsured", "no_insurance", "health_insurance_none"],
        "lower_stems": ["uninsured_ci_lower", "uninsured_lci", "uninsured_low", "no_insurance_lci", "no_insurance_low"],
        "upper_stems": ["uninsured_ci_upper", "uninsured_uci", "uninsured_high", "no_insurance_uci", "no_insurance_high"],
        "ci_text_stems": ["uninsured_ci", "uninsured_95ci", "no_insurance_95ci"],
        "age_adjusted_stems": ["uninsured_age_adjusted", "uninsured_age_adjusted_pct", "no_insurance_age_adjusted"],
    },
}


def _get_cdc_places_tract_lookup(requested):
    """
    Reads CDC PLACES tract estimates plus lower/upper confidence limits when
    those columns exist in the loaded ETL table.

    The app's model currently uses simplified estimate names, but different
    PLACES ETL files name CI columns differently. This raw-column reader checks
    the actual database columns and maps any available lower/upper CI columns
    into the output keys expected by results.html/views.py.
    """
    requested = set(requested or ())
    measure_specs = {
        token: spec
        for token, spec in CDC_PLACES_TRACT_MEASURE_SPECS.items()
        if token in requested
    }
    if not measure_specs:
        return {}

    table_name = CDCPlacesTract2024._meta.db_table
    columns = _get_model_table_columns(CDCPlacesTract2024)
    if not columns:
        return {}

    geoid_col = _first_existing_column(columns, [
        "tract_fips", "tractfips", "tract", "geoid", "geo_id", "locationid", "location_id",
    ])
    if not geoid_col:
        return {}

    selected = {"geoid": geoid_col}
    col_roles = {}

    for token, spec in measure_specs.items():
        est_col = _find_measure_column(
            columns,
            spec["estimate_stems"],
            contains_all=[spec["estimate_stems"][0]],
        )
        low_col = _find_measure_column(
            columns,
            spec.get("lower_stems", []),
            suffix_candidates=["ci_lower", "lower", "lci", "low", "95ci_lower", "95ci_low"],
            contains_all=[spec["estimate_stems"][0].split("_")[0], "lci"],
        )
        high_col = _find_measure_column(
            columns,
            spec.get("upper_stems", []),
            suffix_candidates=["ci_upper", "upper", "uci", "high", "95ci_upper", "95ci_high"],
            contains_all=[spec["estimate_stems"][0].split("_")[0], "uci"],
        )
        ci_text_col = _find_measure_column(
            columns,
            spec.get("ci_text_stems", []),
            suffix_candidates=["ci", "95ci", "confidence_interval"],
            contains_all=[spec["estimate_stems"][0].split("_")[0], "95ci"],
        )
        age_col = None
        if spec.get("age_adjusted_key"):
            age_col = _find_measure_column(
                columns,
                spec.get("age_adjusted_stems", []),
                suffix_candidates=["age_adjusted", "ageadj", "age_adjusted_pct"],
                contains_all=[spec["estimate_stems"][0].split("_")[0], "age"],
            )

        for role, col in (
            ("estimate", est_col),
            ("low", low_col),
            ("high", high_col),
            ("ci_text", ci_text_col),
            ("age_adjusted", age_col),
        ):
            if col:
                alias = f"{token}__{role}"
                selected[alias] = col
                col_roles[(token, role)] = alias

    select_sql = ", ".join(
        f"{_quote_ident(col)} AS {_quote_ident(alias)}"
        for alias, col in selected.items()
    )
    sql = f"SELECT {select_sql} FROM {_quote_ident(table_name)}"

    lookup = {}
    with connection.cursor() as cur:
        cur.execute(sql)
        colnames = [desc[0] for desc in cur.description]
        for db_row in cur.fetchall():
            row = dict(zip(colnames, db_row))
            tract = row.get("geoid")
            tract = str(tract).strip() if tract is not None else None
            if not tract:
                continue
            tract = tract[-11:] if len(tract) >= 11 else tract

            out = {}
            for token, spec in measure_specs.items():
                est = row.get(col_roles.get((token, "estimate")))
                low = row.get(col_roles.get((token, "low")))
                high = row.get(col_roles.get((token, "high")))
                if (low in (None, "")) or (high in (None, "")):
                    parsed_low, parsed_high = _parse_ci_text(row.get(col_roles.get((token, "ci_text"))))
                    low = low if low not in (None, "") else parsed_low
                    high = high if high not in (None, "") else parsed_high

                age_adjusted = row.get(col_roles.get((token, "age_adjusted")))

                out[spec["estimate_key"]] = _safe_round_float(est, 2)
                out[spec["output_key"]] = _safe_round_float(est, 2)
                out[spec["ci_lower_key"]] = _safe_round_float(low, 2)
                out[spec["ci_upper_key"]] = _safe_round_float(high, 2)
                if spec.get("age_adjusted_key"):
                    out[spec["age_adjusted_key"]] = _safe_round_float(age_adjusted, 2)

            lookup[tract] = out

    return lookup


def _rate_ci_from_count(case_count, population, multiplier=100000.0):
    if case_count in (None, "") or population in (None, "", 0):
        return (None, None, None)

    try:
        case_count = float(case_count)
        population = float(population)
    except Exception:
        return (None, None, None)

    if population <= 0:
        return (None, None, None)

    rate = (case_count / population) * multiplier
    se = (math.sqrt(case_count) / population) * multiplier if case_count >= 0 else 0.0
    lo = max(0.0, rate - 1.96 * se)
    hi = rate + 1.96 * se
    return (round(rate, 1), round(lo, 1), round(hi, 1))


def _haversine_miles(lat1, lon1, lat2, lon2):
    """
    Great-circle distance in miles.
    """
    lat1 = math.radians(lat1)
    lon1 = math.radians(lon1)
    lat2 = math.radians(lat2)
    lon2 = math.radians(lon2)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.asin(min(1, math.sqrt(a)))
    earth_radius_miles = 3958.7613
    return earth_radius_miles * c


def _normalize_geoid_from_geo_id(geo_id_value):
    if geo_id_value is None:
        return None
    s = str(geo_id_value).strip()
    if not s:
        return None
    if "US" in s:
        return s.split("US", 1)[1]
    return s


def _get_table_columns(table_name):
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = %s
            """,
            [table_name.lower()],
        )
        return [row[0] for row in cur.fetchall()]


def _find_geo_column(table_name):
    cols = _get_table_columns(table_name)
    lower_map = {c.lower(): c for c in cols}
    for candidate in ("geo_id", "geoid", "tract_fips"):
        if candidate in lower_map:
            return lower_map[candidate]
    return None


def _find_total_column(table_name):
    cols = _get_table_columns(table_name)
    lower_map = {c.lower(): c for c in cols}
    table_suffix = table_name.replace("acs_5yr_", "").lower()
    exact = f"{table_suffix}_001e"
    if exact in lower_map:
        return lower_map[exact]
    for c in cols:
        if c.lower().endswith("_001e"):
            return c
    return None


@lru_cache(maxsize=32)
def _fetch_acs_total_lookup(table_name):
    spec = RACE_TABLE_SPECS.get(table_name)
    if not spec:
        return {}

    geo_col = spec["geo_col"]
    total_col = spec["total_col"]

    sql = f'''
        SELECT "{geo_col}", "{total_col}"
        FROM "{table_name}"
    '''

    lookup = {}
    with connection.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    for geo_val, total_val in rows:
        tract = _normalize_geoid_from_geo_id(geo_val)
        if not tract:
            continue

        tract = str(tract).strip()
        if len(tract) != 11:
            continue

        try:
            lookup[tract] = float(total_val or 0)
        except (TypeError, ValueError):
            lookup[tract] = 0.0

    return lookup


@lru_cache(maxsize=32)
def _fetch_acs_total_moe_lookup(table_name):
    spec = RACE_TABLE_SPECS.get(table_name)
    if not spec:
        return {}

    rows, aliases = _select_columns_from_table(
        table_name,
        {
            "geo": [spec["geo_col"]],
            "estimate": [spec["total_col"]],
            "moe": [spec.get("moe_col") or spec["total_col"].replace("_001E", "_001M")],
        },
    )

    def val(row, alias):
        col = aliases.get(alias)
        return row.get(col) if col else None

    lookup = {}
    for row in rows:
        tract = _normalize_geoid_from_geo_id(val(row, "geo"))
        if not tract:
            continue
        tract = str(tract).strip()
        if len(tract) != 11:
            continue
        lookup[tract] = {
            "estimate": _safe_num(val(row, "estimate")) or 0.0,
            "moe": val(row, "moe"),
        }
    return lookup


@lru_cache(maxsize=1)
def _get_tract_race_ethnicity_lookup():
    total_lookup = {}
    for tract, row in _get_acs_b01001_tract_community_lookup(("pop_total",)).items():
        total_lookup[tract] = {
            "estimate": row.get("total_population"),
            "moe": row.get("total_population_moe_90"),
            "ci_lower": row.get("total_population_ci_lower"),
            "ci_upper": row.get("total_population_ci_upper"),
        }

    race_specs = {
        "white_alone": ("acs_5yr_B01001A", "white_alone_pct", "white_alone_ci_lower", "white_alone_ci_upper"),
        "black_alone": ("acs_5yr_B01001B", "black_alone_pct", "black_alone_ci_lower", "black_alone_ci_upper"),
        "aian_alone": ("acs_5yr_B01001C", "aian_alone_pct", "aian_alone_ci_lower", "aian_alone_ci_upper"),
        "asian_alone": ("acs_5yr_B01001D", "asian_alone_pct", "asian_alone_ci_lower", "asian_alone_ci_upper"),
        "nhpi_alone": ("acs_5yr_B01001E", "nhpi_alone_pct", "nhpi_alone_ci_lower", "nhpi_alone_ci_upper"),
        "other_race_alone": ("acs_5yr_B01001F", "other_race_alone_pct", "other_race_alone_ci_lower", "other_race_alone_ci_upper"),
        "multiracial": ("acs_5yr_B01001G", "multiracial_pct", "multiracial_ci_lower", "multiracial_ci_upper"),
        "nh_white": ("acs_5yr_B01001H", "nh_white_pct", "nh_white_ci_lower", "nh_white_ci_upper"),
        "hispanic": ("acs_5yr_B01001I", "hispanic_pct", "hispanic_ci_lower", "hispanic_ci_upper"),
    }

    race_lookups = {
        name: _fetch_acs_total_moe_lookup(table)
        for name, (table, _, _, _) in race_specs.items()
    }

    all_tracts = set(total_lookup.keys())
    for lu in race_lookups.values():
        all_tracts |= set(lu.keys())

    lookup = {}
    for tract in all_tracts:
        total = (total_lookup.get(tract) or {}).get("estimate")
        # Use the raw ACS total MOE when available from B01001.
        total_moe = (total_lookup.get(tract) or {}).get("moe")

        row_out = {}
        for name, (_, pct_key, low_key, high_key) in race_specs.items():
            race_row = race_lookups.get(name, {}).get(tract, {})
            estimate = race_row.get("estimate")
            moe = race_row.get("moe")
            row_out[pct_key] = _safe_pct(estimate, total)
            row_out[low_key], row_out[high_key] = _acs_pct_ci_from_num_denom(
                estimate, moe, total, total_moe
            )

        row_out["race_ethnicity"] = "Race/Ethnicity percentages"
        row_out["race_eth_ci_lower"] = None
        row_out["race_eth_ci_upper"] = None
        lookup[tract] = row_out

    return lookup


@lru_cache(maxsize=32)
def _get_tract_support_lookups_cached(requested_tuple):
    """
    Returns tract-level lookup dicts for selected non-disease fields.

    ACS community-characteristic CIs are calculated from ACS MOE columns.
    ACS publishes MOEs at 90%; helpers above convert those MOEs to approximate
    95% bounds for the PopCASE display.
    """
    requested = set(requested_tuple or ())
    lookups = {}

    community_acs = {}

    if requested & {"pop_total", "sex_distribution", "median_age"}:
        basic_lookup = _get_acs_b01001_tract_community_lookup(requested)
        for tract, row in basic_lookup.items():
            community_acs.setdefault(tract, {}).update(row)

        if "pop_total" in requested:
            lookups["pop"] = {
                tract: row.get("total_population")
                for tract, row in basic_lookup.items()
            }

        if "sex_distribution" in requested:
            lookups["sex"] = {
                tract: {
                    "male_population": row.get("male_population"),
                    "female_population": row.get("female_population"),
                    "male_pct": row.get("male_pct"),
                    "female_pct": row.get("female_pct"),
                    "male_pct_ci_lower": row.get("male_pct_ci_lower"),
                    "male_pct_ci_upper": row.get("male_pct_ci_upper"),
                    "female_pct_ci_lower": row.get("female_pct_ci_lower"),
                    "female_pct_ci_upper": row.get("female_pct_ci_upper"),
                }
                for tract, row in basic_lookup.items()
            }

        if "median_age" in requested:
            lookups["median_age"] = {
                tract: row.get("median_age")
                for tract, row in basic_lookup.items()
            }

    if "race_eth" in requested:
        race_lookup = _get_tract_race_ethnicity_lookup()
        lookups["race_eth"] = race_lookup
        for tract, row in race_lookup.items():
            community_acs.setdefault(tract, {}).update(row)

    if "med_hh_income" in requested:
        income_lookup = _get_acs_income_tract_lookup()
        lookups["income"] = {
            tract: row.get("median_household_income")
            for tract, row in income_lookup.items()
        }
        for tract, row in income_lookup.items():
            community_acs.setdefault(tract, {}).update(row)

    if "limited_english_pct" in requested:
        limited_lookup = _get_acs_limited_english_tract_lookup()
        lookups["limited_english_pct"] = {
            tract: row.get("limited_english_pct")
            for tract, row in limited_lookup.items()
        }
        for tract, row in limited_lookup.items():
            community_acs.setdefault(tract, {}).update(row)

    if community_acs:
        lookups["community_acs"] = community_acs

    if requested & {"breast_screen", "routine_checkup", "no_transport", "no_insurance", "smoking", "obesity", "binge_drinking", "no_leisure_pa", "short_sleep", "crc_screen", "dentist", "poor_health", "phys_distress", "mental_distress", "food_insecurity", "social_isolation", "any_disability", "mobility_disability", "selfcare_disability", "independent_living_disability"}:
        places_lookup = {}
        # Keep the model-based estimate lookup as a fallback for the core tract
        # measures. If raw CDC lookup helpers are present in this services.py,
        # they will be used elsewhere/merged by _get_support_lookups.
        model_fields = {f.name for f in CDCPlacesTract2024._meta.get_fields() if hasattr(f, "attname")}
        values_fields = ["tract_fips"]
        for f in [
            "mammography_screening", "routine_checkup", "lack_transportation", "uninsured",
            "mammography_screening_ci", "routine_checkup_ci", "lack_transportation_ci", "uninsured_ci",
            "smoking", "smoking_ci", "obesity", "obesity_ci", "binge_drinking", "binge_drinking_ci",
            "no_leisure_pa", "no_leisure_pa_ci", "short_sleep", "short_sleep_ci",
            "colorectal_screening", "colorectal_screening_ci", "dental", "dental_ci",
            "poor_health", "poor_health_ci", "physical_distress", "physical_distress_ci",
            "mental_distress", "mental_distress_ci", "food_insecurity", "food_insecurity_ci",
            "social_isolation", "social_isolation_ci", "any_disability", "any_disability_ci",
            "mobility_disability", "mobility_disability_ci", "selfcare_disability", "selfcare_disability_ci",
            "independent_living_disability", "independent_living_disability_ci",
        ]:
            if f in model_fields:
                values_fields.append(f)

        def add_ci(out, row, src_key, low_key, high_key):
            lo, hi = _parse_places_ci(row.get(src_key))
            out[low_key] = lo
            out[high_key] = hi

        for row in CDCPlacesTract2024.objects.all().values(*values_fields).iterator(chunk_size=5000):
            tract = str(row["tract_fips"]).strip() if row.get("tract_fips") else None
            if not tract:
                continue
            tract = tract.zfill(11)[-11:]

            out = {}

            if "mammography_screening" in row:
                out["breast_screen"] = row.get("mammography_screening")
                out["mammography_screening_pct"] = row.get("mammography_screening")
            if "mammography_screening_ci" in row:
                add_ci(out, row, "mammography_screening_ci", "mammography_screening_ci_lower", "mammography_screening_ci_upper")

            if "routine_checkup" in row:
                out["routine_checkup"] = row.get("routine_checkup")
                out["routine_checkup_pct"] = row.get("routine_checkup")
            if "routine_checkup_ci" in row:
                add_ci(out, row, "routine_checkup_ci", "routine_checkup_ci_lower", "routine_checkup_ci_upper")

            if "lack_transportation" in row:
                out["no_transport"] = row.get("lack_transportation")
                out["lack_transportation_pct"] = row.get("lack_transportation")
            if "lack_transportation_ci" in row:
                add_ci(out, row, "lack_transportation_ci", "lack_transportation_ci_lower", "lack_transportation_ci_upper")

            if "uninsured" in row:
                out["no_insurance"] = row.get("uninsured")
                out["uninsured_pct"] = row.get("uninsured")
            if "uninsured_ci" in row:
                add_ci(out, row, "uninsured_ci", "uninsured_ci_lower", "uninsured_ci_upper")

            places_map = {
                "smoking": ("smoking_pct", "smoking_ci", "smoking_ci_lower", "smoking_ci_upper"),
                "obesity": ("obesity_pct", "obesity_ci", "obesity_ci_lower", "obesity_ci_upper"),
                "binge_drinking": ("binge_drinking_pct", "binge_drinking_ci", "binge_drinking_ci_lower", "binge_drinking_ci_upper"),
                "no_leisure_pa": ("no_leisure_pa_pct", "no_leisure_pa_ci", "no_leisure_pa_ci_lower", "no_leisure_pa_ci_upper"),
                "short_sleep": ("short_sleep_pct", "short_sleep_ci", "short_sleep_ci_lower", "short_sleep_ci_upper"),
                "colorectal_screening": ("crc_screening_pct", "colorectal_screening_ci", "crc_screening_ci_lower", "crc_screening_ci_upper"),
                "dental": ("dentist_pct", "dental_ci", "dentist_ci_lower", "dentist_ci_upper"),
                "poor_health": ("poor_health_pct", "poor_health_ci", "poor_health_ci_lower", "poor_health_ci_upper"),
                "physical_distress": ("phys_distress_pct", "physical_distress_ci", "phys_distress_ci_lower", "phys_distress_ci_upper"),
                "mental_distress": ("mental_distress_pct", "mental_distress_ci", "mental_distress_ci_lower", "mental_distress_ci_upper"),
                "food_insecurity": ("food_insecurity_pct", "food_insecurity_ci", "food_insecurity_ci_lower", "food_insecurity_ci_upper"),
                "social_isolation": ("social_isolation_pct", "social_isolation_ci", "social_isolation_ci_lower", "social_isolation_ci_upper"),
                "any_disability": ("any_disability_pct", "any_disability_ci", "any_disability_ci_lower", "any_disability_ci_upper"),
                "mobility_disability": ("mobility_disability_pct", "mobility_disability_ci", "mobility_disability_ci_lower", "mobility_disability_ci_upper"),
                "selfcare_disability": ("selfcare_disability_pct", "selfcare_disability_ci", "selfcare_disability_ci_lower", "selfcare_disability_ci_upper"),
                "independent_living_disability": ("independent_living_disability_pct", "independent_living_disability_ci", "independent_living_disability_ci_lower", "independent_living_disability_ci_upper"),
            }
            for model_key, (out_key, ci_key, low_key, high_key) in places_map.items():
                if model_key in row:
                    out[out_key] = row.get(model_key)
                if ci_key in row:
                    add_ci(out, row, ci_key, low_key, high_key)

            if out:
                places_lookup[tract] = out

        if places_lookup:
            lookups["places"] = places_lookup

    if "pcp_access_score" in requested:
        try:
            lookups["pcp_access"] = {
                str(row["tract_geoid"]).strip(): row["weighted_sa_final"]
                for row in TravelTimeTract.objects.using("popcase_manual_etl").all().values("tract_geoid", "weighted_sa_final").iterator(chunk_size=5000)
                if row["tract_geoid"]
            }
        except Exception:
            lookups["pcp_access"] = {}

    if "mammo_access" in requested:
        try:
            lookups["mammo_access"] = _get_tract_mammography_access_lookup(radius_miles=20.0)
        except Exception:
            lookups["mammo_access"] = {}

    return lookups


def _ci_requested_for_token(token, display_options):
    return any(
        token in CI_DISPLAY_OPTION_TO_TOKENS.get(option, set())
        for option in display_options
    )


def _age_adjusted_requested_for_token(token, display_options):
    return any(
        token in AGE_ADJUST_DISPLAY_OPTION_TO_TOKENS.get(option, set())
        for option in display_options
    )


def _add_display_option_columns(out, support_measures, display_options, source_values=None):
    """
    Add selected support-measure columns plus optional CI/age-adjusted companion columns.

    If the real lower/upper CI or age-adjusted source field is not mapped yet,
    the column is intentionally left blank instead of being filled with an
    unsupported estimate.
    """
    source_values = source_values or {}
    display_options = set(display_options or [])

    for token in support_measures:
        spec = SUPPORT_MEASURE_OUTPUT_SPECS.get(token)
        if not spec:
            continue

        value_key, ci_low_key, ci_high_key, age_adjusted_key = spec

        if value_key not in out:
            out[value_key] = source_values.get(value_key)

        if _ci_requested_for_token(token, display_options):
            out[ci_low_key] = source_values.get(ci_low_key)
            out[ci_high_key] = source_values.get(ci_high_key)

        if age_adjusted_key and _age_adjusted_requested_for_token(token, display_options):
            out[age_adjusted_key] = source_values.get(age_adjusted_key)



def _get_tract_support_lookups(requested_support_measures=None):
    """Backward-compatible tract support lookup wrapper."""
    return _get_geo_support_lookups("tract", requested_support_measures)


CDC_PLACES_MODEL_BY_GEO = {
    "tract": CDCPlacesTract2024,
    "county": CDCPlacesCounty2024,
    "zcta": CDCPlacesZCTA2024,
    "place": CDCPlacesPlace2024,
}

CDC_PLACES_GEOID_CANDIDATES_BY_GEO = {
    "tract": ["TractFIPS", "tract_fips", "tractfips", "tract", "GEOID", "geoid", "GEO_ID", "geo_id", "LOCATIONID", "locationid"],
    "county": ["CountyFIPS", "county_fips", "countyfips", "FIPS", "fips", "GEOID", "geoid", "GEO_ID", "geo_id", "LOCATIONID", "locationid"],
    "zcta": ["ZCTA5", "zcta5", "ZCTA", "zcta", "GEOID", "geoid", "GEO_ID", "geo_id", "LOCATIONID", "locationid"],
    "place": ["PlaceFIPS", "place_fips", "placefips", "GEOID", "geoid", "GEO_ID", "geo_id", "LOCATIONID", "locationid"],
}

CDC_PLACES_CODE_BY_TOKEN = {
    "breast_screen": "MAMMOUSE",
    "routine_checkup": "CHECKUP",
    "no_transport": "LACKTRPT",
    "no_insurance": "ACCESS2",
    "smoking": "CSMOKING",
    "obesity": "OBESITY",
    "binge_drinking": "BINGE",
    "no_leisure_pa": "LPA",
    "short_sleep": "SLEEP",
    "crc_screen": "COLON_SCREEN",
    "cervical_screen": "CERVICAL",
    "dentist": "DENTAL",
    "poor_health": "GHLTH",
    "phys_distress": "PHLTH",
    "mental_distress": "MHLTH",
    "food_insecurity": "FOODINSECU",
    "social_isolation": "ISOLATION",
    "any_disability": "DISABILITY",
    "mobility_disability": "MOBILITY",
    "selfcare_disability": "SELFCARE",
    "independent_living_disability": "INDEPLIVE",
    "snap_pct": "FOODSTAMP",
    "utility_shutoff_threat": "SHUTUTILITY",
    "housing_insecurity": "HOUSINSECU",
}

CDC_PLACES_TOKENS = set(CDC_PLACES_CODE_BY_TOKEN)


def _normalize_places_geoid(value, geographic_level):
    if value in (None, ""):
        return None
    s = str(value).strip()
    if not s:
        return None
    if "US" in s:
        s = s.split("US", 1)[1]
    # ZCTA values can arrive as ZCTA5 44106 or 860Z200US44106.
    digits = re.sub(r"\D", "", s)
    if geographic_level == "tract":
        return digits.zfill(11)[-11:] if digits else None
    if geographic_level == "county":
        return digits.zfill(5)[-5:] if digits else None
    if geographic_level == "zcta":
        return digits.zfill(5)[-5:] if digits else None
    if geographic_level == "place":
        # Ohio place FIPS is state(2)+place(5) = 7 digits.
        return digits.zfill(7)[-7:] if digits else s
    return s


def _first_existing_column_ci(columns, candidates):
    lower_map = {str(c).lower(): c for c in columns}
    for c in candidates:
        found = lower_map.get(str(c).lower())
        if found:
            return found
    return None


def _get_cdc_places_lookup(requested, geographic_level):
    """Return CDC PLACES estimates for tract, county, ZCTA, or place tables.

    This intentionally uses raw column discovery rather than Django model fields,
    because the county/ZCTA/place model classes currently define only their
    geographic ID field while the database tables contain the measure columns.
    """
    requested = set(requested or ()) & CDC_PLACES_TOKENS
    if not requested:
        return {}

    model = CDC_PLACES_MODEL_BY_GEO.get(geographic_level)
    if not model:
        return {}

    table_name = model._meta.db_table
    columns = _get_model_table_columns(model)
    if not columns:
        return {}

    geoid_col = _first_existing_column_ci(
        columns,
        CDC_PLACES_GEOID_CANDIDATES_BY_GEO.get(geographic_level, [])
    )
    if not geoid_col:
        return {}

    selected = {"geoid": geoid_col}
    col_roles = {}

    for token in requested:
        code = CDC_PLACES_CODE_BY_TOKEN[token]
        candidates = {
            "estimate": [f"{code}_CrudePrev", f"{code}_CrudePREV", f"{code}_crude_prev", f"{code}_crudeprev"],
            "ci_text": [f"{code}_Crude95CI", f"{code}_Crude_95CI", f"{code}_crude95ci", f"{code}_crude_95ci"],
            "age_adjusted": [f"{code}_AdjPrev", f"{code}_AdjPREV", f"{code}_AgeAdjPrev", f"{code}_AgeAdjPREV", f"{code}_AgeAdjustedPrev", f"{code}_ageadjprev"],
            "age_ci_text": [f"{code}_Adj95CI", f"{code}_Adj_95CI", f"{code}_AgeAdj95CI", f"{code}_AgeAdj_95CI", f"{code}_ageadj95ci"],
        }
        for role, role_candidates in candidates.items():
            col = _first_existing_column_ci(columns, role_candidates)
            if col:
                alias = f"{token}__{role}"
                selected[alias] = col
                col_roles[(token, role)] = alias

    select_sql = ", ".join(
        f"{_quote_ident(col)} AS {_quote_ident(alias)}"
        for alias, col in selected.items()
    )
    sql = f"SELECT {select_sql} FROM {_quote_ident(table_name)}"

    lookup = {}
    try:
        with connection.cursor() as cur:
            cur.execute(sql)
            colnames = [desc[0] for desc in cur.description]
            for db_row in cur.fetchall():
                row = dict(zip(colnames, db_row))
                geoid = _normalize_places_geoid(row.get("geoid"), geographic_level)
                if not geoid:
                    continue

                out = {}
                for token in requested:
                    spec = SUPPORT_MEASURE_OUTPUT_SPECS.get(token)
                    if not spec:
                        continue
                    value_key, ci_low_key, ci_high_key, age_adjusted_key = spec
                    est = row.get(col_roles.get((token, "estimate")))
                    ci_text = row.get(col_roles.get((token, "ci_text")))
                    age_est = row.get(col_roles.get((token, "age_adjusted")))

                    out[value_key] = _safe_round_float(est, 2)
                    lo, hi = _parse_places_ci(ci_text)
                    out[ci_low_key] = lo
                    out[ci_high_key] = hi
                    if age_adjusted_key:
                        out[age_adjusted_key] = _safe_round_float(age_est, 2)

                if out:
                    lookup[geoid] = out
    except Exception:
        return {}

    return lookup


def _get_most_recent_community_lookup(requested, geographic_level):
    requested = set(_normalize_support_measure_tokens(requested or []))
    out_by_geo = {}

    acs_requested = requested & COMMUNITY_ACS_TOKENS
    if acs_requested:
        for geoid, row in _get_acs_period_community_lookup(
            acs_requested,
            geographic_level,
            COMMUNITY_MOST_RECENT["acs"],
        ).items():
            out_by_geo.setdefault(geoid, {}).update(row)

    if "rurality" in requested:
        if geographic_level == "tract":
            rural_lookup = _get_generic_community_lookup("ruca", geographic_level, COMMUNITY_MOST_RECENT["ruca"])
        elif geographic_level == "county":
            rural_lookup = _get_generic_community_lookup("rucc", geographic_level, COMMUNITY_MOST_RECENT["rucc"])
        else:
            rural_lookup = {}
        for geoid, row in rural_lookup.items():
            out_by_geo.setdefault(geoid, {}).update(row)

    if "svi_adi" in requested:
        if geographic_level == "county":
            svi_lookup = _get_county_adi_lookup()
        else:
            svi_lookup = _get_generic_community_lookup("svi", geographic_level, COMMUNITY_MOST_RECENT["svi"])
        for geoid, row in svi_lookup.items():
            out_by_geo.setdefault(geoid, {}).update(row)

    return out_by_geo


@lru_cache(maxsize=64)
def _get_geo_support_lookups_cached(geographic_level, requested_tuple):
    requested = tuple(sorted(_normalize_support_measure_tokens(requested_tuple or [])))
    requested_set = set(requested)
    lookups = {}

    community_lookup = _get_most_recent_community_lookup(requested_set, geographic_level)
    if community_lookup:
        lookups["community_acs"] = community_lookup
        if "pop_total" in requested_set:
            lookups["pop"] = {g: row.get("total_population") for g, row in community_lookup.items()}
        if "sex_distribution" in requested_set:
            lookups["sex"] = {g: row for g, row in community_lookup.items()}
        if "median_age" in requested_set:
            lookups["median_age"] = {g: row.get("median_age") for g, row in community_lookup.items()}
        if "med_hh_income" in requested_set:
            lookups["income"] = {g: row.get("median_household_income") for g, row in community_lookup.items()}
        if "limited_english_pct" in requested_set:
            lookups["limited_english_pct"] = {g: row.get("limited_english_pct") for g, row in community_lookup.items()}
        if "race_eth" in requested_set:
            lookups["race_eth"] = {g: row for g, row in community_lookup.items()}

    places_lookup = _get_cdc_places_lookup(requested_set, geographic_level)
    if places_lookup:
        lookups["places"] = places_lookup

    # Space-based access tables currently exist at tract level only. For other
    # geographies, keep the selected output columns blank via
    # _add_display_option_columns rather than failing the results page.
    if geographic_level == "tract" and "pcp_access_score" in requested_set:
        try:
            lookups["pcp_access"] = {
                str(row["tract_geoid"]).strip(): row["weighted_sa_final"]
                for row in TravelTimeTract.objects.using("popcase_manual_etl").all().values("tract_geoid", "weighted_sa_final").iterator(chunk_size=5000)
                if row["tract_geoid"]
            }
        except Exception:
            lookups["pcp_access"] = {}

    if geographic_level == "tract" and "mammo_access" in requested_set:
        try:
            lookups["mammo_access"] = _get_tract_mammography_access_lookup(radius_miles=20.0)
        except Exception:
            lookups["mammo_access"] = {}

    return lookups


def _get_geo_support_lookups(geographic_level, requested_support_measures=None):
    normalized = tuple(sorted(_normalize_support_measure_tokens(requested_support_measures or [])))
    return _get_geo_support_lookups_cached(geographic_level, normalized)



# ---------------------------------------------------------
# COMMUNITY CHARACTERISTICS TIMEFRAME SELECTION
# ---------------------------------------------------------

COMMUNITY_ACS_PERIODS = [
    ("2009-2013", 2009, 2013),
    ("2014-2018", 2014, 2018),
    ("2019-2023", 2019, 2023),
]

COMMUNITY_MOST_RECENT = {
    "acs": "2019-2023",
    "rucc": "2023",
    "ruca": "2020",
    "svi": "2022",
}

COMMUNITY_ACS_TOKENS = {
    "pop_total", "sex_distribution", "median_age", "race_eth",
    "med_hh_income", "limited_english_pct",
    "per_capita_income", "poverty_pct", "snap_pct", "gini",
    "employment_16plus", "occupation_dist", "redlined_pct",
    "housing_unoccupied", "renting_pct", "median_year_built",
    "median_home_value", "internet_access", "moved_last_year",
}
COMMUNITY_RURALITY_TOKENS = {"rurality"}
COMMUNITY_SVI_TOKENS = {"svi_adi"}

COMMUNITY_BASE_OUTPUT_KEYS = {
    "total_population", "total_population_ci_lower", "total_population_ci_upper", "total_population_moe_90",
    "male_population", "female_population", "male_pct", "male_pct_ci_lower", "male_pct_ci_upper",
    "female_pct", "female_pct_ci_lower", "female_pct_ci_upper", "sex_distribution",
    "sex_distribution_ci_lower", "sex_distribution_ci_upper", "median_age", "median_age_ci_lower", "median_age_ci_upper",
    "median_household_income", "median_household_income_ci_lower", "median_household_income_ci_upper",
    "limited_english_pct", "limited_english_ci_lower", "limited_english_ci_upper",
    "per_capita_income", "per_capita_income_ci_lower", "per_capita_income_ci_upper",
    "poverty_pct", "poverty_ci_lower", "poverty_ci_upper",
    "snap_pct", "snap_ci_lower", "snap_ci_upper",
    "gini_index", "gini_ci_lower", "gini_ci_upper",
    "housing_unoccupied_pct", "housing_unoccupied_ci_lower", "housing_unoccupied_ci_upper",
    "renting_pct", "renting_ci_lower", "renting_ci_upper",
    "median_year_built", "median_year_built_ci_lower", "median_year_built_ci_upper",
    "median_home_value", "median_home_value_ci_lower", "median_home_value_ci_upper",
    "internet_access_pct", "internet_access_ci_lower", "internet_access_ci_upper",
    "moved_last_year_pct", "moved_last_year_ci_lower", "moved_last_year_ci_upper",
    "employment_16plus",
    "employment_labor_force_pct", "employment_labor_force_ci_lower", "employment_labor_force_ci_upper",
    "employment_employed_pct", "employment_employed_ci_lower", "employment_employed_ci_upper",
    "employment_unemployed_pct", "employment_unemployed_ci_lower", "employment_unemployed_ci_upper",
    "employment_not_in_labor_force_pct", "employment_not_in_labor_force_ci_lower", "employment_not_in_labor_force_ci_upper",
    "occupation_distribution",
    "occupation_management_business_science_arts_pct", "occupation_management_business_science_arts_ci_lower", "occupation_management_business_science_arts_ci_upper",
    "occupation_service_pct", "occupation_service_ci_lower", "occupation_service_ci_upper",
    "occupation_sales_office_pct", "occupation_sales_office_ci_lower", "occupation_sales_office_ci_upper",
    "occupation_natural_resources_construction_maintenance_pct", "occupation_natural_resources_construction_maintenance_ci_lower", "occupation_natural_resources_construction_maintenance_ci_upper",
    "occupation_production_transportation_material_moving_pct", "occupation_production_transportation_material_moving_ci_lower", "occupation_production_transportation_material_moving_ci_upper",
    "redlined_pct", "ranked_historic_redlining_index",
    "adi_pct_deciles_9_10", "adi_population_deciles_9_10", "adi_total_population",
    "race_ethnicity", "race_eth_ci_lower", "race_eth_ci_upper",
    "white_alone_pct", "white_alone_ci_lower", "white_alone_ci_upper",
    "black_alone_pct", "black_alone_ci_lower", "black_alone_ci_upper",
    "aian_alone_pct", "aian_alone_ci_lower", "aian_alone_ci_upper",
    "asian_alone_pct", "asian_alone_ci_lower", "asian_alone_ci_upper",
    "nhpi_alone_pct", "nhpi_alone_ci_lower", "nhpi_alone_ci_upper",
    "other_race_alone_pct", "other_race_alone_ci_lower", "other_race_alone_ci_upper",
    "multiracial_pct", "multiracial_ci_lower", "multiracial_ci_upper",
    "nh_white_pct", "nh_white_ci_lower", "nh_white_ci_upper",
    "hispanic_pct", "hispanic_ci_lower", "hispanic_ci_upper",
    "rurality", "rurality_description", "svi_adi", "svi_adi_ci_lower", "svi_adi_ci_upper",
}


def _normalize_community_timeframes(value):
    selected = [str(v).strip() for v in _as_list(value) if str(v).strip()]
    selected = [v for v in selected if v in {"most_recent", "historical"}]
    return selected or ["most_recent"]


def _as_int_year(value, default):
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _community_period_plan(dx_start, dx_end, selected_timeframes, geographic_level, support_measures):
    """Return community source periods selected by the Measures page options."""
    selected = set(_normalize_community_timeframes(selected_timeframes))
    support_set = set(_normalize_support_measure_tokens(support_measures or []))
    start = _as_int_year(dx_start, 2011)
    end = _as_int_year(dx_end, 2022)
    if start > end:
        start, end = end, start

    plan = []
    seen = set()

    def add(source, period):
        key = (source, str(period))
        if key not in seen:
            plan.append({"source": source, "period": str(period)})
            seen.add(key)

    # ACS-5: include the most recent period, and/or any period overlapping the
    # diagnosis-year range when Historical is selected.
    if support_set & COMMUNITY_ACS_TOKENS:
        if "most_recent" in selected:
            add("acs", COMMUNITY_MOST_RECENT["acs"])
        if "historical" in selected:
            for label, p_start, p_end in COMMUNITY_ACS_PERIODS:
                if start <= p_end and end >= p_start:
                    add("acs", label)

    # RUCC is county-only; RUCA is tract-only.
    if support_set & COMMUNITY_RURALITY_TOKENS:
        if geographic_level == "county":
            if "most_recent" in selected:
                add("rucc", COMMUNITY_MOST_RECENT["rucc"])
            if "historical" in selected:
                if end >= 2019:
                    add("rucc", "2023")
                if start <= 2018:
                    add("rucc", "2013")
        elif geographic_level == "tract":
            if "most_recent" in selected:
                add("ruca", COMMUNITY_MOST_RECENT["ruca"])
            if "historical" in selected:
                if end >= 2016:
                    add("ruca", "2020")
                if start <= 2015:
                    add("ruca", "2010")

    # SVI: one or both periods according to the diagnosis range midpoint rule.
    if support_set & COMMUNITY_SVI_TOKENS:
        if "most_recent" in selected:
            add("svi", COMMUNITY_MOST_RECENT["svi"])
        if "historical" in selected:
            if end >= 2018:
                add("svi", "2022")
            if start <= 2017:
                add("svi", "2012")

    return plan


def _community_period_suffix(source, period):
    return f"__{source}_{str(period).replace('-', '_')}"


def _community_geoid_from_geo_id(geo_id, geographic_level):
    if geo_id is None:
        return None
    s = str(geo_id).strip()
    if not s:
        return None
    if "US" in s:
        s = s.split("US", 1)[1]
    if geographic_level == "county":
        return s[-5:]
    if geographic_level == "tract":
        return s[-11:]
    if geographic_level == "zcta":
        return s[-5:]
    return s


def _table_exists(table_name):
    try:
        schema, table = _split_schema_table(table_name)
        with connection.cursor() as cur:
            if schema:
                cur.execute(
                    """
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = %s
                      AND table_name = %s
                    LIMIT 1
                    """,
                    [schema, table],
                )
            else:
                cur.execute(
                    """
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = ANY(current_schemas(false))
                      AND table_name = %s
                    LIMIT 1
                    """,
                    [table],
                )
            return cur.fetchone() is not None
    except Exception:
        return False


def _community_year_candidates(source, period):
    period = str(period)
    if source == "acs" and "-" in period:
        return [period, period.split("-")[-1]]
    return [period]


def _community_where_sql(table_name, geographic_level, source, period):
    colmap = _get_table_column_map(table_name)
    clauses = []
    params = []

    level_col = _first_existing_col(colmap, ["geographic_level", "geo_level", "geography_level"])
    if level_col:
        clauses.append(f'{_quote_identifier(level_col)} = %s')
        params.append(geographic_level)

    year_col = _first_existing_col(colmap, ["year", "ACSyear", "acs_year", "data_year", "svi_year", "rucc_year", "ruca_year"])
    candidates = _community_year_candidates(source, period)
    if year_col and candidates:
        placeholders = ",".join(["%s"] * len(candidates))
        clauses.append(f'{_quote_identifier(year_col)} IN ({placeholders})')
        params.extend(candidates)

    return (" AND ".join(clauses) if clauses else None), params


def _get_acs_b01001_community_lookup(requested, geographic_level="tract", acs_period="2019-2023"):
    requested = set(requested or ())
    if not (requested & {"pop_total", "sex_distribution", "median_age"}):
        return {}

    table = "acs_5yr_B01001"
    if not _table_exists(table):
        return {}

    base_cols = {
        "geo_id": ["GEO_ID", "geo_id", "geoid"],
        "total": ["B01001_001E"],
        "total_moe": ["B01001_001M"],
        "male": ["B01001_002E"],
        "male_moe": ["B01001_002M"],
        "female": ["B01001_026E"],
        "female_moe": ["B01001_026M"],
    }
    age_groups = [
        ("m_under5", "B01001_003E", "B01001_003M", 0, 5), ("m_5_9", "B01001_004E", "B01001_004M", 5, 10),
        ("m_10_14", "B01001_005E", "B01001_005M", 10, 15), ("m_15_17", "B01001_006E", "B01001_006M", 15, 18),
        ("m_18_19", "B01001_007E", "B01001_007M", 18, 20), ("m_20", "B01001_008E", "B01001_008M", 20, 21),
        ("m_21", "B01001_009E", "B01001_009M", 21, 22), ("m_22_24", "B01001_010E", "B01001_010M", 22, 25),
        ("m_25_29", "B01001_011E", "B01001_011M", 25, 30), ("m_30_34", "B01001_012E", "B01001_012M", 30, 35),
        ("m_35_39", "B01001_013E", "B01001_013M", 35, 40), ("m_40_44", "B01001_014E", "B01001_014M", 40, 45),
        ("m_45_49", "B01001_015E", "B01001_015M", 45, 50), ("m_50_54", "B01001_016E", "B01001_016M", 50, 55),
        ("m_55_59", "B01001_017E", "B01001_017M", 55, 60), ("m_60_61", "B01001_018E", "B01001_018M", 60, 62),
        ("m_62_64", "B01001_019E", "B01001_019M", 62, 65), ("m_65_66", "B01001_020E", "B01001_020M", 65, 67),
        ("m_67_69", "B01001_021E", "B01001_021M", 67, 70), ("m_70_74", "B01001_022E", "B01001_022M", 70, 75),
        ("m_75_79", "B01001_023E", "B01001_023M", 75, 80), ("m_80_84", "B01001_024E", "B01001_024M", 80, 85),
        ("m_85_plus", "B01001_025E", "B01001_025M", 85, 90),
        ("f_under5", "B01001_027E", "B01001_027M", 0, 5), ("f_5_9", "B01001_028E", "B01001_028M", 5, 10),
        ("f_10_14", "B01001_029E", "B01001_029M", 10, 15), ("f_15_17", "B01001_030E", "B01001_030M", 15, 18),
        ("f_18_19", "B01001_031E", "B01001_031M", 18, 20), ("f_20", "B01001_032E", "B01001_032M", 20, 21),
        ("f_21", "B01001_033E", "B01001_033M", 21, 22), ("f_22_24", "B01001_034E", "B01001_034M", 22, 25),
        ("f_25_29", "B01001_035E", "B01001_035M", 25, 30), ("f_30_34", "B01001_036E", "B01001_036M", 30, 35),
        ("f_35_39", "B01001_037E", "B01001_037M", 35, 40), ("f_40_44", "B01001_038E", "B01001_038M", 40, 45),
        ("f_45_49", "B01001_039E", "B01001_039M", 45, 50), ("f_50_54", "B01001_040E", "B01001_040M", 50, 55),
        ("f_55_59", "B01001_041E", "B01001_041M", 55, 60), ("f_60_61", "B01001_042E", "B01001_042M", 60, 62),
        ("f_62_64", "B01001_043E", "B01001_043M", 62, 65), ("f_65_66", "B01001_044E", "B01001_044M", 65, 67),
        ("f_67_69", "B01001_045E", "B01001_045M", 67, 70), ("f_70_74", "B01001_046E", "B01001_046M", 70, 75),
        ("f_75_79", "B01001_047E", "B01001_047M", 75, 80), ("f_80_84", "B01001_048E", "B01001_048M", 80, 85),
        ("f_85_plus", "B01001_049E", "B01001_049M", 85, 90),
    ]
    cols = dict(base_cols)
    if "median_age" in requested:
        for alias, e_col, m_col, _, _ in age_groups:
            cols[alias] = [e_col]
            cols[f"{alias}_moe"] = [m_col]

    where, params = _community_where_sql(table, geographic_level, "acs", acs_period)
    rows, aliases = _select_columns_from_table(table, cols, where, params)

    def val(row, alias):
        col = aliases.get(alias)
        return row.get(col) if col else None

    lookup = {}
    for row in rows:
        geoid = _community_geoid_from_geo_id(val(row, "geo_id"), geographic_level)
        if not geoid:
            continue
        total = val(row, "total")
        total_moe = val(row, "total_moe")
        male = val(row, "male")
        male_moe = val(row, "male_moe")
        female = val(row, "female")
        female_moe = val(row, "female_moe")
        out = {}
        if "pop_total" in requested:
            out["total_population"] = total
            out["total_population_moe_90"] = total_moe
            lo, hi = _acs_estimate_ci(total, total_moe, floor_zero=True, ndigits=0)
            out["total_population_ci_lower"] = lo
            out["total_population_ci_upper"] = hi
        if "sex_distribution" in requested:
            out["male_population"] = male
            out["female_population"] = female
            out["male_pct"] = _safe_pct(male, total)
            out["female_pct"] = _safe_pct(female, total)
            out["male_pct_ci_lower"], out["male_pct_ci_upper"] = _acs_pct_ci_from_num_denom(male, male_moe, total, total_moe)
            out["female_pct_ci_lower"], out["female_pct_ci_upper"] = _acs_pct_ci_from_num_denom(female, female_moe, total, total_moe)
            out["sex_distribution"] = "Male/Female"
            out["sex_distribution_ci_lower"] = None
            out["sex_distribution_ci_upper"] = None
        if "median_age" in requested:
            bins = defaultdict(lambda: [0.0, 0.0])
            for alias, _, _, lower, upper in age_groups:
                est = _safe_num(val(row, alias)) or 0.0
                moe95 = _acs_moe_95(val(row, f"{alias}_moe")) or 0.0
                bins[(lower, upper)][0] += est
                bins[(lower, upper)][1] = math.sqrt((bins[(lower, upper)][1] ** 2) + (moe95 ** 2))
            counts_est = [(lo, hi, est_moe[0]) for (lo, hi), est_moe in sorted(bins.items())]
            counts_low = [(lo, hi, max(0.0, est_moe[0] - est_moe[1])) for (lo, hi), est_moe in sorted(bins.items())]
            counts_high = [(lo, hi, est_moe[0] + est_moe[1]) for (lo, hi), est_moe in sorted(bins.items())]
            out["median_age"] = _estimate_grouped_median_age(counts_est)
            low_med = _estimate_grouped_median_age(counts_low)
            high_med = _estimate_grouped_median_age(counts_high)
            out["median_age_ci_lower"] = min(low_med, high_med) if low_med is not None and high_med is not None else None
            out["median_age_ci_upper"] = max(low_med, high_med) if low_med is not None and high_med is not None else None
        if out:
            lookup[geoid] = out
    return lookup


def _get_acs_income_community_lookup(geographic_level="tract", acs_period="2019-2023"):
    table = "acs_5yr_B19013"
    if not _table_exists(table):
        return {}
    cols = {"geo_id": ["GEO_ID", "geo_id", "geoid"], "income": ["B19013_001E"], "income_moe": ["B19013_001M"]}
    where, params = _community_where_sql(table, geographic_level, "acs", acs_period)
    rows, aliases = _select_columns_from_table(table, cols, where, params)
    def val(row, alias):
        col = aliases.get(alias)
        return row.get(col) if col else None
    lookup = {}
    for row in rows:
        geoid = _community_geoid_from_geo_id(val(row, "geo_id"), geographic_level)
        if not geoid:
            continue
        income = val(row, "income")
        lo, hi = _acs_estimate_ci(income, val(row, "income_moe"), floor_zero=True, ndigits=0)
        lookup[geoid] = {"median_household_income": income, "median_household_income_ci_lower": lo, "median_household_income_ci_upper": hi}
    return lookup


def _get_acs_limited_english_community_lookup(geographic_level="tract", acs_period="2019-2023"):
    table = "acs_5yr_C16001"
    if not _table_exists(table):
        return {}
    cols = {
        "geo_id": ["GEO_ID", "geo_id", "geoid"],
        "denom": ["C16001_001E"], "denom_moe": ["C16001_001M"],
        "num": ["C16001_004E"], "num_moe": ["C16001_004M"],
    }
    where, params = _community_where_sql(table, geographic_level, "acs", acs_period)
    rows, aliases = _select_columns_from_table(table, cols, where, params)
    def val(row, alias):
        col = aliases.get(alias)
        return row.get(col) if col else None
    lookup = {}
    for row in rows:
        geoid = _community_geoid_from_geo_id(val(row, "geo_id"), geographic_level)
        if not geoid:
            continue
        num, denom = val(row, "num"), val(row, "denom")
        lo, hi = _acs_pct_ci_from_num_denom(num, val(row, "num_moe"), denom, val(row, "denom_moe"))
        lookup[geoid] = {"limited_english_pct": _safe_pct(num, denom), "limited_english_ci_lower": lo, "limited_english_ci_upper": hi}
    return lookup


def _fetch_acs_total_moe_lookup_for_period(table_name, geographic_level="tract", acs_period="2019-2023"):
    spec = RACE_TABLE_SPECS.get(table_name)
    if not spec or not _table_exists(table_name):
        return {}
    where, params = _community_where_sql(table_name, geographic_level, "acs", acs_period)
    rows, aliases = _select_columns_from_table(
        table_name,
        {"geo": [spec["geo_col"], "GEO_ID", "geo_id"], "estimate": [spec["total_col"]], "moe": [spec.get("moe_col") or spec["total_col"].replace("_001E", "_001M")]},
        where,
        params,
    )
    def val(row, alias):
        col = aliases.get(alias)
        return row.get(col) if col else None
    lookup = {}
    for row in rows:
        geoid = _community_geoid_from_geo_id(val(row, "geo"), geographic_level)
        if not geoid:
            continue
        lookup[geoid] = {"estimate": _safe_num(val(row, "estimate")) or 0.0, "moe": val(row, "moe")}
    return lookup


def _get_race_ethnicity_community_lookup(geographic_level="tract", acs_period="2019-2023"):
    total_lookup = {}
    for geoid, row in _get_acs_b01001_community_lookup(("pop_total",), geographic_level, acs_period).items():
        total_lookup[geoid] = {"estimate": row.get("total_population"), "moe": row.get("total_population_moe_90")}
    race_specs = {
        "white_alone": ("acs_5yr_B01001A", "white_alone_pct", "white_alone_ci_lower", "white_alone_ci_upper"),
        "black_alone": ("acs_5yr_B01001B", "black_alone_pct", "black_alone_ci_lower", "black_alone_ci_upper"),
        "aian_alone": ("acs_5yr_B01001C", "aian_alone_pct", "aian_alone_ci_lower", "aian_alone_ci_upper"),
        "asian_alone": ("acs_5yr_B01001D", "asian_alone_pct", "asian_alone_ci_lower", "asian_alone_ci_upper"),
        "nhpi_alone": ("acs_5yr_B01001E", "nhpi_alone_pct", "nhpi_alone_ci_lower", "nhpi_alone_ci_upper"),
        "other_race_alone": ("acs_5yr_B01001F", "other_race_alone_pct", "other_race_alone_ci_lower", "other_race_alone_ci_upper"),
        "multiracial": ("acs_5yr_B01001G", "multiracial_pct", "multiracial_ci_lower", "multiracial_ci_upper"),
        "nh_white": ("acs_5yr_B01001H", "nh_white_pct", "nh_white_ci_lower", "nh_white_ci_upper"),
        "hispanic": ("acs_5yr_B01001I", "hispanic_pct", "hispanic_ci_lower", "hispanic_ci_upper"),
    }
    race_lookups = {name: _fetch_acs_total_moe_lookup_for_period(table, geographic_level, acs_period) for name, (table, _, _, _) in race_specs.items()}
    all_geoids = set(total_lookup.keys())
    for lu in race_lookups.values():
        all_geoids |= set(lu.keys())
    lookup = {}
    for geoid in all_geoids:
        total = (total_lookup.get(geoid) or {}).get("estimate")
        total_moe = (total_lookup.get(geoid) or {}).get("moe")
        row_out = {}
        for name, (_, pct_key, low_key, high_key) in race_specs.items():
            race_row = race_lookups.get(name, {}).get(geoid, {})
            estimate = race_row.get("estimate")
            moe = race_row.get("moe")
            row_out[pct_key] = _safe_pct(estimate, total)
            row_out[low_key], row_out[high_key] = _acs_pct_ci_from_num_denom(estimate, moe, total, total_moe)
        row_out["race_ethnicity"] = "Race/Ethnicity percentages"
        row_out["race_eth_ci_lower"] = None
        row_out["race_eth_ci_upper"] = None
        lookup[geoid] = row_out
    return lookup




def _acs_lookup_value(row, aliases, alias):
    col = aliases.get(alias)
    return row.get(col) if col else None


def _acs_period_end_year(acs_period):
    period = str(acs_period)
    if "-" in period:
        return period.split("-")[-1]
    return period


def _candidate_acs_tables(base_table, geographic_level="tract", acs_period="2019-2023"):
    """Return likely physical ACS tables for standard and census_build ETL layouts."""
    base = str(base_table).replace("acs_5yr_", "").replace("acs_5y_", "")
    base = base.upper()
    level = {
        "county": "county",
        "tract": "tract",
        "zcta": "zcta",
        "place": "place",
        "state": "state",
        "block_group": "block_group",
    }.get(geographic_level, geographic_level)
    end_year = _acs_period_end_year(acs_period)
    return [
        f"census_build.acs_39_{base}_{level}_{end_year}",
        f"acs_5yr_{base}",
        f"acs_5y_{base.lower()}",
        f"acs_5y_{base}",
    ]


def _resolve_acs_table(base_table, geographic_level="tract", acs_period="2019-2023"):
    for table in _candidate_acs_tables(base_table, geographic_level, acs_period):
        if _table_exists(table):
            return table
    return None


def _get_acs_single_estimate_community_lookup(
    table,
    estimate_candidates,
    moe_candidates,
    out_key,
    ci_lower_key,
    ci_upper_key,
    geographic_level="tract",
    acs_period="2019-2023",
    floor_zero=True,
    ndigits=2,
):
    """
    Generic ACS single-estimate reader, e.g. per-capita income, GINI,
    median year built, and median home value. Uses ACS MOE columns to derive
    approximate 95% bounds.
    """
    if not _table_exists(table):
        return {}

    cols = {
        "geo_id": ["GEO_ID", "geo_id", "geoid", "GEOID"],
        "estimate": estimate_candidates,
        "moe": moe_candidates,
    }
    where, params = _community_where_sql(table, geographic_level, "acs", acs_period)
    rows, aliases = _select_columns_from_table(table, cols, where, params)

    lookup = {}
    for row in rows:
        geoid = _community_geoid_from_geo_id(_acs_lookup_value(row, aliases, "geo_id"), geographic_level)
        if not geoid:
            continue
        est = _acs_lookup_value(row, aliases, "estimate")
        lo, hi = _acs_estimate_ci(
            est,
            _acs_lookup_value(row, aliases, "moe"),
            floor_zero=floor_zero,
            ndigits=ndigits,
        )
        lookup[geoid] = {
            out_key: _safe_round_float(est, ndigits),
            ci_lower_key: lo,
            ci_upper_key: hi,
        }
    return lookup


def _get_acs_percentage_community_lookup(
    table,
    numerator_candidates,
    denominator_candidates,
    numerator_moe_candidates,
    denominator_moe_candidates,
    out_key,
    ci_lower_key,
    ci_upper_key,
    geographic_level="tract",
    acs_period="2019-2023",
):
    """
    Generic ACS percentage reader from numerator/denominator estimates and
    MOEs. This powers scalar community characteristics such as poverty, SNAP,
    housing vacancy, renting, internet access, and moved-in-last-year.
    """
    if not _table_exists(table):
        return {}

    cols = {
        "geo_id": ["GEO_ID", "geo_id", "geoid", "GEOID"],
        "num": numerator_candidates,
        "denom": denominator_candidates,
        "num_moe": numerator_moe_candidates,
        "denom_moe": denominator_moe_candidates,
    }
    where, params = _community_where_sql(table, geographic_level, "acs", acs_period)
    rows, aliases = _select_columns_from_table(table, cols, where, params)

    lookup = {}
    for row in rows:
        geoid = _community_geoid_from_geo_id(_acs_lookup_value(row, aliases, "geo_id"), geographic_level)
        if not geoid:
            continue
        num = _acs_lookup_value(row, aliases, "num")
        denom = _acs_lookup_value(row, aliases, "denom")
        lo, hi = _acs_pct_ci_from_num_denom(
            num,
            _acs_lookup_value(row, aliases, "num_moe"),
            denom,
            _acs_lookup_value(row, aliases, "denom_moe"),
        )
        lookup[geoid] = {
            out_key: _safe_pct(num, denom),
            ci_lower_key: lo,
            ci_upper_key: hi,
        }
    return lookup


def _get_acs_complement_percentage_community_lookup(
    table,
    complement_candidates,
    denominator_candidates,
    complement_moe_candidates,
    denominator_moe_candidates,
    out_key,
    ci_lower_key,
    ci_upper_key,
    geographic_level="tract",
    acs_period="2019-2023",
):
    """
    Generic ACS percentage reader for measures defined as 100% minus a
    published subgroup, such as % moved in last year = 100% - same-house.
    The MOE for the complement uses the subgroup MOE as an approximation.
    """
    if not _table_exists(table):
        return {}

    cols = {
        "geo_id": ["GEO_ID", "geo_id", "geoid", "GEOID"],
        "same": complement_candidates,
        "denom": denominator_candidates,
        "same_moe": complement_moe_candidates,
        "denom_moe": denominator_moe_candidates,
    }
    where, params = _community_where_sql(table, geographic_level, "acs", acs_period)
    rows, aliases = _select_columns_from_table(table, cols, where, params)

    lookup = {}
    for row in rows:
        geoid = _community_geoid_from_geo_id(_acs_lookup_value(row, aliases, "geo_id"), geographic_level)
        if not geoid:
            continue
        denom = _safe_num(_acs_lookup_value(row, aliases, "denom"))
        same = _safe_num(_acs_lookup_value(row, aliases, "same"))
        if denom is None or same is None:
            continue
        moved = max(0.0, denom - same)
        lo, hi = _acs_pct_ci_from_num_denom(
            moved,
            _acs_lookup_value(row, aliases, "same_moe"),
            denom,
            _acs_lookup_value(row, aliases, "denom_moe"),
        )
        lookup[geoid] = {
            out_key: _safe_pct(moved, denom),
            ci_lower_key: lo,
            ci_upper_key: hi,
        }
    return lookup


def _get_acs_per_capita_income_community_lookup(geographic_level="tract", acs_period="2019-2023"):
    return _get_acs_single_estimate_community_lookup(
        _resolve_acs_table("B19301", geographic_level, acs_period) or "acs_5yr_B19301",
        ["B19301_001E"],
        ["B19301_001M"],
        "per_capita_income",
        "per_capita_income_ci_lower",
        "per_capita_income_ci_upper",
        geographic_level,
        acs_period,
        floor_zero=True,
        ndigits=0,
    )


def _get_acs_poverty_community_lookup(geographic_level="tract", acs_period="2019-2023"):
    # Prefer household poverty table B17017. Fall back to person-level B17001
    # when only that table is loaded.
    primary = _get_acs_percentage_community_lookup(
        _resolve_acs_table("B17017", geographic_level, acs_period) or "acs_5yr_B17017",
        ["B17017_002E"],
        ["B17017_001E"],
        ["B17017_002M"],
        ["B17017_001M"],
        "poverty_pct",
        "poverty_ci_lower",
        "poverty_ci_upper",
        geographic_level,
        acs_period,
    )
    if primary:
        return primary
    return _get_acs_percentage_community_lookup(
        _resolve_acs_table("B17001", geographic_level, acs_period) or "acs_5yr_B17001",
        ["B17001_002E"],
        ["B17001_001E"],
        ["B17001_002M"],
        ["B17001_001M"],
        "poverty_pct",
        "poverty_ci_lower",
        "poverty_ci_upper",
        geographic_level,
        acs_period,
    )


def _get_acs_snap_community_lookup(geographic_level="tract", acs_period="2019-2023"):
    return _get_acs_percentage_community_lookup(
        _resolve_acs_table("B22010", geographic_level, acs_period) or "acs_5yr_B22010",
        ["B22010_002E"],
        ["B22010_001E"],
        ["B22010_002M"],
        ["B22010_001M"],
        "snap_pct",
        "snap_ci_lower",
        "snap_ci_upper",
        geographic_level,
        acs_period,
    )


def _get_acs_gini_community_lookup(geographic_level="tract", acs_period="2019-2023"):
    return _get_acs_single_estimate_community_lookup(
        _resolve_acs_table("B19083", geographic_level, acs_period) or "acs_5yr_B19083",
        ["B19083_001E"],
        ["B19083_001M"],
        "gini_index",
        "gini_ci_lower",
        "gini_ci_upper",
        geographic_level,
        acs_period,
        floor_zero=False,
        ndigits=3,
    )


def _get_acs_housing_unoccupied_community_lookup(geographic_level="tract", acs_period="2019-2023"):
    return _get_acs_percentage_community_lookup(
        _resolve_acs_table("B25002", geographic_level, acs_period) or "acs_5yr_B25002",
        ["B25002_003E"],
        ["B25002_001E"],
        ["B25002_003M"],
        ["B25002_001M"],
        "housing_unoccupied_pct",
        "housing_unoccupied_ci_lower",
        "housing_unoccupied_ci_upper",
        geographic_level,
        acs_period,
    )


def _get_acs_renting_community_lookup(geographic_level="tract", acs_period="2019-2023"):
    return _get_acs_percentage_community_lookup(
        _resolve_acs_table("B25003", geographic_level, acs_period) or "acs_5yr_B25003",
        ["B25003_003E"],
        ["B25003_001E"],
        ["B25003_003M"],
        ["B25003_001M"],
        "renting_pct",
        "renting_ci_lower",
        "renting_ci_upper",
        geographic_level,
        acs_period,
    )


def _get_acs_median_year_built_community_lookup(geographic_level="tract", acs_period="2019-2023"):
    return _get_acs_single_estimate_community_lookup(
        _resolve_acs_table("B25035", geographic_level, acs_period) or "acs_5yr_B25035",
        ["B25035_001E"],
        ["B25035_001M"],
        "median_year_built",
        "median_year_built_ci_lower",
        "median_year_built_ci_upper",
        geographic_level,
        acs_period,
        floor_zero=False,
        ndigits=0,
    )


def _get_acs_median_home_value_community_lookup(geographic_level="tract", acs_period="2019-2023"):
    return _get_acs_single_estimate_community_lookup(
        _resolve_acs_table("B25077", geographic_level, acs_period) or "acs_5yr_B25077",
        ["B25077_001E"],
        ["B25077_001M"],
        "median_home_value",
        "median_home_value_ci_lower",
        "median_home_value_ci_upper",
        geographic_level,
        acs_period,
        floor_zero=True,
        ndigits=0,
    )


def _get_acs_internet_access_community_lookup(geographic_level="tract", acs_period="2019-2023"):
    return _get_acs_percentage_community_lookup(
        _resolve_acs_table("B28002", geographic_level, acs_period) or "acs_5yr_B28002",
        ["B28002_002E"],
        ["B28002_001E"],
        ["B28002_002M"],
        ["B28002_001M"],
        "internet_access_pct",
        "internet_access_ci_lower",
        "internet_access_ci_upper",
        geographic_level,
        acs_period,
    )


def _get_acs_moved_last_year_community_lookup(geographic_level="tract", acs_period="2019-2023"):
    # In ACS B07003, B07003_002E is commonly "Same house 1 year ago".
    # Therefore moved in the last year is calculated as total - same-house.
    return _get_acs_complement_percentage_community_lookup(
        _resolve_acs_table("B07003", geographic_level, acs_period) or "acs_5yr_B07003",
        ["B07003_002E"],
        ["B07003_001E"],
        ["B07003_002M"],
        ["B07003_001M"],
        "moved_last_year_pct",
        "moved_last_year_ci_lower",
        "moved_last_year_ci_upper",
        geographic_level,
        acs_period,
    )


def _merge_pct_component(row, aliases, denom_alias, num_alias, denom_moe_alias, num_moe_alias, out, value_key, low_key, high_key):
    denom = _safe_num(_acs_lookup_value(row, aliases, denom_alias))
    num = _safe_num(_acs_lookup_value(row, aliases, num_alias))
    out[value_key] = _safe_pct(num, denom)
    lo, hi = _acs_pct_ci_from_num_denom(
        num,
        _acs_lookup_value(row, aliases, num_moe_alias),
        denom,
        _acs_lookup_value(row, aliases, denom_moe_alias),
    )
    out[low_key] = lo
    out[high_key] = hi


def _get_acs_employment_community_lookup(geographic_level="tract", acs_period="2019-2023"):
    table = _resolve_acs_table("B23025", geographic_level, acs_period)
    if not table:
        return {}
    cols = {
        "geo_id": ["GEO_ID", "geo_id", "geoid", "GEOID"],
        "denom": ["B23025_001E"], "denom_moe": ["B23025_001M"],
        "labor": ["B23025_002E"], "labor_moe": ["B23025_002M"],
        "employed": ["B23025_004E"], "employed_moe": ["B23025_004M"],
        "unemployed": ["B23025_005E"], "unemployed_moe": ["B23025_005M"],
        "not_labor": ["B23025_007E"], "not_labor_moe": ["B23025_007M"],
    }
    where, params = _community_where_sql(table, geographic_level, "acs", acs_period)
    rows, aliases = _select_columns_from_table(table, cols, where, params)
    lookup = {}
    for row in rows:
        geoid = _community_geoid_from_geo_id(_acs_lookup_value(row, aliases, "geo_id"), geographic_level)
        if not geoid:
            continue
        out = {"employment_16plus": "Employment status percentages"}
        _merge_pct_component(row, aliases, "denom", "labor", "denom_moe", "labor_moe", out,
                             "employment_labor_force_pct", "employment_labor_force_ci_lower", "employment_labor_force_ci_upper")
        _merge_pct_component(row, aliases, "denom", "employed", "denom_moe", "employed_moe", out,
                             "employment_employed_pct", "employment_employed_ci_lower", "employment_employed_ci_upper")
        _merge_pct_component(row, aliases, "denom", "unemployed", "denom_moe", "unemployed_moe", out,
                             "employment_unemployed_pct", "employment_unemployed_ci_lower", "employment_unemployed_ci_upper")
        _merge_pct_component(row, aliases, "denom", "not_labor", "denom_moe", "not_labor_moe", out,
                             "employment_not_in_labor_force_pct", "employment_not_in_labor_force_ci_lower", "employment_not_in_labor_force_ci_upper")
        lookup[geoid] = out
    return lookup


def _get_acs_occupation_community_lookup(geographic_level="tract", acs_period="2019-2023"):
    table = _resolve_acs_table("C24010", geographic_level, acs_period)
    if not table:
        return {}
    # C24010 has sex-stratified occupation rows. Aggregate male + female major
    # categories to keep the PopCASE output compact and stable.
    cols = {
        "geo_id": ["GEO_ID", "geo_id", "geoid", "GEOID"],
        "denom": ["C24010_001E"], "denom_moe": ["C24010_001M"],
        "mgmt_m": ["C24010_003E"], "mgmt_m_moe": ["C24010_003M"],
        "service_m": ["C24010_019E"], "service_m_moe": ["C24010_019M"],
        "sales_m": ["C24010_027E"], "sales_m_moe": ["C24010_027M"],
        "nr_m": ["C24010_030E"], "nr_m_moe": ["C24010_030M"],
        "prod_m": ["C24010_034E"], "prod_m_moe": ["C24010_034M"],
        "mgmt_f": ["C24010_039E"], "mgmt_f_moe": ["C24010_039M"],
        "service_f": ["C24010_055E"], "service_f_moe": ["C24010_055M"],
        "sales_f": ["C24010_063E"], "sales_f_moe": ["C24010_063M"],
        "nr_f": ["C24010_066E"], "nr_f_moe": ["C24010_066M"],
        "prod_f": ["C24010_070E"], "prod_f_moe": ["C24010_070M"],
    }
    where, params = _community_where_sql(table, geographic_level, "acs", acs_period)
    rows, aliases = _select_columns_from_table(table, cols, where, params)

    def est(row, alias):
        return _safe_num(_acs_lookup_value(row, aliases, alias)) or 0.0

    def moe90_combined(row, a, b):
        ma = _safe_num(_acs_lookup_value(row, aliases, a)) or 0.0
        mb = _safe_num(_acs_lookup_value(row, aliases, b)) or 0.0
        return math.sqrt((ma ** 2) + (mb ** 2))

    lookup = {}
    categories = [
        ("occupation_management_business_science_arts_pct", "occupation_management_business_science_arts_ci_lower", "occupation_management_business_science_arts_ci_upper", "mgmt_m", "mgmt_f", "mgmt_m_moe", "mgmt_f_moe"),
        ("occupation_service_pct", "occupation_service_ci_lower", "occupation_service_ci_upper", "service_m", "service_f", "service_m_moe", "service_f_moe"),
        ("occupation_sales_office_pct", "occupation_sales_office_ci_lower", "occupation_sales_office_ci_upper", "sales_m", "sales_f", "sales_m_moe", "sales_f_moe"),
        ("occupation_natural_resources_construction_maintenance_pct", "occupation_natural_resources_construction_maintenance_ci_lower", "occupation_natural_resources_construction_maintenance_ci_upper", "nr_m", "nr_f", "nr_m_moe", "nr_f_moe"),
        ("occupation_production_transportation_material_moving_pct", "occupation_production_transportation_material_moving_ci_lower", "occupation_production_transportation_material_moving_ci_upper", "prod_m", "prod_f", "prod_m_moe", "prod_f_moe"),
    ]
    for row in rows:
        geoid = _community_geoid_from_geo_id(_acs_lookup_value(row, aliases, "geo_id"), geographic_level)
        if not geoid:
            continue
        denom = _safe_num(_acs_lookup_value(row, aliases, "denom"))
        denom_moe = _acs_lookup_value(row, aliases, "denom_moe")
        out = {"occupation_distribution": "Occupation percentages"}
        for value_key, low_key, high_key, am, af, mm, mf in categories:
            num = est(row, am) + est(row, af)
            num_moe = moe90_combined(row, mm, mf)
            out[value_key] = _safe_pct(num, denom)
            out[low_key], out[high_key] = _acs_pct_ci_from_num_denom(num, num_moe, denom, denom_moe)
        lookup[geoid] = out
    return lookup


def _get_county_adi_lookup():
    table = 'popcaseui.county'
    # This table is in the manual ETL database, not the default popcase DB.
    try:
        with connections['popcase_manual_etl'].cursor() as cur:
            cur.execute('SELECT geoid, pct_adi910, adi_pop, tot_pop FROM popcaseui.county')
            rows = cur.fetchall()
    except Exception:
        return {}
    lookup = {}
    for geoid, pct, adi_pop, tot_pop in rows:
        county = _normalize_plain_fips(geoid, target_len=5)
        if not county:
            continue
        lookup[county] = {
            'svi_adi': _safe_round_float(pct, 2),
            'adi_pct_deciles_9_10': _safe_round_float(pct, 2),
            'adi_population_deciles_9_10': _safe_round_float(adi_pop, 0),
            'adi_total_population': _safe_round_float(tot_pop, 0),
        }
    return lookup


def _read_redlining_hri_tract_table(period='2020'):
    """Return direct tract-level Historic Redlining Index rows for Ohio."""
    period = str(period)
    if period == '2010':
        table = 'redlined_hri2010_shapefiles'
        geo_col, hri_col, rhri_col = 'geoid10', 'hri2010', 'rhri10'
    else:
        table = 'redlined_hri2020_shapefiles'
        geo_col, hri_col, rhri_col = 'geoid20', 'hri2020', 'rhri2020'
    if not _table_exists(table):
        return {}

    sql = (
        f'SELECT {_quote_identifier(geo_col)}, {_quote_identifier(hri_col)}, {_quote_identifier(rhri_col)} '
        f'FROM {_quote_table_identifier(table)}'
    )
    lookup = {}
    try:
        with connection.cursor() as cur:
            cur.execute(sql)
            for geoid, hri, rhri in cur.fetchall():
                tract = _normalize_plain_fips(geoid, target_len=11)
                if tract and tract.startswith("39"):
                    lookup[tract] = {
                        'redlined_pct': _safe_round_float(hri, 3),
                        'ranked_historic_redlining_index': _safe_round_float(rhri, 3),
                    }
    except Exception:
        return {}
    return lookup


def _get_tract_population_lookup_for_hri(acs_period='2019-2023'):
    """Return tract population for weighting HRI county aggregation."""
    table = _resolve_acs_table('B01001', 'tract', acs_period)
    if not table:
        return {}
    cols = {
        'geo_id': ['GEO_ID', 'geo_id', 'geoid', 'GEOID'],
        'population': ['B01001_001E'],
    }
    where, params = _community_where_sql(table, 'tract', 'acs', acs_period)
    rows, aliases = _select_columns_from_table(table, cols, where, params)

    lookup = {}
    for row in rows:
        tract = _community_geoid_from_geo_id(_acs_lookup_value(row, aliases, 'geo_id'), 'tract')
        pop = _safe_num(_acs_lookup_value(row, aliases, 'population'))
        if tract and tract.startswith('39') and pop is not None and pop > 0:
            lookup[tract] = pop
    return lookup


def _aggregate_redlining_hri_to_county(tract_lookup, acs_period='2019-2023'):
    """Aggregate tract HRI to county using population weights when available."""
    if not tract_lookup:
        return {}

    pop_lookup = _get_tract_population_lookup_for_hri(acs_period)
    county_acc = defaultdict(lambda: {'hri_wsum': 0.0, 'rhri_wsum': 0.0, 'weight_sum': 0.0, 'hri_vals': [], 'rhri_vals': []})

    for tract, row in tract_lookup.items():
        county = str(tract)[:5]
        hri = _safe_num(row.get('redlined_pct'))
        rhri = _safe_num(row.get('ranked_historic_redlining_index'))
        if hri is None and rhri is None:
            continue

        weight = pop_lookup.get(tract)
        acc = county_acc[county]
        if weight is not None and weight > 0:
            if hri is not None:
                acc['hri_wsum'] += hri * weight
            if rhri is not None:
                acc['rhri_wsum'] += rhri * weight
            acc['weight_sum'] += weight
        if hri is not None:
            acc['hri_vals'].append(hri)
        if rhri is not None:
            acc['rhri_vals'].append(rhri)

    out = {}
    for county, acc in county_acc.items():
        if acc['weight_sum'] > 0:
            hri_val = acc['hri_wsum'] / acc['weight_sum'] if acc['hri_wsum'] else None
            rhri_val = acc['rhri_wsum'] / acc['weight_sum'] if acc['rhri_wsum'] else None
        else:
            hri_val = sum(acc['hri_vals']) / len(acc['hri_vals']) if acc['hri_vals'] else None
            rhri_val = sum(acc['rhri_vals']) / len(acc['rhri_vals']) if acc['rhri_vals'] else None

        out[county] = {
            'redlined_pct': _safe_round_float(hri_val, 3),
            'ranked_historic_redlining_index': _safe_round_float(rhri_val, 3),
        }
    return out


def _get_redlining_hri_lookup(geographic_level='tract', period='2020', acs_period='2019-2023'):
    """
    Return Historic Redlining Index by geography.

    - Tract: direct 2020 HRI lookup, falling back to 2010 for missing tracts.
    - County: aggregate tract HRI values by county; use ACS tract population
      weights when available, otherwise a simple mean.
    - ZCTA/place: left blank until a valid crosswalk or direct table is added.
    """
    if geographic_level not in {'tract', 'county'}:
        return {}

    period = str(period)
    if period == '2010':
        tract_lookup = _read_redlining_hri_tract_table('2010')
    else:
        tract_lookup = _read_redlining_hri_tract_table('2020')
        fallback_2010 = _read_redlining_hri_tract_table('2010')
        for tract, row in fallback_2010.items():
            tract_lookup.setdefault(tract, row)

    if geographic_level == 'tract':
        return tract_lookup

    return _aggregate_redlining_hri_to_county(tract_lookup, acs_period)


def _get_acs_period_community_lookup(requested, geographic_level, acs_period):
    requested = set(requested or [])
    lookup = {}

    def merge(rows):
        for geoid, row in rows.items():
            lookup.setdefault(geoid, {}).update(row)

    if requested & {"pop_total", "sex_distribution", "median_age"}:
        merge(_get_acs_b01001_community_lookup(requested, geographic_level, acs_period))
    if "race_eth" in requested:
        merge(_get_race_ethnicity_community_lookup(geographic_level, acs_period))
    if "med_hh_income" in requested:
        merge(_get_acs_income_community_lookup(geographic_level, acs_period))
    if "limited_english_pct" in requested:
        merge(_get_acs_limited_english_community_lookup(geographic_level, acs_period))

    if "per_capita_income" in requested:
        merge(_get_acs_per_capita_income_community_lookup(geographic_level, acs_period))
    if "poverty_pct" in requested:
        merge(_get_acs_poverty_community_lookup(geographic_level, acs_period))
    if "snap_pct" in requested:
        merge(_get_acs_snap_community_lookup(geographic_level, acs_period))
    if "gini" in requested:
        merge(_get_acs_gini_community_lookup(geographic_level, acs_period))
    if "employment_16plus" in requested:
        merge(_get_acs_employment_community_lookup(geographic_level, acs_period))
    if "occupation_dist" in requested:
        merge(_get_acs_occupation_community_lookup(geographic_level, acs_period))
    if "redlined_pct" in requested:
        # HRI tables are tract-specific and available for 2010/2020. Most recent uses 2020; historical 2010 is handled separately below.
        hri_period = "2010" if str(acs_period).endswith("2018") or str(acs_period).endswith("2013") else "2020"
        merge(_get_redlining_hri_lookup(geographic_level, hri_period, acs_period))
    if "housing_unoccupied" in requested:
        merge(_get_acs_housing_unoccupied_community_lookup(geographic_level, acs_period))
    if "renting_pct" in requested:
        merge(_get_acs_renting_community_lookup(geographic_level, acs_period))
    if "median_year_built" in requested:
        merge(_get_acs_median_year_built_community_lookup(geographic_level, acs_period))
    if "median_home_value" in requested:
        merge(_get_acs_median_home_value_community_lookup(geographic_level, acs_period))
    if "internet_access" in requested:
        merge(_get_acs_internet_access_community_lookup(geographic_level, acs_period))
    if "moved_last_year" in requested:
        merge(_get_acs_moved_last_year_community_lookup(geographic_level, acs_period))

    return lookup


def _candidate_community_tables(source, geographic_level, period):
    level = {
        "county": "county",
        "tract": "tract",
        "zcta": "zcta",
        "place": "place",
    }.get(geographic_level, geographic_level)

    period = str(period)

    if source == "rucc":
        return [
            f"rucc{period}",              # your actual table style: rucc2013, rucc2023
            f"rucc_{period}",
            f"rucc_codes_{period}",
            f"rucc_county_{period}",
            f"rucc_county_codes_{period}",
        ]

    if source == "ruca":
        return [
            f"ruca{period}",              # your actual table style: ruca2010, ruca2020
            f"ruca_{period}",
            f"ruca_codes_{period}",
            f"ruca_tract_{period}",
            f"ruca_tract_codes_{period}",
        ]

    if source == "svi":
        return [
            f"svi_{level}_{period}",
            f"cdc_svi_{level}_{period}",
            f"svi_{period}_{level}",
            f"cdc_svi_{period}_{level}",
            f"svi_{period}",
            f"cdc_svi_{period}",
            f"svi{period}",
            f"cdcsvi{period}",
        ]

    return []


def _normalize_plain_fips(value, target_len=None):
    """
    Normalize tract/county FIPS values from RUCA/RUCC tables.

    Handles values imported as text, integers, or Excel-style decimals, and
    strips non-digit characters before left-padding to the requested length.
    """
    if value in (None, ""):
        return None

    s = str(value).strip()
    if not s:
        return None

    if s.endswith(".0"):
        s = s[:-2]

    s = re.sub(r"\D", "", s)
    if not s:
        return None

    if target_len:
        s = s.zfill(target_len)[-target_len:]

    return s



RUCA_PRIMARY_CODE_DESCRIPTIONS = {
    "1": "Metropolitan core",
    "2": "Metropolitan high commuting",
    "3": "Metropolitan low commuting",
    "4": "Micropolitan core",
    "5": "Micropolitan high commuting",
    "6": "Micropolitan low commuting",
    "7": "Small town core",
    "8": "Small town high commuting",
    "9": "Small town low commuting",
    "10": "Rural",
}


def _normalize_ruca_primary_code(value):
    """Return the primary RUCA code as a compact string, e.g., 1.0 -> 1."""
    if value in (None, ""):
        return None
    s = str(value).strip()
    if not s:
        return None

    match = re.search(r"\d+(?:\.\d+)?", s)
    if not match:
        return None

    raw = match.group(0)
    try:
        number = float(raw)
        if number.is_integer():
            return str(int(number))
        return raw.rstrip("0").rstrip(".")
    except Exception:
        return raw.rstrip("0").rstrip(".")


def _is_numeric_like(value):
    if value in (None, ""):
        return False
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", str(value).strip()))


def _ruca_description_from_code(code, db_description=None):
    """
    Prefer a real text description from the database. If the selected
    description column is actually another numeric RUCA code, derive the
    description from the primary RUCA code so the output does not show a value
    like '1' as the description.
    """
    if db_description not in (None, "") and not _is_numeric_like(db_description):
        return str(db_description).strip()

    primary_code = _normalize_ruca_primary_code(code)
    return RUCA_PRIMARY_CODE_DESCRIPTIONS.get(primary_code)


def _get_ruca_lookup(period):
    """
    Return tract-level RUCA lookup for the actual PopCASE RUCA tables:
      - ruca2010
      - ruca2020

    Output by tract GEOID:
      {
        "39035123456": {
            "rurality": "1.0",
            "rurality_description": "...",
        }
      }
    """
    period = str(period).strip()

    if period == "2010":
        table = "ruca2010"
        geo_candidates = [
            "state_county_tract_fips_code",
            "TractFIPS",
            "tract_fips",
            "tractfips",
            "GEO_ID",
            "geo_id",
            "GEOID",
            "geoid",
        ]
        code_candidates = [
            "primary_ruca_code_2010",
            "PrimaryRUCA",
            "primary_ruca",
            "ruca_code",
            "RUCA_CODE",
            "ruca",
            "RUCA",
            "code",
            "CODE",
        ]
        desc_candidates = [
            "PrimaryRUCADescription",
            "primary_ruca_description",
            "SecondaryRUCADescription",
            "description",
            "Description",
        ]

    elif period == "2020":
        table = "ruca2020"
        geo_candidates = [
            "TractFIPS20",
            "TractFIPS23",
            "state_county_tract_fips_code",
            "TractFIPS",
            "tract_fips",
            "tractfips",
            "GEO_ID",
            "geo_id",
            "GEOID",
            "geoid",
        ]
        code_candidates = [
            "PrimaryRUCA",
            "primary_ruca_code_2020",
            "primary_ruca",
            "ruca_code",
            "RUCA_CODE",
            "ruca",
            "RUCA",
            "code",
            "CODE",
        ]
        desc_candidates = [
            "PrimaryRUCADescription",
            "primary_ruca_description",
            "SecondaryRUCADescription",
            "description",
            "Description",
        ]

    else:
        return {}

    if not _table_exists(table):
        return {}

    colmap = _get_table_column_map(table)
    geo_col = _first_existing_col(colmap, geo_candidates)
    code_col = _first_existing_col(colmap, code_candidates)
    desc_col = _first_existing_col(colmap, desc_candidates)

    if not geo_col or not code_col:
        return {}

    selected = {
        "geo": [geo_col],
        "code": [code_col],
    }
    if desc_col:
        selected["desc"] = [desc_col]

    rows, aliases = _select_columns_from_table(table, selected)

    def val(row, alias):
        col = aliases.get(alias)
        return row.get(col) if col else None

    lookup = {}
    for row in rows:
        tract = _normalize_plain_fips(val(row, "geo"), target_len=11)
        if not tract:
            continue

        code = val(row, "code")
        if code in (None, ""):
            continue

        desc = _ruca_description_from_code(code, val(row, "desc"))
        lookup[tract] = {
            "rurality": str(code).strip(),
            "rurality_description": desc,
        }

    return lookup


def _get_rucc_lookup(period):
    """
    Return county-level RUCC lookup for the actual PopCASE RUCC tables:
      - rucc2013
      - rucc2023

    Handles both wide tables with RUCC_2013 and long tables with Attribute/Value.
    """
    period = str(period).strip()

    if period == "2013":
        table = "rucc2013"
        geo_candidates = [
            "FIPS",
            "fips",
            "county_fips",
            "CountyFIPS",
            "GEO_ID",
            "geo_id",
            "GEOID",
            "geoid",
        ]
        code_candidates = [
            "RUCC_2013",
            "rucc_2013",
            "RUCC",
            "rucc",
            "code",
            "CODE",
            "Value",
            "value",
        ]
        desc_candidates = [
            "Description",
            "description",
            "RUCC_Description",
            "rucc_description",
        ]

    elif period == "2023":
        table = "rucc2023"
        geo_candidates = [
            "FIPS",
            "fips",
            "county_fips",
            "CountyFIPS",
            "GEO_ID",
            "geo_id",
            "GEOID",
            "geoid",
        ]
        code_candidates = [
            "RUCC_2023",
            "rucc_2023",
            "RUCC",
            "rucc",
            "code",
            "CODE",
            "Value",
            "value",
        ]
        desc_candidates = [
            "Description",
            "description",
            "RUCC_Description",
            "rucc_description",
        ]

    else:
        return {}

    if not _table_exists(table):
        return {}

    colmap = _get_table_column_map(table)
    geo_col = _first_existing_col(colmap, geo_candidates)
    code_col = _first_existing_col(colmap, code_candidates)
    desc_col = _first_existing_col(colmap, desc_candidates)
    attr_col = _first_existing_col(colmap, ["Attribute", "attribute", "Variable", "variable", "Name", "name"])

    if not geo_col or not code_col:
        return {}

    selected = {
        "geo": [geo_col],
        "code": [code_col],
    }
    if desc_col:
        selected["desc"] = [desc_col]
    if attr_col:
        selected["attribute"] = [attr_col]

    rows, aliases = _select_columns_from_table(table, selected)

    def val(row, alias):
        col = aliases.get(alias)
        return row.get(col) if col else None

    preferred = {}
    fallback = {}

    for row in rows:
        county = _normalize_plain_fips(val(row, "geo"), target_len=5)
        if not county:
            continue

        code = val(row, "code")
        if code in (None, ""):
            continue

        desc = val(row, "desc")
        attr = str(val(row, "attribute") or "").strip().lower()

        record = {
            "rurality": str(code).strip(),
            "rurality_description": str(desc).strip() if desc not in (None, "") else None,
        }

        # For long-format RUCC 2023 tables, keep the row most likely to be the
        # actual RUCC code. If the table is already one row per county, this
        # simply behaves like a normal lookup.
        if attr and ("rucc" in attr or "rural" in attr or "metro" in attr or "code" in attr):
            preferred[county] = record
        elif county not in fallback:
            fallback[county] = record

    out = dict(fallback)
    out.update(preferred)
    return out


def _get_generic_community_lookup(source, geographic_level, period):
    """
    Generic lookup for non-ACS community sources.

    RUCA/RUCC are routed to explicit mappers because the actual PopCASE tables
    use source-specific column names:
      ruca2010: state_county_tract_fips_code, primary_ruca_code_2010
      ruca2020: TractFIPS20, PrimaryRUCA, PrimaryRUCADescription
      rucc2013: FIPS, RUCC_2013, Description
      rucc2023: FIPS, Attribute, Value
    """
    if source == "ruca":
        return _get_ruca_lookup(period) if geographic_level == "tract" else {}

    if source == "rucc":
        return _get_rucc_lookup(period) if geographic_level == "county" else {}

    tables = [t for t in _candidate_community_tables(source, geographic_level, period) if _table_exists(t)]
    if not tables:
        return {}

    table = tables[0]
    colmap = _get_table_column_map(table)
    geo_col = _first_existing_col(
        colmap,
        [
            "GEO_ID",
            "geo_id",
            "GEOID",
            "geoid",
            "FIPS",
            "fips",
            "CountyFIPS",
            "TractFIPS",
            "TractFIPS20",
            "TractFIPS23",
            "LOCATIONID",
            "locationid",
        ],
    )
    if not geo_col:
        return {}

    val_col = _first_existing_col(
        colmap,
        ["svi_adi", "SVI_ADI", "svi", "SVI", "overall_svi", "RPL_THEMES", "rpl_themes"],
    )
    out_key = "svi_adi"

    if not val_col:
        return {}

    where, params = _community_where_sql(table, geographic_level, source, period)
    sql = f'SELECT {_quote_identifier(geo_col)}, {_quote_identifier(val_col)} FROM {_quote_identifier(table)}'
    if where:
        sql += f" WHERE {where}"

    lookup = {}
    try:
        with connection.cursor() as cur:
            cur.execute(sql, params)
            for geo, val in cur.fetchall():
                geoid = _community_geoid_from_geo_id(geo, geographic_level)
                if geoid:
                    lookup[geoid] = {out_key: val}
    except Exception:
        return {}

    return lookup


@lru_cache(maxsize=64)
def _get_period_community_lookups_cached(geographic_level, dx_start, dx_end, support_tuple, display_tuple, timeframe_tuple):
    support_measures = _normalize_support_measure_tokens(support_tuple or [])
    display_options = set(display_tuple or [])
    plan = _community_period_plan(dx_start, dx_end, timeframe_tuple, geographic_level, support_measures)

    # Do not create duplicate suffixed columns for the ordinary default case;
    # the existing unsuffixed columns remain the default "Most recent" output.
    if set(_normalize_community_timeframes(timeframe_tuple)) == {"most_recent"}:
        return {}, False

    requested = set(support_measures)
    out_by_geo = {}
    has_historical_mode = "historical" in set(_normalize_community_timeframes(timeframe_tuple))

    for item in plan:
        source = item["source"]
        period = item["period"]
        suffix = _community_period_suffix(source, period)
        if source == "acs":
            lookup = _get_acs_period_community_lookup(requested & COMMUNITY_ACS_TOKENS, geographic_level, period)
        elif source in {"rucc", "ruca"}:
            lookup = _get_generic_community_lookup(source, geographic_level, period) if "rurality" in requested else {}
        elif source == "svi":
            if "svi_adi" in requested and geographic_level == "county":
                lookup = _get_county_adi_lookup()
            else:
                lookup = _get_generic_community_lookup(source, geographic_level, period) if "svi_adi" in requested else {}
        else:
            lookup = {}
        for geoid, row in lookup.items():
            target = out_by_geo.setdefault(geoid, {})
            for key, val in row.items():
                if key == "total_population_moe_90":
                    continue
                target[f"{key}{suffix}"] = val

    return out_by_geo, has_historical_mode


def _parse_yyyymmdd(value):
    """Parse NAACCR-style YYYYMMDD strings. Returns a date or None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    if len(digits) != 8:
        return None
    if digits in {"00000000", "99999999", "88888888"}:
        return None
    try:
        return datetime.strptime(digits, "%Y%m%d").date()
    except Exception:
        return None


def _parse_gleason_value(value):
    """Return a valid Gleason score 2-10, or None for unknown/special codes."""
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    if text in {"X7", "X8", "X9", "88", "98", "99", "998", "999", "UNKNOWN", "NA", "N/A"}:
        return None
    if not re.fullmatch(r"\d+", text):
        return None
    try:
        score = int(text)
    except Exception:
        return None
    if 2 <= score <= 10:
        return score
    return None


def _percentile(values, percentile):
    """Linear-interpolated percentile for a non-empty numeric list."""
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * (percentile / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return ordered[int(k)]
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


def _summarize_numeric_values(values):
    """Return mean, normal 95% CI, median, Q1, Q3 for numeric values."""
    clean = [float(v) for v in values if v is not None]
    n = len(clean)
    if n == 0:
        return {
            "n": 0,
            "mean": None,
            "ci_lower": None,
            "ci_upper": None,
            "median": None,
            "q1": None,
            "q3": None,
        }
    mean_val = sum(clean) / n
    ci_lower = ci_upper = None
    if n >= 2:
        var = sum((v - mean_val) ** 2 for v in clean) / (n - 1)
        se = math.sqrt(var) / math.sqrt(n)
        ci_lower = mean_val - 1.96 * se
        ci_upper = mean_val + 1.96 * se
    return {
        "n": n,
        "mean": mean_val,
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "median": _percentile(clean, 50),
        "q1": _percentile(clean, 25),
        "q3": _percentile(clean, 75),
    }


def _get_tti_gleason_details_for_patients(patient_ids):
    """
    Pull disease-specific details that are not represented on the minimal
    NaaccrData Django model. Uses raw SQL so long NAACCR column names can be
    read without expanding the unmanaged model.
    """
    ids = [str(x).strip() for x in patient_ids if str(x).strip()]
    if not ids:
        return {}

    sql = """
        SELECT
            "Patient ID Number" AS mid,
            "Date of Diagnosis" AS dx_date,
            "Date Initial RX SEER" AS initial_rx_date,
            "Date 1st Crs RX CoC" AS first_course_rx_date,
            "RX Date Surgery" AS surgery_date,
            "RX Date Chemo" AS chemo_date,
            "RX Date Radiation" AS radiation_date,
            "RX Date Systemic" AS systemic_date,
            "RX Date Hormone" AS hormone_date,
            "RX Date BRM" AS brm_date,
            "RX Date Other" AS other_rx_date,
            "RX Date Mst Defn Srg" AS mst_defn_surgery_date,
            "Primary Site" AS primary_site,
            "Gleason Score Clinical" AS gleason_clinical,
            "Gleason Score Pathological" AS gleason_pathological
        FROM "naaccr_data"
        WHERE "Patient ID Number" = ANY(%s)
    """

    details = {}
    chunk_size = 5000
    with connection.cursor() as cursor:
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i:i + chunk_size]
            cursor.execute(sql, [chunk])
            cols = [c[0] for c in cursor.description]
            for raw_row in cursor.fetchall():
                row = dict(zip(cols, raw_row))
                mid = str(row.get("mid") or "").strip()
                if not mid:
                    continue

                dx = _parse_yyyymmdd(row.get("dx_date"))
                treatment_candidates = []
                for key in (
                    "initial_rx_date",
                    "first_course_rx_date",
                    "surgery_date",
                    "chemo_date",
                    "radiation_date",
                    "systemic_date",
                    "hormone_date",
                    "brm_date",
                    "other_rx_date",
                    "mst_defn_surgery_date",
                ):
                    parsed = _parse_yyyymmdd(row.get(key))
                    if parsed is not None:
                        treatment_candidates.append(parsed)

                tti_days = None
                if dx is not None and treatment_candidates:
                    tx = min(treatment_candidates)
                    delta = (tx - dx).days
                    if 0 <= delta <= 365:
                        tti_days = delta

                primary_site = str(row.get("primary_site") or "").strip().upper()
                gleason = None
                if primary_site == "C619":
                    gleason = _parse_gleason_value(row.get("gleason_pathological"))
                    if gleason is None:
                        gleason = _parse_gleason_value(row.get("gleason_clinical"))

                details[mid] = {
                    "tti_days": tti_days,
                    "gleason": gleason,
                }
    return details

def _build_geo_dataset_uncached(
    geographic_level: str,
    year_range=("2011", "2022"),
    filters=None,
    disease_measures=None,
    support_measures=None,
    display_options=None,
    community_timeframes=None,
    incidence_year=None,
):
    """
    Returns list[dict] with one row per geographic unit.

    Rows can include:
      - disease measures
      - available community / prevention / access measures

    Option B:
      - primary_care_access_score comes from weighted_sa_final
      - it is an accessibility score, not minutes
    """
    if filters is None:
        filters = {}

    disease_measures = set(_as_list(disease_measures))
    support_measures = _normalize_support_measure_tokens(support_measures)
    display_options = set(_as_list(display_options))
    community_timeframes = _normalize_community_timeframes(community_timeframes)

    dx_start, dx_end = year_range
    filters = dict(filters)
    filters["dx_start"] = str(dx_start)
    filters["dx_end"] = str(dx_end)

    filtered_qs = apply_naaccr_filters(NaaccrData.objects.all(), filters)
    stage_by_mid = dict(filtered_qs.values_list("mid", "stg_grp"))
    filtered_pat_ids = list(stage_by_mid.keys())

    linking_rows = []
    if filtered_pat_ids:
        linking_rows = list(
            NaaccrPatientCensusLinking.objects
            .filter(geographic_level=geographic_level, pat_id__in=filtered_pat_ids)
            .values_list("pat_id", "geoid")
            .distinct()
        )

    denom_ids = {"0", "1", "2", "3", "4", "5", "6", "7", "9"}
    adv_ids = {"2", "3", "4", "5", "7"}
    meta_id = "7"
    non_applicable_ids = {"8"}

    denom_by_geo = defaultdict(int)
    adv_by_geo = defaultdict(int)
    meta_by_geo = defaultdict(int)
    tti_by_geo = defaultdict(list)
    gleason_by_geo = defaultdict(list)

    need_tti = ("median_tti" in disease_measures) or ("median_tti_iqr" in disease_measures)
    need_gleason = ("gleason" in disease_measures) or ("gleason_ci" in disease_measures)
    disease_detail_by_mid = {}
    if (need_tti or need_gleason) and filtered_pat_ids:
        disease_detail_by_mid = _get_tti_gleason_details_for_patients(filtered_pat_ids)

    def _norm_geoid(g):
        g = str(g).strip()
        if geographic_level == "zcta":
            return g[-5:]
        return g

    for pat_id, geoid in linking_rows:
        if not geoid:
            continue
        geoid = _norm_geoid(geoid)

        if not _geoid_in_scope(geographic_level, geoid, filters):
            continue

        if disease_detail_by_mid:
            detail = disease_detail_by_mid.get(str(pat_id).strip(), {})
            if need_tti and detail.get("tti_days") is not None:
                tti_by_geo[geoid].append(detail["tti_days"])
            if need_gleason and detail.get("gleason") is not None:
                gleason_by_geo[geoid].append(detail["gleason"])

        stg = stage_by_mid.get(pat_id)
        if stg is None:
            continue
        stg = str(stg).strip()
        if not stg or stg in non_applicable_ids:
            continue

        if stg in denom_ids:
            denom_by_geo[geoid] += 1
            if stg in adv_ids:
                adv_by_geo[geoid] += 1
            if stg == meta_id:
                meta_by_geo[geoid] += 1

    def _prop_ci(a, n):
        if n <= 0:
            return (None, None, None)
        p = a / n
        se = sqrt(p * (1 - p) / n)
        lo = max(0.0, p - 1.96 * se)
        hi = min(1.0, p + 1.96 * se)
        return (p, lo, hi)

    incidence_lookup = {}
    if ("crude_inc_rate" in disease_measures) or ("crude_inc_ci" in disease_measures) or ("inc_rate" in disease_measures) or ("inc_ci" in disease_measures):
        if incidence_year is None:
            incidence_year = (
                NaaccrPatientCensusLinking.objects
                .values_list("year", flat=True)
                .order_by("-year")
                .first()
            )
        inc_rows = get_incidence_by_geography(
            year=incidence_year,
            geographic_level=geographic_level,
            filters=filters,
        )
        for r in inc_rows:
            incidence_lookup[r["geoid"]] = r
    incidence_lookup = _filter_lookup_to_scope(incidence_lookup, geographic_level, filters)

    support_lookup = {}
    if support_measures:
        support_lookup = _get_geo_support_lookups(geographic_level, support_measures)

    if support_lookup:
        support_lookup = {
            name: _filter_lookup_to_scope(lookup, geographic_level, filters)
            for name, lookup in support_lookup.items()
        }

    community_period_lookup, remove_unsuffixed_community = _get_period_community_lookups_cached(
        geographic_level,
        str(dx_start),
        str(dx_end),
        tuple(sorted(support_measures)),
        tuple(sorted(display_options)),
        tuple(sorted(community_timeframes)),
    )
    community_period_lookup = _filter_lookup_to_scope(community_period_lookup, geographic_level, filters)

    all_geoids = set(denom_by_geo.keys()) | set(incidence_lookup.keys()) | set(tti_by_geo.keys()) | set(gleason_by_geo.keys())

    if community_period_lookup:
        all_geoids |= set(community_period_lookup.keys())

    if support_lookup:
        for lookup in support_lookup.values():
            all_geoids |= set(lookup.keys())

    all_geoids = {
        g for g in all_geoids
        if _geoid_in_scope(geographic_level, g, filters)
    }

    rows = []

    for geoid in all_geoids:
        out = {
            "label": _geo_label(geographic_level, geoid),
            "geoid": geoid,
        }

        if geographic_level == "tract":
            out["tract_geoid"] = geoid

        if "case_count" in disease_measures:
            out["case_count"] = int(denom_by_geo.get(geoid, 0))

        if ("pct_advanced" in disease_measures) or ("pct_advanced_ci" in disease_measures):
            n = int(denom_by_geo.get(geoid, 0))
            a = int(adv_by_geo.get(geoid, 0))
            out["n_total_staged_unstaged"] = n
            p, lo, hi = _prop_ci(a, n)

            if "pct_advanced" in disease_measures:
                out["pct_advanced"] = round(p * 100, 2) if p is not None else None

            if "pct_advanced_ci" in disease_measures:
                out["adv_ci_lower"] = round(lo * 100, 2) if lo is not None else None
                out["adv_ci_upper"] = round(hi * 100, 2) if hi is not None else None

        if ("pct_metastatic" in disease_measures) or ("pct_metastatic_ci" in disease_measures):
            n = int(denom_by_geo.get(geoid, 0))
            m = int(meta_by_geo.get(geoid, 0))
            p, lo, hi = _prop_ci(m, n)

            if "pct_metastatic" in disease_measures:
                out["pct_metastatic"] = round(p * 100, 2) if p is not None else None

            if "pct_metastatic_ci" in disease_measures:
                out["meta_ci_lower"] = round(lo * 100, 2) if lo is not None else None
                out["meta_ci_upper"] = round(hi * 100, 2) if hi is not None else None

        if ("median_tti" in disease_measures) or ("median_tti_iqr" in disease_measures):
            tti_summary = _summarize_numeric_values(tti_by_geo.get(geoid, []))
            if "median_tti" in disease_measures:
                out["median_tti"] = round(tti_summary["median"], 2) if tti_summary["median"] is not None else None
            if "median_tti_iqr" in disease_measures:
                out["median_tti_iqr_lower"] = round(tti_summary["q1"], 2) if tti_summary["q1"] is not None else None
                out["median_tti_iqr_upper"] = round(tti_summary["q3"], 2) if tti_summary["q3"] is not None else None

        if ("gleason" in disease_measures) or ("gleason_ci" in disease_measures):
            gleason_summary = _summarize_numeric_values(gleason_by_geo.get(geoid, []))
            if "gleason" in disease_measures:
                out["mean_gleason_score"] = round(gleason_summary["mean"], 2) if gleason_summary["mean"] is not None else None
            if "gleason_ci" in disease_measures:
                out["gleason_ci_lower"] = round(gleason_summary["ci_lower"], 2) if gleason_summary["ci_lower"] is not None else None
                out["gleason_ci_upper"] = round(gleason_summary["ci_upper"], 2) if gleason_summary["ci_upper"] is not None else None

        if ("crude_inc_rate" in disease_measures) or ("crude_inc_ci" in disease_measures) or ("inc_rate" in disease_measures) or ("inc_ci" in disease_measures):
            ir = incidence_lookup.get(geoid)
            if "crude_inc_rate" in disease_measures:
                out["crude_incidence_per_100k"] = ir.get("crude_incidence_per_100k") if ir else None
            if "crude_inc_ci" in disease_measures:
                out["crude_inc_ci_lower_per_100k"] = ir.get("crude_incidence_ci_lower") if ir else None
                out["crude_inc_ci_upper_per_100k"] = ir.get("crude_incidence_ci_upper") if ir else None
            if "inc_rate" in disease_measures:
                out["age_adjusted_per_100k"] = ir.get("age_adjusted_per_100k") if ir else None
            if "inc_ci" in disease_measures:
                out["inc_ci_lower_per_100k"] = ir.get("age_adjusted_ci_lower") if ir else None
                out["inc_ci_upper_per_100k"] = ir.get("age_adjusted_ci_upper") if ir else None

        if ("crude_mort_rate" in disease_measures):
            out["crude_mortality_per_100k"] = None
        if ("crude_mort_ci" in disease_measures):
            out["crude_mort_ci_lower_per_100k"] = None
            out["crude_mort_ci_upper_per_100k"] = None
        if ("mort_rate" in disease_measures):
            out["age_adjusted_mortality_per_100k"] = None
        if ("mort_ci" in disease_measures):
            out["mort_ci_lower_per_100k"] = None
            out["mort_ci_upper_per_100k"] = None

        if support_lookup:
            community_row = support_lookup.get("community_acs", {}).get(geoid, {})
            if community_row:
                out.update(community_row)
                out.pop("total_population_moe_90", None)

            if "pop_total" in support_measures:
                out["total_population"] = community_row.get("total_population", support_lookup.get("pop", {}).get(geoid))

            if "med_hh_income" in support_measures:
                out["median_household_income"] = community_row.get("median_household_income", support_lookup.get("income", {}).get(geoid))
                out["median_household_income_ci_lower"] = community_row.get("median_household_income_ci_lower")
                out["median_household_income_ci_upper"] = community_row.get("median_household_income_ci_upper")

            if "limited_english_pct" in support_measures:
                out["limited_english_pct"] = community_row.get("limited_english_pct", support_lookup.get("limited_english_pct", {}).get(geoid))
                out["limited_english_ci_lower"] = community_row.get("limited_english_ci_lower")
                out["limited_english_ci_upper"] = community_row.get("limited_english_ci_upper")

            if "sex_distribution" in support_measures:
                sex_row = support_lookup.get("sex", {}).get(geoid, {})
                out["male_population"] = sex_row.get("male_population")
                out["female_population"] = sex_row.get("female_population")
                out["male_pct"] = sex_row.get("male_pct")
                out["female_pct"] = sex_row.get("female_pct")
                out["male_pct_ci_lower"] = sex_row.get("male_pct_ci_lower")
                out["male_pct_ci_upper"] = sex_row.get("male_pct_ci_upper")
                out["female_pct_ci_lower"] = sex_row.get("female_pct_ci_lower")
                out["female_pct_ci_upper"] = sex_row.get("female_pct_ci_upper")

            if "median_age" in support_measures:
                out["median_age"] = community_row.get("median_age", support_lookup.get("median_age", {}).get(geoid))
                out["median_age_ci_lower"] = community_row.get("median_age_ci_lower")
                out["median_age_ci_upper"] = community_row.get("median_age_ci_upper")

            places_row = support_lookup.get("places", {}).get(geoid, {})
            if places_row:
                out.update(places_row)

            if "breast_screen" in support_measures:
                out["mammography_screening_pct"] = places_row.get("mammography_screening_pct", places_row.get("breast_screen"))
                out["mammography_screening_ci_lower"] = places_row.get("mammography_screening_ci_lower")
                out["mammography_screening_ci_upper"] = places_row.get("mammography_screening_ci_upper")

            if "routine_checkup" in support_measures:
                out["routine_checkup_pct"] = places_row.get("routine_checkup_pct", places_row.get("routine_checkup"))
                out["routine_checkup_ci_lower"] = places_row.get("routine_checkup_ci_lower")
                out["routine_checkup_ci_upper"] = places_row.get("routine_checkup_ci_upper")
                out["routine_checkup_age_adjusted_pct"] = places_row.get("routine_checkup_age_adjusted_pct")

            if "no_transport" in support_measures:
                out["lack_transportation_pct"] = places_row.get("lack_transportation_pct", places_row.get("no_transport"))
                out["lack_transportation_ci_lower"] = places_row.get("lack_transportation_ci_lower")
                out["lack_transportation_ci_upper"] = places_row.get("lack_transportation_ci_upper")
                out["lack_transportation_age_adjusted_pct"] = places_row.get("lack_transportation_age_adjusted_pct")

            if "no_insurance" in support_measures:
                out["uninsured_pct"] = places_row.get("uninsured_pct", places_row.get("no_insurance"))
                out["uninsured_ci_lower"] = places_row.get("uninsured_ci_lower")
                out["uninsured_ci_upper"] = places_row.get("uninsured_ci_upper")
                out["uninsured_age_adjusted_pct"] = places_row.get("uninsured_age_adjusted_pct")

            if "pcp_access_score" in support_measures:
                out["primary_care_access_score"] = support_lookup.get("pcp_access", {}).get(geoid)

            if "mammo_access" in support_measures:
                mammo_row = support_lookup.get("mammo_access", {}).get(geoid, {})
                out["nearest_mammography_distance_miles"] = mammo_row.get("nearest_miles")
                out["mammography_facility_count_20mi"] = mammo_row.get("count_20mi")
                out["mammography_access_score"] = mammo_row.get("access_score")

            if "race_eth" in support_measures:
                race_row = support_lookup.get("race_eth", {}).get(geoid, {})
                for key in (
                    "race_ethnicity", "race_eth_ci_lower", "race_eth_ci_upper",
                    "white_alone_pct", "white_alone_ci_lower", "white_alone_ci_upper",
                    "black_alone_pct", "black_alone_ci_lower", "black_alone_ci_upper",
                    "aian_alone_pct", "aian_alone_ci_lower", "aian_alone_ci_upper",
                    "asian_alone_pct", "asian_alone_ci_lower", "asian_alone_ci_upper",
                    "nhpi_alone_pct", "nhpi_alone_ci_lower", "nhpi_alone_ci_upper",
                    "other_race_alone_pct", "other_race_alone_ci_lower", "other_race_alone_ci_upper",
                    "multiracial_pct", "multiracial_ci_lower", "multiracial_ci_upper",
                    "nh_white_pct", "nh_white_ci_lower", "nh_white_ci_upper",
                    "hispanic_pct", "hispanic_ci_lower", "hispanic_ci_upper",
                ):
                    if key in race_row:
                        out[key] = race_row.get(key)

        if remove_unsuffixed_community:
            for key in list(COMMUNITY_BASE_OUTPUT_KEYS):
                out.pop(key, None)

        if community_period_lookup:
            out.update(community_period_lookup.get(geoid, {}))

        # Add optional Display 95% CI / age-adjusted companion columns for
        # geo-stratified support measures selected on measures.html.
        #
        # results.html builds the table columns dynamically from the keys
        # present in dataset_rows. Without this call, checkbox-only options such as
        # cancer_screening_ci, noncancer_health_status_ci, access survey CI,
        # and community-characteristics CI are saved in the session but never
        # appear as row keys, so no CI columns can be displayed or exported.
        #
        # Passing `out` as source_values preserves any real value/CI keys that
        # were already populated above. If a real CI source is not connected
        # yet, _add_display_option_columns intentionally creates the requested
        # columns with None values instead of inventing unsupported estimates.
        _add_display_option_columns(
            out,
            support_measures=support_measures,
            display_options=display_options,
            source_values=out,
        )

        rows.append(out)

    def _sort_key(r):
        if r.get("pct_advanced") is not None:
            return (r.get("pct_advanced"), r.get("case_count") or 0)
        if r.get("age_adjusted_per_100k") is not None:
            return (r.get("age_adjusted_per_100k"), r.get("case_count") or 0)
        return (0, 0)

    rows.sort(key=_sort_key, reverse=True)
    return rows


def _serialize_cache_payload(value):
    return json.dumps(value or {}, sort_keys=True, separators=(",", ":"))


def _deserialize_cache_payload(value):
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


@lru_cache(maxsize=128)
def _build_geo_dataset_cached(
    geographic_level: str,
    dx_start: str,
    dx_end: str,
    filters_json: str,
    disease_measures_tuple: tuple,
    support_measures_tuple: tuple,
    display_options_tuple: tuple,
    community_timeframes_tuple: tuple,
    incidence_year: str,
):
    return _build_geo_dataset_uncached(
        geographic_level=geographic_level,
        year_range=(dx_start, dx_end),
        filters=_deserialize_cache_payload(filters_json),
        disease_measures=list(disease_measures_tuple),
        support_measures=list(support_measures_tuple),
        display_options=list(display_options_tuple),
        community_timeframes=list(community_timeframes_tuple),
        incidence_year=incidence_year or None,
    )


def build_geo_dataset(
    geographic_level: str,
    year_range=("2011", "2022"),
    filters=None,
    disease_measures=None,
    support_measures=None,
    display_options=None,
    community_timeframes=None,
    incidence_year=None,
):
    filters = filters or {}
    dx_start, dx_end = year_range
    normalized_disease = tuple(sorted(_as_list(disease_measures)))
    normalized_support = tuple(sorted(_normalize_support_measure_tokens(support_measures)))
    normalized_display_options = tuple(sorted(_as_list(display_options)))
    normalized_community_timeframes = tuple(sorted(_normalize_community_timeframes(community_timeframes)))
    return _build_geo_dataset_cached(
        geographic_level=geographic_level,
        dx_start=str(dx_start),
        dx_end=str(dx_end),
        filters_json=_serialize_cache_payload(filters),
        disease_measures_tuple=normalized_disease,
        support_measures_tuple=normalized_support,
        display_options_tuple=normalized_display_options,
        community_timeframes_tuple=normalized_community_timeframes,
        incidence_year=str(incidence_year or ""),
    )



def _age_to_bin(age):
    if age is None:
        return None

    age = int(age)

    if age == 0:
        return "00"
    if 1 <= age <= 4:
        return "01"
    if 5 <= age <= 9:
        return "02"
    if 10 <= age <= 14:
        return "03"
    if 15 <= age <= 19:
        return "04"
    if 20 <= age <= 24:
        return "05"
    if 25 <= age <= 29:
        return "06"
    if 30 <= age <= 34:
        return "07"
    if 35 <= age <= 39:
        return "08"
    if 40 <= age <= 44:
        return "09"
    if 45 <= age <= 49:
        return "10"
    if 50 <= age <= 54:
        return "11"
    if 55 <= age <= 59:
        return "12"
    if 60 <= age <= 64:
        return "13"
    if 65 <= age <= 69:
        return "14"
    if 70 <= age <= 74:
        return "15"
    if 75 <= age <= 79:
        return "16"
    if 80 <= age <= 84:
        return "17"
    if 85 <= age <= 89:
        return "18"
    if age >= 90:
        return "19"


def _compute_age_adjusted_by_tract(year, filtered_pat_ids):
    if not filtered_pat_ids:
        return {}

    with connection.cursor() as cur:
        cur.execute(f"""
            SELECT
                l.geoid,
                d."Age at Diagnosis"::int AS age_dx
            FROM naaccr_data d
            JOIN naaccr_patient_census_linking l
                ON d."Patient ID Number" = l."Patient ID Number"
            WHERE l.year = %s
            AND l.geographic_level = 'tract'
            AND l."Patient ID Number" IN ({",".join(["%s"] * len(filtered_pat_ids))})
        """, [year] + filtered_pat_ids)
        case_rows = cur.fetchall()

    case_lookup = {}
    for geoid, age_dx in case_rows:
        bin_id = _age_to_bin(age_dx)
        if not bin_id:
            continue

        if bin_id in ("18", "19"):
            bin_id = "85+"

        case_lookup.setdefault(geoid, {})
        case_lookup[geoid][bin_id] = case_lookup[geoid].get(bin_id, 0) + 1

    with connections["popcase_manual_etl"].cursor() as cur:
        cur.execute("""
            SELECT state_fips, county_fips, tract, age, population
            FROM age_adjustment_census_tract
            WHERE year = %s
        """, [year])
        pop_rows = cur.fetchall()

    pop_lookup = {}
    for state_fips, county_fips, tract, age_bin, pop in pop_rows:
        geoid = f"{state_fips}{county_fips}{tract}"

        if age_bin in ("18", "19"):
            age_bin = "85+"

        pop_lookup.setdefault(geoid, {})
        pop_lookup[geoid][age_bin] = pop_lookup[geoid].get(age_bin, 0) + float(pop)

    adjusted_rates = {}
    for geoid, age_cases in case_lookup.items():
        total_rate = 0
        for age_bin, weight in US2000_STD_WEIGHTS.items():
            pop = pop_lookup.get(geoid, {}).get(age_bin)
            cases = age_cases.get(age_bin, 0)
            if pop and pop > 0:
                age_specific_rate = cases / pop
                total_rate += weight * age_specific_rate

        if total_rate > 0:
            adjusted_rates[geoid] = round(total_rate * 100000 / 1_000_000, 1)
        else:
            adjusted_rates[geoid] = None

    return adjusted_rates


def _compute_age_adjusted_ci_by_tract(year, filtered_pat_ids):
    if not filtered_pat_ids:
        return {}

    with connection.cursor() as cur:
        cur.execute(f"""
            SELECT
                l.geoid,
                d."Age at Diagnosis"::int AS age_dx
            FROM naaccr_data d
            JOIN naaccr_patient_census_linking l
                ON d."Patient ID Number" = l."Patient ID Number"
            WHERE l.year = %s
            AND l.geographic_level = 'tract'
            AND l."Patient ID Number" IN ({",".join(["%s"] * len(filtered_pat_ids))})
        """, [str(year)] + filtered_pat_ids)
        case_rows = cur.fetchall()

    case_lookup = defaultdict(lambda: defaultdict(int))
    for geoid, age_dx in case_rows:
        bin_id = _age_to_bin(age_dx)
        if not bin_id:
            continue
        if bin_id in ("18", "19"):
            bin_id = "85+"
        case_lookup[str(geoid)][bin_id] += 1

    with connections["popcase_manual_etl"].cursor() as cur:
        cur.execute("""
            SELECT state_fips, county_fips, tract, age, population
            FROM age_adjustment_census_tract
            WHERE year = %s
        """, [str(year)])
        pop_rows = cur.fetchall()

    pop_lookup = defaultdict(lambda: defaultdict(float))
    for state_fips, county_fips, tract, age_bin, pop in pop_rows:
        geoid = f"{state_fips}{county_fips}{tract}"
        if age_bin in ("18", "19"):
            age_bin = "85+"
        pop_lookup[geoid][age_bin] += float(pop)

    out = {}
    scale = 100000.0 / 1_000_000.0

    for geoid, age_cases in case_lookup.items():
        total = 0.0
        var_sum = 0.0

        for age_bin, weight in US2000_STD_WEIGHTS.items():
            pop = pop_lookup.get(geoid, {}).get(age_bin)
            d = age_cases.get(age_bin, 0)

            if pop and pop > 0:
                total += weight * (d / pop)
                var_sum += (weight ** 2) * (d / (pop ** 2))

        rate = total * scale
        se = math.sqrt(var_sum) * scale if var_sum > 0 else 0.0

        if total > 0:
            lo = max(0.0, rate - 1.96 * se)
            hi = rate + 1.96 * se
            out[geoid] = (round(rate, 1), round(lo, 1), round(hi, 1))
        else:
            out[geoid] = (None, None, None)

    return out


def _compute_age_adjusted_by_county(year, filtered_pat_ids):
    if not filtered_pat_ids:
        return {}

    with connection.cursor() as cur:
        cur.execute(f"""
            SELECT
                l.geoid,
                d."Age at Diagnosis"::int AS age_dx
            FROM naaccr_data d
            JOIN naaccr_patient_census_linking l
                ON d."Patient ID Number" = l."Patient ID Number"
            WHERE l.year = %s
            AND l.geographic_level = 'county'
            AND l."Patient ID Number" IN ({",".join(["%s"] * len(filtered_pat_ids))})
        """, [year] + filtered_pat_ids)
        rows = cur.fetchall()

    case_lookup = {}
    for geoid, age_dx in rows:
        bin_id = _age_to_bin(age_dx)
        if not bin_id:
            continue

        if bin_id in ("18", "19"):
            bin_id = "85+"

        case_lookup.setdefault(geoid, {})
        case_lookup[geoid][bin_id] = case_lookup[geoid].get(bin_id, 0) + 1

    with connections["popcase_manual_etl"].cursor() as cur:
        cur.execute("""
            SELECT state_fips, county_fips, age, SUM(population)
            FROM age_adjustment_census_tract
            WHERE year = %s
            GROUP BY state_fips, county_fips, age
        """, [year])
        pop_rows = cur.fetchall()

    pop_lookup = {}
    for state_fips, county_fips, age_bin, pop in pop_rows:
        geoid = f"{state_fips}{county_fips}"

        if age_bin in ("18", "19"):
            age_bin = "85+"

        pop_lookup.setdefault(geoid, {})
        pop_lookup[geoid][age_bin] = float(pop)

    adjusted = {}
    for geoid, age_cases in case_lookup.items():
        total_rate = 0
        for age_bin, weight in US2000_STD_WEIGHTS.items():
            pop = pop_lookup.get(geoid, {}).get(age_bin)
            cases = age_cases.get(age_bin, 0)
            if pop and pop > 0:
                total_rate += weight * (cases / pop)

        adjusted[geoid] = round(total_rate * 100000 / 1_000_000, 1)

    return adjusted


def _compute_age_adjusted_ci_by_county(year, filtered_pat_ids):
    if not filtered_pat_ids:
        return {}

    with connection.cursor() as cur:
        cur.execute(f"""
            SELECT
                l.geoid,
                d."Age at Diagnosis"::int AS age_dx
            FROM naaccr_data d
            JOIN naaccr_patient_census_linking l
                ON d."Patient ID Number" = l."Patient ID Number"
            WHERE l.year = %s
              AND l.geographic_level = 'county'
              AND l."Patient ID Number" IN ({",".join(["%s"] * len(filtered_pat_ids))})
        """, [str(year)] + filtered_pat_ids)
        rows = cur.fetchall()

    case_lookup = defaultdict(lambda: defaultdict(int))
    for geoid, age_dx in rows:
        bin_id = _age_to_bin(age_dx)
        if not bin_id:
            continue
        if bin_id in ("18", "19"):
            bin_id = "85+"
        case_lookup[str(geoid)][bin_id] += 1

    with connections["popcase_manual_etl"].cursor() as cur:
        cur.execute("""
            SELECT state_fips, county_fips, age, SUM(population::numeric)
            FROM age_adjustment_census_tract
            WHERE year = %s
            GROUP BY state_fips, county_fips, age
        """, [str(year)])
        pop_rows = cur.fetchall()

    pop_lookup = defaultdict(lambda: defaultdict(float))
    for state_fips, county_fips, age_bin, pop in pop_rows:
        geoid = f"{state_fips}{county_fips}"
        if age_bin in ("18", "19"):
            age_bin = "85+"
        pop_lookup[geoid][age_bin] += float(pop)

    out = {}
    scale = 100000.0 / 1_000_000.0

    for geoid, age_cases in case_lookup.items():
        total = 0.0
        var_sum = 0.0

        for age_bin, weight in US2000_STD_WEIGHTS.items():
            pop = pop_lookup.get(geoid, {}).get(age_bin)
            d = age_cases.get(age_bin, 0)
            if pop and pop > 0:
                total += weight * (d / pop)
                var_sum += (weight ** 2) * (d / (pop ** 2))

        rate = total * scale
        se = math.sqrt(var_sum) * scale if var_sum > 0 else 0.0

        if total > 0:
            lo = max(0.0, rate - 1.96 * se)
            hi = rate + 1.96 * se
            out[geoid] = (round(rate, 1), round(lo, 1), round(hi, 1))
        else:
            out[geoid] = (None, None, None)

    return out


def _compute_age_adjusted_by_zcta(year, filtered_pat_ids):
    if not filtered_pat_ids:
        return {}

    with connection.cursor() as cur:
        cur.execute(f"""
            SELECT
                l.geoid,
                d."Age at Diagnosis"::int AS age_dx
            FROM naaccr_data d
            JOIN naaccr_patient_census_linking l
                ON d."Patient ID Number" = l."Patient ID Number"
            WHERE l.year = %s
            AND l.geographic_level = 'zcta'
            AND l."Patient ID Number" IN ({",".join(["%s"] * len(filtered_pat_ids))})
        """, [year] + filtered_pat_ids)
        rows = cur.fetchall()

    case_lookup = {}
    for geoid, age_dx in rows:
        bin_id = _age_to_bin(age_dx)
        if not bin_id:
            continue

        if bin_id in ("18", "19"):
            bin_id = "85+"

        case_lookup.setdefault(geoid, {})
        case_lookup[geoid][bin_id] = case_lookup[geoid].get(bin_id, 0) + 1

    with connections["popcase_manual_etl"].cursor() as cur:
        cur.execute("""
            SELECT "GEOID", age, SUM(population::numeric)
            FROM age_adjustment_zcta
            GROUP BY "GEOID", age
        """)
        pop_rows = cur.fetchall()

    pop_lookup = {}
    for geoid_raw, age_bin, pop in pop_rows:
        geoid = str(geoid_raw).strip()[-5:]
        age_bin = _map_population_age_bin(age_bin)
        if not age_bin:
            continue

        pop_lookup.setdefault(geoid, {})
        pop_lookup[geoid][age_bin] = float(pop)

    adjusted = {}
    for geoid, age_cases in case_lookup.items():
        total_rate = 0
        for age_bin, weight in US2000_STD_WEIGHTS.items():
            pop = pop_lookup.get(geoid, {}).get(age_bin)
            cases = age_cases.get(age_bin, 0)
            if pop and pop > 0:
                total_rate += weight * (cases / pop)

        adjusted[geoid] = round(total_rate * 100000 / 1_000_000, 1)
    return adjusted


def _compute_age_adjusted_ci_by_zcta(year, filtered_pat_ids):
    if not filtered_pat_ids:
        return {}

    year = str(year)
    pop_year = _resolve_zcta_pop_year(year)

    with connection.cursor() as cur:
        cur.execute(f"""
            SELECT
                l.geoid,
                d."Age at Diagnosis"::int AS age_dx
            FROM naaccr_data d
            JOIN naaccr_patient_census_linking l
                ON d."Patient ID Number" = l."Patient ID Number"
            WHERE l.year = %s
              AND l.geographic_level = 'zcta'
              AND l."Patient ID Number" IN ({",".join(["%s"] * len(filtered_pat_ids))})
        """, [year] + filtered_pat_ids)
        rows = cur.fetchall()

    case_lookup = defaultdict(lambda: defaultdict(int))
    for geoid_raw, age_dx in rows:
        zip5 = str(geoid_raw).strip()[-5:]
        bin_id = _age_to_bin(age_dx)
        if not bin_id:
            continue
        if bin_id in ("18", "19"):
            bin_id = "85+"
        case_lookup[zip5][bin_id] += 1

    with connections["popcase_manual_etl"].cursor() as cur:
        cur.execute("""
            SELECT "GEOID", age, SUM(population::numeric)
            FROM age_adjustment_zcta
            WHERE year = %s
            GROUP BY "GEOID", age
        """, [pop_year])
        pop_rows = cur.fetchall()

    pop_lookup = defaultdict(lambda: defaultdict(float))
    for geoid_raw, age_label, pop in pop_rows:
        zip5 = str(geoid_raw).strip()[-5:]
        age_bin = _map_population_age_bin(str(age_label))
        if not age_bin:
            age_bin = str(age_label).strip()
            if age_bin in US2000_STD_WEIGHTS or age_bin == "85+":
                pass
            else:
                continue

        pop_lookup[zip5][age_bin] += float(pop)

    out = {}
    scale = 100000.0 / 1_000_000.0

    for zip5, age_cases in case_lookup.items():
        total = 0.0
        var_sum = 0.0

        for age_bin, weight in US2000_STD_WEIGHTS.items():
            pop = pop_lookup.get(zip5, {}).get(age_bin)
            d = age_cases.get(age_bin, 0)

            if pop and pop > 0:
                total += weight * (d / pop)
                var_sum += (weight ** 2) * (d / (pop ** 2))

        rate = total * scale
        se = math.sqrt(var_sum) * scale if var_sum > 0 else 0.0

        if total > 0:
            lo = max(0.0, rate - 1.96 * se)
            hi = rate + 1.96 * se
            out[zip5] = (round(rate, 1), round(lo, 1), round(hi, 1))
        else:
            out[zip5] = (None, None, None)

    return out


def _resolve_zcta_pop_year(requested_year: str) -> str:
    requested_year = str(requested_year)

    with connections["popcase_manual_etl"].cursor() as cur:
        cur.execute("""SELECT DISTINCT year FROM age_adjustment_zcta""")
        years = [str(r[0]) for r in cur.fetchall() if r and r[0] is not None]

    if not years:
        return requested_year
    if requested_year in years:
        return requested_year

    years_sorted = sorted(years, key=lambda x: int(x))
    return years_sorted[-1]


def _map_population_age_bin(label):
    mapping = {
        "0-4": "01",
        "5-9": "02",
        "10-14": "03",
        "15-19": "04",
        "20-24": "05",
        "25-29": "06",
        "30-34": "07",
        "35-39": "08",
        "40-44": "09",
        "45-49": "10",
        "50-54": "11",
        "55-59": "12",
        "60-64": "13",
        "65-69": "14",
        "70-74": "15",
        "75-79": "16",
        "80-84": "17",
        "85+": "85+",
    }
    return mapping.get(label.strip())


def _safe_literal_eval(s):
    s = (s or "").strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    try:
        return ast.literal_eval(s)
    except Exception:
        return None


def _geo_label(geographic_level: str, geoid: str) -> str:
    if geographic_level == "county":
        nm = OHIO_COUNTY_NAMES.get(geoid)
        return f"{nm} County" if nm else f"County {geoid}"
    if geographic_level == "tract":
        return f"Census Tract {geoid}"
    if geographic_level == "zcta":
        return f"ZIP {geoid}"
    return str(geoid)


def _get_tract_sex_distribution_lookup():
    sex_lookup = {}

    for row in (
        Acs5YrB01001.objects
        .filter(geographic_level="tract")
        .values("geo_id", "total_population", "total_male", "total_female")
    ):
        tract = _tract_from_geo_id(row["geo_id"])
        if not tract:
            continue

        total_pop = row.get("total_population")
        male = row.get("total_male")
        female = row.get("total_female")

        sex_lookup[tract] = {
            "male_population": male,
            "female_population": female,
            "male_pct": _safe_pct(male, total_pop),
            "female_pct": _safe_pct(female, total_pop),
        }

    return sex_lookup


def _estimate_grouped_median_age(counts):
    normalized = []
    for lower, upper, count in counts:
        c = float(count or 0)
        normalized.append((lower, upper, c))

    total = sum(count for _, _, count in normalized)
    if total <= 0:
        return None

    halfway = total / 2.0
    cum = 0.0

    for lower, upper, count in normalized:
        prev = cum
        cum += count
        if cum >= halfway:
            width = (upper - lower) if upper is not None else 5
            if count == 0:
                return round((lower + (upper if upper is not None else lower + 5)) / 2.0, 1)
            frac = (halfway - prev) / count
            return round(lower + frac * width, 1)

    return None


def _get_tract_median_age_lookup():
    lookup = {}

    for row in Acs5YrB01001.objects.filter(geographic_level="tract").values(
        "geo_id",
        "m_under5", "m_5_9", "m_10_14", "m_15_17", "m_18_19", "m_20", "m_21", "m_22_24",
        "m_25_29", "m_30_34", "m_35_39", "m_40_44", "m_45_49", "m_50_54", "m_55_59",
        "m_60_61", "m_62_64", "m_65_66", "m_67_69", "m_70_74", "m_75_79", "m_80_84", "m_85_plus",
        "f_under5", "f_5_9", "f_10_14", "f_15_17", "f_18_19", "f_20", "f_21", "f_22_24",
        "f_25_29", "f_30_34", "f_35_39", "f_40_44", "f_45_49", "f_50_54", "f_55_59",
        "f_60_61", "f_62_64", "f_65_66", "f_67_69", "f_70_74", "f_75_79", "f_80_84", "f_85_plus",
    ):
        tract = _tract_from_geo_id(row["geo_id"])
        if not tract:
            continue

        counts = [
            (0, 5, (row["m_under5"] or 0) + (row["f_under5"] or 0)),
            (5, 10, (row["m_5_9"] or 0) + (row["f_5_9"] or 0)),
            (10, 15, (row["m_10_14"] or 0) + (row["f_10_14"] or 0)),
            (15, 18, (row["m_15_17"] or 0) + (row["f_15_17"] or 0)),
            (18, 20, (row["m_18_19"] or 0) + (row["f_18_19"] or 0)),
            (20, 21, (row["m_20"] or 0) + (row["f_20"] or 0)),
            (21, 22, (row["m_21"] or 0) + (row["f_21"] or 0)),
            (22, 25, (row["m_22_24"] or 0) + (row["f_22_24"] or 0)),
            (25, 30, (row["m_25_29"] or 0) + (row["f_25_29"] or 0)),
            (30, 35, (row["m_30_34"] or 0) + (row["f_30_34"] or 0)),
            (35, 40, (row["m_35_39"] or 0) + (row["f_35_39"] or 0)),
            (40, 45, (row["m_40_44"] or 0) + (row["f_40_44"] or 0)),
            (45, 50, (row["m_45_49"] or 0) + (row["f_45_49"] or 0)),
            (50, 55, (row["m_50_54"] or 0) + (row["f_50_54"] or 0)),
            (55, 60, (row["m_55_59"] or 0) + (row["f_55_59"] or 0)),
            (60, 62, (row["m_60_61"] or 0) + (row["f_60_61"] or 0)),
            (62, 65, (row["m_62_64"] or 0) + (row["f_62_64"] or 0)),
            (65, 67, (row["m_65_66"] or 0) + (row["f_65_66"] or 0)),
            (67, 70, (row["m_67_69"] or 0) + (row["f_67_69"] or 0)),
            (70, 75, (row["m_70_74"] or 0) + (row["f_70_74"] or 0)),
            (75, 80, (row["m_75_79"] or 0) + (row["f_75_79"] or 0)),
            (80, 85, (row["m_80_84"] or 0) + (row["f_80_84"] or 0)),
            (85, 90, (row["m_85_plus"] or 0) + (row["f_85_plus"] or 0)),
        ]

        lookup[tract] = _estimate_grouped_median_age(counts)

    return lookup


def _get_latest_tiger_tract_year():
    """
    Returns the latest available year in tiger_tract_shapefiles.
    Falls back to None if unavailable.
    """
    yr = (
        TigerTractShapefile.objects
        .values_list("year", flat=True)
        .order_by("-year")
        .first()
    )
    return str(yr) if yr is not None else None


@lru_cache(maxsize=8)
def _get_tract_mammography_access_lookup(year="2013", radius_miles=20.0):
    """
    Builds tract-level mammography facility proximity/access using tract internal
    point coordinates and FDA mammography facility points.

    Outputs per tract:
      - nearest_miles
      - count_20mi
      - access_score = sum(1 / (1 + distance_miles)) across facilities within radius
    """
    tract_rows = list(
        TigerTractShapefile.objects
        .filter(year=str(year))
        .values("geoid", "intptlat", "intptlon")
    )

    facility_rows = list(
        FdaMammographyFacility.objects
        .using("popcase_manual_etl")
        .all()
        .values("id", "lat", "long")
    )

    facilities = []
    for f in facility_rows:
        lat = _safe_float(f.get("lat"))
        lon = _safe_float(f.get("long"))
        if lat is None or lon is None:
            continue
        facilities.append((lat, lon))

    lookup = {}

    if not facilities:
        return lookup

    for row in tract_rows:
        geoid = str(row.get("geoid") or "").strip()
        lat = _safe_float(row.get("intptlat"))
        lon = _safe_float(row.get("intptlon"))

        if not geoid or lat is None or lon is None:
            continue

        nearest = None
        count_within = 0
        access_score = 0.0

        for flat, flon in facilities:
            d = _haversine_miles(lat, lon, flat, flon)

            if nearest is None or d < nearest:
                nearest = d

            if d <= radius_miles:
                count_within += 1
                access_score += 1.0 / (1.0 + d)

        lookup[geoid] = {
            "nearest_miles": round(nearest, 2) if nearest is not None else None,
            "count_20mi": count_within,
            "access_score": round(access_score, 4),
        }

    return lookup


RACE_TABLE_SPECS = {
    "acs_5yr_B01001A": {"geo_col": "GEO_ID", "total_col": "B01001A_001E"},
    "acs_5yr_B01001B": {"geo_col": "GEO_ID", "total_col": "B01001B_001E"},
    "acs_5yr_B01001C": {"geo_col": "GEO_ID", "total_col": "B01001C_001E"},
    "acs_5yr_B01001D": {"geo_col": "GEO_ID", "total_col": "B01001D_001E"},
    "acs_5yr_B01001E": {"geo_col": "GEO_ID", "total_col": "B01001E_001E"},
    "acs_5yr_B01001F": {"geo_col": "GEO_ID", "total_col": "B01001F_001E"},
    "acs_5yr_B01001G": {"geo_col": "GEO_ID", "total_col": "B01001G_001E"},
    "acs_5yr_B01001H": {"geo_col": "GEO_ID", "total_col": "B01001H_001E"},
    "acs_5yr_B01001I": {"geo_col": "GEO_ID", "total_col": "B01001I_001E"},
}
