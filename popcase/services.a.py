import re
import csv
import math
import ast
import json
from math import sqrt
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



def build_mvp_tract_dataset(
    year_range=("2011", "2022"),
    filters=None,
    disease_measures=None,
    incidence_year=None,
):
    if filters is None:
        filters = {}
    if disease_measures is None:
        disease_measures = []

    if isinstance(disease_measures, str):
        disease_measures = [disease_measures]
    disease_measures = set(disease_measures)

    dx_start, dx_end = year_range
    filters = dict(filters)
    filters["dx_start"] = str(dx_start)
    filters["dx_end"] = str(dx_end)
    neo15_scope = _is_neo15_scope(filters)

    filtered_qs = apply_naaccr_filters(NaaccrData.objects.all(), filters)
    stage_by_mid = dict(filtered_qs.values_list("mid", "stg_grp"))
    if not stage_by_mid:
        return []

    filtered_pat_ids = list(stage_by_mid.keys())

    linking_rows = (
        NaaccrPatientCensusLinking.objects
        .filter(geographic_level="tract", pat_id__in=filtered_pat_ids)
        .values_list("pat_id", "geoid")
        .distinct()
    )

    denom_ids = {"0", "1", "2", "3", "4", "5", "6", "7", "9"}
    adv_ids = {"2", "3", "4", "5", "7"}
    meta_id = "7"
    non_applicable_ids = {"8"}

    denom_by_tract = defaultdict(int)
    adv_by_tract = defaultdict(int)
    meta_by_tract = defaultdict(int)

    geo_scope = (filters.get("geography") or "all_ohio").strip().lower()

    for pat_id, tract_geoid in linking_rows:
        if not tract_geoid:
            continue

        tract_geoid = str(tract_geoid).strip()
        if len(tract_geoid) < 5:
            continue

        if geo_scope in ("neo15", "neo_15", "catchment15", "catchment_15"):
            county_geoid = tract_geoid[:5]
            if county_geoid not in NEO_15_COUNTY_GEOIDS:
                continue

        stg = stage_by_mid.get(pat_id)
        if stg is None:
            continue

        stg = str(stg).strip()
        if not stg or stg in non_applicable_ids:
            continue

        if stg in denom_ids:
            denom_by_tract[tract_geoid] += 1
            if stg in adv_ids:
                adv_by_tract[tract_geoid] += 1
            if stg == meta_id:
                meta_by_tract[tract_geoid] += 1

    def _prop_ci(a, n):
        if n <= 0:
            return (None, None)
        p = a / n
        se = sqrt(p * (1 - p) / n) if n > 0 else 0.0
        lo = max(0.0, p - 1.96 * se)
        hi = min(1.0, p + 1.96 * se)
        return (lo, hi)

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
            geographic_level="tract",
            filters=filters,
        )
        for r in inc_rows:
            incidence_lookup[r["geoid"]] = r

    rows = []
    all_tracts = set(denom_by_tract.keys()) | set(incidence_lookup.keys())

    for tract_geoid in all_tracts:
        out = {"tract_geoid": tract_geoid}

        if "case_count" in disease_measures:
            if tract_geoid in denom_by_tract:
                out["case_count"] = int(denom_by_tract[tract_geoid])
            elif tract_geoid in incidence_lookup:
                out["case_count"] = int(incidence_lookup[tract_geoid].get("case_count", 0))
            else:
                out["case_count"] = 0

        if "pct_advanced" in disease_measures:
            n = int(denom_by_tract.get(tract_geoid, 0))
            a = int(adv_by_tract.get(tract_geoid, 0))
            out["n_total_staged_unstaged"] = n
            if n > 0:
                p = a / n
                lo, hi = _prop_ci(a, n)
                out["pct_advanced"] = round(p * 100, 2)
                out["adv_ci_lower"] = round(lo * 100, 2) if lo is not None else None
                out["adv_ci_upper"] = round(hi * 100, 2) if hi is not None else None
            else:
                out["pct_advanced"] = None
                out["adv_ci_lower"] = None
                out["adv_ci_upper"] = None

        if "pct_metastatic" in disease_measures:
            n = int(denom_by_tract.get(tract_geoid, 0))
            m = int(meta_by_tract.get(tract_geoid, 0))
            out["n_metastatic"] = m
            if n > 0:
                p = m / n
                lo, hi = _prop_ci(m, n)
                out["pct_metastatic"] = round(p * 100, 2)
                out["meta_ci_lower"] = round(lo * 100, 2) if lo is not None else None
                out["meta_ci_upper"] = round(hi * 100, 2) if hi is not None else None
            else:
                out["pct_metastatic"] = None
                out["meta_ci_lower"] = None
                out["meta_ci_upper"] = None

        if ("crude_inc_rate" in disease_measures) or ("crude_inc_ci" in disease_measures) or ("inc_rate" in disease_measures) or ("inc_ci" in disease_measures):
            ir = incidence_lookup.get(tract_geoid)
            if ("crude_inc_rate" in disease_measures):
                out["crude_incidence_per_100k"] = ir.get("crude_incidence_per_100k") if ir else None
            if ("crude_inc_ci" in disease_measures):
                out["crude_inc_ci_lower_per_100k"] = ir.get("crude_incidence_ci_lower") if ir else None
                out["crude_inc_ci_upper_per_100k"] = ir.get("crude_incidence_ci_upper") if ir else None
            if ("inc_rate" in disease_measures):
                out["age_adjusted_per_100k"] = ir.get("age_adjusted_per_100k") if ir else None
            if ("inc_ci" in disease_measures):
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

        rows.append(out)

    def _sort_key(r):
        if r.get("pct_advanced") is not None:
            return (r.get("pct_advanced"), r.get("case_count") or 0)
        if r.get("incidence_per_100k") is not None:
            return (r.get("incidence_per_100k"), r.get("cases_for_incidence") or 0)
        return (0, 0)

    rows.sort(key=_sort_key, reverse=True)
    return rows


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return []


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


@lru_cache(maxsize=1)
def _get_tract_race_ethnicity_lookup():
    total_pop_lookup = {
        _tract_from_geo_id(row["geo_id"]): float(row["total_population"] or 0)
        for row in (
            Acs5YrB01001.objects
            .filter(geographic_level="tract")
            .values("geo_id", "total_population")
            .iterator(chunk_size=5000)
        )
        if _tract_from_geo_id(row["geo_id"])
    }

    white_lookup = _fetch_acs_total_lookup("acs_5yr_B01001A")
    black_lookup = _fetch_acs_total_lookup("acs_5yr_B01001B")
    aian_lookup = _fetch_acs_total_lookup("acs_5yr_B01001C")
    asian_lookup = _fetch_acs_total_lookup("acs_5yr_B01001D")
    nhpi_lookup = _fetch_acs_total_lookup("acs_5yr_B01001E")
    other_lookup = _fetch_acs_total_lookup("acs_5yr_B01001F")
    multiracial_lookup = _fetch_acs_total_lookup("acs_5yr_B01001G")
    nh_white_lookup = _fetch_acs_total_lookup("acs_5yr_B01001H")
    hispanic_lookup = _fetch_acs_total_lookup("acs_5yr_B01001I")

    all_tracts = set(total_pop_lookup.keys())
    all_tracts |= set(white_lookup.keys())
    all_tracts |= set(black_lookup.keys())
    all_tracts |= set(aian_lookup.keys())
    all_tracts |= set(asian_lookup.keys())
    all_tracts |= set(nhpi_lookup.keys())
    all_tracts |= set(other_lookup.keys())
    all_tracts |= set(multiracial_lookup.keys())
    all_tracts |= set(nh_white_lookup.keys())
    all_tracts |= set(hispanic_lookup.keys())

    lookup = {}
    for tract in all_tracts:
        total_pop = float(total_pop_lookup.get(tract) or 0)

        white = float(white_lookup.get(tract) or 0)
        black = float(black_lookup.get(tract) or 0)
        aian = float(aian_lookup.get(tract) or 0)
        asian = float(asian_lookup.get(tract) or 0)
        nhpi = float(nhpi_lookup.get(tract) or 0)
        other = float(other_lookup.get(tract) or 0)
        multiracial = float(multiracial_lookup.get(tract) or 0)
        nh_white = float(nh_white_lookup.get(tract) or 0)
        hispanic = float(hispanic_lookup.get(tract) or 0)

        lookup[tract] = {
            "white_alone_pct": _safe_pct(white, total_pop),
            "black_alone_pct": _safe_pct(black, total_pop),
            "aian_alone_pct": _safe_pct(aian, total_pop),
            "asian_alone_pct": _safe_pct(asian, total_pop),
            "nhpi_alone_pct": _safe_pct(nhpi, total_pop),
            "other_race_alone_pct": _safe_pct(other, total_pop),
            "multiracial_pct": _safe_pct(multiracial, total_pop),
            "nh_white_pct": _safe_pct(nh_white, total_pop),
            "hispanic_pct": _safe_pct(hispanic, total_pop),
        }

    return lookup


@lru_cache(maxsize=32)
def _get_tract_support_lookups_cached(requested_tuple):
    """
    Returns tract-level lookup dicts for selected non-disease MVP fields.
    """
    requested = set(requested_tuple or ())
    lookups = {}

    if requested & {"pop_total", "sex_distribution", "median_age"}:
        acs_rows = list(
            Acs5YrB01001.objects
            .filter(geographic_level="tract")
            .values(
                "geo_id", "total_population", "total_male", "total_female",
                "m_under5", "m_5_9", "m_10_14", "m_15_17", "m_18_19", "m_20", "m_21", "m_22_24",
                "m_25_29", "m_30_34", "m_35_39", "m_40_44", "m_45_49", "m_50_54", "m_55_59",
                "m_60_61", "m_62_64", "m_65_66", "m_67_69", "m_70_74", "m_75_79", "m_80_84", "m_85_plus",
                "f_under5", "f_5_9", "f_10_14", "f_15_17", "f_18_19", "f_20", "f_21", "f_22_24",
                "f_25_29", "f_30_34", "f_35_39", "f_40_44", "f_45_49", "f_50_54", "f_55_59",
                "f_60_61", "f_62_64", "f_65_66", "f_67_69", "f_70_74", "f_75_79", "f_80_84", "f_85_plus",
            )
            .iterator(chunk_size=5000)
        )
        pop_lookup, sex_lookup, median_age_lookup = {}, {}, {}
        for row in acs_rows:
            tract = _tract_from_geo_id(row["geo_id"])
            if not tract:
                continue
            total_pop = row.get("total_population")
            if "pop_total" in requested:
                pop_lookup[tract] = total_pop
            if "sex_distribution" in requested:
                male = row.get("total_male")
                female = row.get("total_female")
                sex_lookup[tract] = {
                    "male_population": male,
                    "female_population": female,
                    "male_pct": _safe_pct(male, total_pop),
                    "female_pct": _safe_pct(female, total_pop),
                }
            if "median_age" in requested:
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
                median_age_lookup[tract] = _estimate_grouped_median_age(counts)
        if "pop_total" in requested:
            lookups["pop"] = pop_lookup
        if "sex_distribution" in requested:
            lookups["sex"] = sex_lookup
        if "median_age" in requested:
            lookups["median_age"] = median_age_lookup

    if "race_eth" in requested:
        lookups["race_eth"] = _get_tract_race_ethnicity_lookup()

    if "med_hh_income" in requested:
        lookups["income"] = {
            _tract_from_geo_id(row["geo_id"]): row["median_household_income"]
            for row in AcsB19013.objects.all().values("geo_id", "median_household_income").iterator(chunk_size=5000)
            if _tract_from_geo_id(row["geo_id"])
        }

    if "limited_english_pct" in requested:
        lookups["limited_english_pct"] = {
            _tract_from_geo_id(row["geo_id"]): _safe_pct(row["limited_english"], row["total_population_5plus"])
            for row in AcsC16001.objects.all().values("geo_id", "limited_english", "total_population_5plus").iterator(chunk_size=5000)
            if _tract_from_geo_id(row["geo_id"])
        }

    if requested & {"breast_screen", "routine_checkup", "no_transport", "no_insurance"}:
        places_lookup = {}
        for row in CDCPlacesTract2024.objects.all().values(
            "tract_fips", "mammography_screening", "routine_checkup", "lack_transportation", "uninsured"
        ).iterator(chunk_size=5000):
            tract = str(row["tract_fips"]).strip() if row["tract_fips"] else None
            if tract:
                places_lookup[tract] = {
                    "breast_screen": row["mammography_screening"],
                    "routine_checkup": row["routine_checkup"],
                    "no_transport": row["lack_transportation"],
                    "no_insurance": row["uninsured"],
                }
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


def _get_tract_support_lookups(requested_support_measures=None):
    normalized = tuple(sorted(_normalize_support_measure_tokens(requested_support_measures or [])))
    return _get_tract_support_lookups_cached(normalized)


def _build_mvp_geo_dataset_uncached(
    geographic_level: str,
    year_range=("2011", "2022"),
    filters=None,
    disease_measures=None,
    support_measures=None,
    incidence_year=None,
):
    """
    Returns list[dict] with one row per geographic unit.

    For the current MVP patch, tract-level rows can include:
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

    tract_support = {}
    if geographic_level == "tract" and support_measures:
        tract_support = _get_tract_support_lookups(support_measures)

    if geographic_level == "tract" and tract_support:
        tract_support = {
            name: _filter_lookup_to_scope(lookup, "tract", filters)
            for name, lookup in tract_support.items()
        }

    all_geoids = set(denom_by_geo.keys()) | set(incidence_lookup.keys())

    if geographic_level == "tract" and tract_support:
        for lookup in tract_support.values():
            all_geoids |= set(lookup.keys())

    all_geoids = {
        g for g in all_geoids
        if _geoid_in_scope(geographic_level, g, filters)
    }

    rows = []

    for geoid in all_geoids:
        out = {
            "label": _geo_label(geographic_level, geoid),
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

        if geographic_level == "tract" and tract_support:
            if "pop_total" in support_measures:
                out["total_population"] = tract_support["pop"].get(geoid)

            if "med_hh_income" in support_measures:
                out["median_household_income"] = tract_support["income"].get(geoid)

            if "limited_english_pct" in support_measures:
                out["limited_english_pct"] = tract_support["limited_english_pct"].get(geoid)

            if "sex_distribution" in support_measures:
                sex_row = tract_support["sex"].get(geoid, {})
                out["male_population"] = sex_row.get("male_population")
                out["female_population"] = sex_row.get("female_population")
                out["male_pct"] = sex_row.get("male_pct")
                out["female_pct"] = sex_row.get("female_pct")

            if "median_age" in support_measures:
                out["median_age"] = tract_support["median_age"].get(geoid)

            places_row = tract_support["places"].get(geoid, {})

            if "breast_screen" in support_measures:
                out["mammography_screening_pct"] = places_row.get("breast_screen")

            if "routine_checkup" in support_measures:
                out["routine_checkup_pct"] = places_row.get("routine_checkup")

            if "no_transport" in support_measures:
                out["lack_transportation_pct"] = places_row.get("no_transport")

            if "no_insurance" in support_measures:
                out["uninsured_pct"] = places_row.get("no_insurance")

            if "pcp_access_score" in support_measures:
                out["primary_care_access_score"] = tract_support["pcp_access"].get(geoid)

            if "mammo_access" in support_measures:
                mammo_row = tract_support.get("mammo_access", {}).get(geoid, {})
                out["nearest_mammography_distance_miles"] = mammo_row.get("nearest_miles")
                out["mammography_facility_count_20mi"] = mammo_row.get("count_20mi")
                out["mammography_access_score"] = mammo_row.get("access_score")

            if "race_eth" in support_measures:
                race_row = tract_support.get("race_eth", {}).get(geoid, {})
                out["white_alone_pct"] = race_row.get("white_alone_pct")
                out["black_alone_pct"] = race_row.get("black_alone_pct")
                out["aian_alone_pct"] = race_row.get("aian_alone_pct")
                out["asian_alone_pct"] = race_row.get("asian_alone_pct")
                out["nhpi_alone_pct"] = race_row.get("nhpi_alone_pct")
                out["other_race_alone_pct"] = race_row.get("other_race_alone_pct")
                out["multiracial_pct"] = race_row.get("multiracial_pct")
                out["nh_white_pct"] = race_row.get("nh_white_pct")
                out["hispanic_pct"] = race_row.get("hispanic_pct")

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
def _build_mvp_geo_dataset_cached(
    geographic_level: str,
    dx_start: str,
    dx_end: str,
    filters_json: str,
    disease_measures_tuple: tuple,
    support_measures_tuple: tuple,
    incidence_year: str,
):
    return _build_mvp_geo_dataset_uncached(
        geographic_level=geographic_level,
        year_range=(dx_start, dx_end),
        filters=_deserialize_cache_payload(filters_json),
        disease_measures=list(disease_measures_tuple),
        support_measures=list(support_measures_tuple),
        incidence_year=incidence_year or None,
    )


def build_mvp_geo_dataset(
    geographic_level: str,
    year_range=("2011", "2022"),
    filters=None,
    disease_measures=None,
    support_measures=None,
    incidence_year=None,
):
    filters = filters or {}
    dx_start, dx_end = year_range
    normalized_disease = tuple(sorted(_as_list(disease_measures)))
    normalized_support = tuple(sorted(_normalize_support_measure_tokens(support_measures)))
    return _build_mvp_geo_dataset_cached(
        geographic_level=geographic_level,
        dx_start=str(dx_start),
        dx_end=str(dx_end),
        filters_json=_serialize_cache_payload(filters),
        disease_measures_tuple=normalized_disease,
        support_measures_tuple=normalized_support,
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
