from typing import Dict, Any
from functools import lru_cache
import csv

from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.contrib.auth import views as auth_views
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
    build_mvp_geo_dataset,
)

from .models import NaaccrPatientCensusLinking


STEPS = ["geographic-level", "filters", "measures", "stratification"]
PREVIEW_ROW_LIMIT = 250
SUPPORTED_DISEASE_MEASURES = {
    "case_count",
    "pct_advanced",
    "pct_advanced_ci",
    "pct_metastatic",
    "pct_metastatic_ci",
    "inc_rate",
    "inc_ci",
    "mort_rate",
    "mort_ci",
}
NORMALIZED_TOTAL_LEVELS = {None, "", "none", "total", "do_not_compare", "no_compare"}

TRACT_HEADER_MAP = {
    "label": "Location",
    "tract_geoid": "Census Tract",
    "case_count": "Case count",
    "n_total_staged_unstaged": "N",
    "pct_advanced": "% Advanced",
    "adv_ci_lower": "% Advanced CI 95% (L)",
    "adv_ci_upper": "% Advanced CI 95% (U)",
    "pct_metastatic": "% Metastatic",
    "meta_ci_lower": "% Metastatic CI 95% (L)",
    "meta_ci_upper": "% Metastatic CI 95% (U)",
    "age_adjusted_per_100k": "Age-adjusted incidence /100,000",
    "inc_ci_lower_per_100k": "Incidence CI 95% (L) /100,000",
    "inc_ci_upper_per_100k": "Incidence CI 95% (U) /100,000",
    "total_population": "Total population",
    "median_household_income": "Median household income",
    "limited_english_pct": "% speak English less than very well",
    "mammography_screening_pct": "Mammography screening (%)",
    "routine_checkup_pct": "Routine checkup (%)",
    "lack_transportation_pct": "Lack reliable transportation (%)",
    "uninsured_pct": "Uninsured age 18-64 (%)",
    "primary_care_access_score": "Primary care access score",
    "male_population": "Male population",
    "female_population": "Female population",
    "male_pct": "Male (%)",
    "female_pct": "Female (%)",
    "median_age": "Median age (approx)",
    "nearest_mammography_distance_miles": "Nearest mammography facility (miles)",
    "mammography_facility_count_20mi": "Mammography facilities within 20 miles",
    "mammography_access_score": "Mammography access score",
    "white_alone_pct": "White alone (%)",
    "black_alone_pct": "Black alone (%)",
    "aian_alone_pct": "AI/AN alone (%)",
    "asian_alone_pct": "Asian alone (%)",
    "nhpi_alone_pct": "NHPI alone (%)",
    "other_race_alone_pct": "Other race alone (%)",
    "multiracial_pct": "Two or more races (%)",
    "nh_white_pct": "NH White (%)",
    "hispanic_pct": "Hispanic (%)",
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
    "age_adjusted_per_100k",
    "inc_ci_lower_per_100k",
    "inc_ci_upper_per_100k",
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
    "female_pct",
    "median_age",
    "nearest_mammography_distance_miles",
    "mammography_facility_count_20mi",
    "mammography_access_score",
    "white_alone_pct",
    "black_alone_pct",
    "aian_alone_pct",
    "asian_alone_pct",
    "nhpi_alone_pct",
    "other_race_alone_pct",
    "multiracial_pct",
    "nh_white_pct",
    "hispanic_pct",
]


class PopcaseLoginView(auth_views.LoginView):
    template_name = "popcase/login.html"


class PopcaseLogoutView(auth_views.LogoutView):
    pass


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


def _get_measure_selections(measures_state: dict, geographic_level: str):
    disease_measures = _coerce_to_list(measures_state.get("disease_measures"))
    cancer_prevention_measures = _coerce_to_list(measures_state.get("cancer_prevention"))
    community_measures = _coerce_to_list(measures_state.get("community_characteristics"))

    access_field_by_geo = {
        "tract": "access_comm_tract",
        "county": "access_comm_county",
        "zcta": "access_comm_zcta_place",
    }
    access_measures = _coerce_to_list(measures_state.get(access_field_by_geo.get(geographic_level)))
    support_measures = _unique_in_order(cancer_prevention_measures + community_measures + access_measures)

    return disease_measures, support_measures


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

    keys = sorted(leaf_meta, key=sort_key)
    return [
        (k, (leaf_meta.get(k, {}).get("Site_sub_sub") or leaf_meta.get(k, {}).get("Site_sub")))
        for k in keys
    ]


def _build_cancer_type_labels(selected_leaf_keys):
    if not selected_leaf_keys:
        return []

    _, leaf_meta = get_cancer_type_tree()

    def _pretty_label(k: str) -> str:
        meta = leaf_meta.get(k) or {}
        return (
            (meta.get("Site_sub_sub") or "").strip()
            or (meta.get("Site_sub") or "").strip()
            or (meta.get("Sites") or "").strip()
            or k
        )

    return _unique_in_order(_pretty_label(k) for k in selected_leaf_keys)


SEX_SPECIFIC_CANCER_SEX = {
    "cervix uteri": "F",
    "corpus uteri": "F",
    "uterus, nos": "F",
    "uteros, nos": "F",   # included defensively in case the source label has this spelling
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
    return redirect("popcase:wizard_step", step="geographic-level")


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

    form = FormClass(request.POST or None, initial=None if request.method == "POST" else initial)
    if step == "filters":
        form.fields["cancer_types"].choices = leaf_choices

    if request.method == "POST" and form.is_valid():
        cleaned_data = _clean_session_value(form.cleaned_data)
        _session_set(request, step, cleaned_data)

        if step == "geographic-level":
            _session_set(request, "geographic_level", form.cleaned_data.get("geographic_level", "none"))

        if "prev" in request.POST:
            prev_step = STEPS[max(0, STEPS.index(step) - 1)]
            return redirect("popcase:wizard_step", step=prev_step)

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


def results(request):
    wizard = request.session.get("popcase_wizard", {})
    filters = wizard.get("filters", {}) or {}
    geographic_level = _normalize_geographic_level(wizard.get("geographic_level", "county"))
    measures_state = wizard.get("measures", {}) or {}

    disease_measures, support_measures = _get_measure_selections(measures_state, geographic_level)
    year = _latest_linking_year()
    dx_start = (filters.get("dx_start") or "2011").strip() or "2011"
    dx_end = (filters.get("dx_end") or "2022").strip() or "2022"
    cancer_type_labels = _build_cancer_type_labels(filters.get("cancer_types") or [])

    incidence = []
    total_incidence = None
    mvp_rows = []
    mvp_rows_preview = []
    result_mode = "none"
    dataset_total_rows = 0
    dataset_is_truncated = False

    has_dataset_request = bool(SUPPORTED_DISEASE_MEASURES.intersection(disease_measures) or support_measures)

    if geographic_level in {"tract", "county", "zcta"} and has_dataset_request:
        mvp_rows = build_mvp_geo_dataset(
            geographic_level=geographic_level,
            year_range=(dx_start, dx_end),
            filters=filters,
            disease_measures=disease_measures,
            support_measures=support_measures,
            incidence_year=year,
        ) or []
        dataset_total_rows = len(mvp_rows)
        dataset_is_truncated = dataset_total_rows > PREVIEW_ROW_LIMIT
        mvp_rows_preview = mvp_rows[:PREVIEW_ROW_LIMIT]
        result_mode = "dataset"
    elif geographic_level == "total":
        total_incidence = get_total_incidence(year=year, filters=filters)
        result_mode = "incidence" if total_incidence else "none"
    else:
        incidence = get_incidence_by_geography(
            year=year,
            geographic_level=geographic_level,
            filters=filters,
        ) or []
        result_mode = "incidence" if incidence else "none"

    context = {
        "wizard_state": wizard,
        "filters": filters,
        "year": year,
        "geographic_level": geographic_level,
        "incidence": incidence,
        "total_incidence": total_incidence,
        "mvp_rows": mvp_rows_preview,
        "dataset_total_rows": dataset_total_rows,
        "dataset_preview_limit": PREVIEW_ROW_LIMIT,
        "dataset_is_truncated": dataset_is_truncated,
        "result_mode": result_mode,
        "disease_measures": disease_measures,
        "cancer_type_labels": cancer_type_labels,
        "dataset_title": f"Selected measures by {geographic_level.title()}",
        "tract_header_map": TRACT_HEADER_MAP,
        "tract_numeric_cols": TRACT_NUMERIC_COLS,
    }
    return render(request, "popcase/results.html", context)


def reset_wizard(request):
    request.session.pop("popcase_wizard", None)
    request.session.modified = True
    return redirect("popcase:wizard_step", step="geographic-level")


def export_mvp_geo_csv(request):
    wizard = request.session.get("popcase_wizard", {})
    filters = wizard.get("filters", {}) or {}
    geographic_level = _normalize_geographic_level(wizard.get("geographic_level", "county"))
    measures_state = wizard.get("measures", {}) or {}
    disease_measures, support_measures = _get_measure_selections(measures_state, geographic_level)

    dx_start = (filters.get("dx_start") or "2011").strip() or "2011"
    dx_end = (filters.get("dx_end") or "2022").strip() or "2022"

    if geographic_level == "total":
        rows = []
        filename = f"popcase_results_total_{dx_start}_{dx_end}.csv"
    else:
        rows = build_mvp_geo_dataset(
            geographic_level=geographic_level,
            year_range=(dx_start, dx_end),
            filters=filters,
            disease_measures=disease_measures,
            support_measures=support_measures,
            incidence_year=_latest_linking_year(),
        ) or []
        filename = f"popcase_results_{geographic_level}_{dx_start}_{dx_end}.csv"

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f"attachment; filename={filename}"

    if rows:
        fieldnames = list(rows[0].keys())
        seen = set(fieldnames)
        for row in rows[1:]:
            for key in row.keys():
                if key not in seen:
                    fieldnames.append(key)
                    seen.add(key)
    else:
        fieldnames = ["label"]

    writer = csv.DictWriter(response, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return response
