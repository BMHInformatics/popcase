"""FR53 definitions from the project's Lookup Tables, Risk Factor Groups tab.

Source: https://docs.google.com/spreadsheets/d/1suHwC3miACkjXwB7YHr9oo0y0W1K4z4cc20rZcsUgGY/edit
Site/histology definitions are reused from the application's cancer-site CSV.
"""

import csv
from functools import lru_cache
from pathlib import Path

from django.db.models import Q


RISK_GROUPS = {
    "risk:tobacco": ("Tobacco-related cancers", (
        "Acute Myeloid Leukemia", "Urinary Bladder", "Colon",
        "Rectum and Rectosigmoid Junction", "Esophagus", "Kidney and Renal Pelvis",
        "Renal pelvis, ureter", "Larynx", "Liver", "Lung and Bronchus", "Trachea",
        "Other Oral Cavity and Pharynx", "Oropharynx", "Floor of Mouth", "Tongue",
        "Gum and Other Mouth", "Nasopharynx", "Tonsil", "Hypopharynx", "Pancreas", "Stomach",
    )),
    "risk:obesity": ("Obesity-related cancers", (
        "Esophagus", "Breast", "Colon", "Rectum and Rectosigmoid Junction",
        "Corpus Uteri", "Uterus, NOS", "Gallbladder", "Stomach", "Kidney and Renal Pelvis",
        "Ovary", "Liver", "Pancreas", "Thyroid", "Meninges", "Myeloma",
    )),
    "risk:hpv": ("HPV-related cancers", (
        "Cervix Uteri", "Vagina", "Vulva", "Penis", "Anus, Anal Canal and Anorectum",
        "Tonsil", "Tongue",
    )),
}


def risk_group_metadata():
    return {
        key: {"Sites": "Risk factor groups", "Site_sub": label, "Site_sub_sub": "",
              "risk_group": key}
        for key, (label, _) in RISK_GROUPS.items()
    }


@lru_cache(maxsize=1)
def _site_scripts():
    # Include aggregate rows (e.g. Breast/Myeloma) even when hidden from the UI.
    with (Path(__file__).parent / "cancer_site_logic.csv").open(encoding="utf-8-sig", newline="") as source:
        return {
            (row["Site_sub_sub"] or row["Site_sub"] or row["Sites"]).strip().casefold():
            row["QueryScript"].strip()
            for row in csv.DictReader(source)
        }


def risk_group_query(key, parse_script):
    scripts = _site_scripts()

    def site(name):
        return parse_script(scripts[name.casefold()])

    result = Q(pk__in=[])
    for name in RISK_GROUPS[key][1]:
        if name == "Trachea":
            # The CSV combines trachea with other organs; the lookup names only trachea.
            predicate = site("Trachea, Mediastinum and Other Respiratory Organs") & Q(primary_site="C339")
        elif name == "Renal pelvis, ureter":
            predicate = (site("Kidney and Renal Pelvis") & Q(primary_site="C659")) | site("Ureter")
        else:
            predicate = site(name)
        if key == "risk:obesity":
            if name == "Esophagus":
                predicate &= Q(hist_o3__gte="8140", hist_o3__lte="8389")
            elif name == "Breast":
                predicate &= Q(sex="2", risk_age_at_dx__gte=51, risk_age_at_dx__lt=999)
        result |= predicate
    return result
