# Patient-level access reporting

## Live audit and corrections - 2026-10-06

The results preview now decodes sex, race, Hispanic origin, invasion indicators,
receptor summaries, and special regional-node codes using NAACCR definitions.
Unrecognized categorical codes are shown explicitly, and missing values remain
missing. CSV exports retain original registry codes; this is a presentation
change, not a rewrite of source data. ICD-O site/histology codes remain codes.
Sources: [NAACCR v25 dictionary](https://apps.naaccr.org/data-dictionary/data-dictionary/version=25/)
(items 160, 190, 220, 830, 3827, 3915, 3855, 3909) and v24 items 820/1182.

All 46,518 source records were checked for date consistency. Last contact equals
the effective diagnosis date in 43,923 records, precedes it in 1,276, and follows
it in 1,319. The effective diagnosis date remains the later of Date of Diagnosis
and Date Conclusive DX, as specified in the SDD. Follow-up age is calculated
independently from Date of Last Contact minus Date of Birth, even when last
contact precedes diagnosis. These source-date inconsistencies are preserved in
the reported ages. Database records are not modified. The output is age at death
**or last follow-up**, not universally
age at death.

Patient stratification is still an implementation gap: the form deliberately
clears row/column/table grouping for this mode, and the results builder emits
individual registry records rather than calling the grouped-report builder.
This is not a missing database connection or a limitation of patient data.

Known source gaps verified in both configured databases:

- All ten required treatment-date columns exist, but only Date Initial RX SEER
  contains values. First-treatment intervals therefore work through the SEER
  fallback; CoC first-course and the eight other modality intervals remain blank.
- No cervical-screening fields exist in the four loaded CDC PLACES tables.
- No B17017 table exists for the requested household-poverty percentage.
- No SVI table exists. HRI tables do not provide the requested redlined-population
  percentage; those two options remain disabled.
- Rurality confidence intervals and descriptions are not supplied for every
  source; a populated rurality code does not imply those fields are populated.

The live audit selected every patient variable, all six access measures, all
available community measures, and their display options for 2000q1-2025q4.
It consumed all 42,147 eligible records for each community geography: County
(394 output columns), Place (390), ZCTA (378), and Tract (382). Every required
registry column exists. The source gaps above account for all completely blank
output columns; remaining columns have values, though individual records can
still lack a geographic link or source observation. All-registry output was
separately checked for its 926-column schema and unchanged raw codes.

Across those eligible records, provider measures each have 42,080 nonmissing
values, mammography 42,144, CoC ACAD drive time 41,717, and NCI drive time 21,004.
Provider scaling and mammography values were independently compared with source
rows. Missing cancer-center routes are not assigned zero. Updated live previews
were also checked for readable demographic labels. Regression coverage verifies
that follow-up ages preserve the source dates even when they precede diagnosis.
The complete regression suite passed (214 tests).

The long patient results explanation was removed as requested. The preview-size
notice remains so a limited on-screen preview is not mistaken for the full CSV.

## Expanded patient Measures wireframe - 2026-10-05

The patient Measures page now includes demographics, location, disease variables,
the existing block-group access choices, and community measures at the patient's
Tract, ZCTA, Place, or County. The implementation follows the supplied
`PopCASE Wireframes-patient level.pdf` and the SDD patient-measures section.
This section supersedes the initial access-only scope described below.

Patient-only and community-only selections are valid. Registry patient ID always
identifies each selected record; other raw registry fields are included only when
selected. Raw CSV field names and coded values remain unchanged. All-registry output
uses database schema introspection, including columns absent from the unmanaged
Django model (926 columns in the current database). It disables the individual
patient-variable/access choices; community variables can still be added.

New patient selections include sex, calculated ages, race, ethnicity, four location
identifiers/names, county RUCC and tract RUCA, tumor sequence, cancer site/type and
breast receptor subtype, histology, lymphovascular/perineural invasion, regional
nodes examined/positive, treatment intervals, and prostate-only Gleason score.
Site labels reuse the app's cancer-site classification rules. Breast subtypes
use ER/PR hormone-receptor status and HER2, retaining unknown status when the
required receptor information is missing. Pathological Gleason takes priority
over clinical score; registry special/unknown codes do not become valid scores.

Calculated ages are completed years from DOB to the latest complete diagnosis or
conclusive-diagnosis date, or to last contact. Partial/invalid dates stay missing.
Treatment intervals use that same diagnosis date. The first-treatment choice uses
the CoC first-course date when valid, otherwise the earliest available SEER or
modality date, as specified in the SDD. Separate outputs cover all ten listed
treatment-date fields. Negative intervals remain missing; no undocumented 365-day
cutoff is applied to the individual intervals.

Community fields reuse existing ACS/CDC PLACES calculations and CI options, with
age-adjusted survey access available only for County and Place. Outputs are
prefixed `community_` and labeled by geography to distinguish area-level values
from individual patient fields. The current wireframe uses the most recent
available community sources. All links use the configured 2023 linkage vintage;
community geography is independent of block-group access. County filters use the
patient's county link at that vintage. Missing or conflicting links never choose
an arbitrary geography or drop a patient from an otherwise unscoped extract.

Two wireframe choices are shown disabled: Social Vulnerability
Index (no SVI table in either configured database), and population percentage in
formerly redlined neighborhoods (the loaded HRI index is not that percentage).
The implementation does not substitute ADI for SVI or HRI for a percentage.

Full CSV exports stream records instead of materializing the 926-column dataset.
Community output rows are restricted to linked geographic IDs before assembly,
so patient reports do not assemble hundreds of columns for all national ZCTAs.
This restricts output areas, not the population used to calculate area measures.
ACS source reads also apply a parameterized geographic-ID restriction. This
request-local restriction is reset even when a source read fails, and scoped
results bypass the shared geography-wide lookup cache.
The preview retains only the first 200 rows while counting all qualifying records.
The administrator checks and private/no-store responses remain in effect. Older
saved access-only selections continue to work until the Measures form is saved
with the expanded selection schema.

Validation includes browser checks for the selection controls, all-registry
override, community-only continuation and geography-dependent availability;
regression tests for calculation rules, record preservation, missing/conflicting
links, original registry names, streaming CSV and bounded preview; and live reads
of all selected patient fields and the complete 926-column registry schema.

Implemented 2026-09-28 against the [current SDD](https://docs.google.com/document/d/1N--cVxlRRnP5vkEdPGafroTbYUHvEYFbfxnhm2SBC_Y/edit).
This supersedes the pending block-group reporting work in provider-access.md.

Active Django staff or superusers can select Patient-level (Administrator),
choose access measures, preview up to 200 registry records, and export all
matching records. Wizard, results, and CSV endpoints enforce this restriction.
Patient datasets bypass the shared results cache; responses use private/no-store.

Each row is a qualifying **cancer registry record**, not a unique patient. The
export retains registry patient ID, tumor sequence, diagnosis date, site,
histology, behavior, demographic codes, and stage code. Internal PostgreSQL row
keys preserve distinct tumors across cancer-site unions but are never exported.
FR54 eligibility and selected diagnosis period, sex, age, race/ethnicity, stage,
cancer sites, county scope, and multiple-primary exclusion apply. Aggregate rates
and subgroup comparisons are not offered in this raw extract workflow.

## Sources and linkage

Provider sources use `popcase_manual_etl.public.travel_bg_2020`, numeric
`count.x * 100000`, with exact source filters:

| Choice | source_file |
| --- | --- |
| Primary care | bg_2020_familypractice_fixepcp1_1220.RDS |
| Oncology | bg_2020_familypractice_fixecnc1_1220.RDS |
| Extended cancer care | bg_2020_familypractice_fixecnc2_1220.RDS |

These are the loaded block-group equivalents of the SDD's December 2020 pcp1,
cnc1, and cnc2 sources; its copied tract `.xlsx.xlsx` filenames do not exist in
the block-group table. Each selected RDS source has 9,407 distinct block groups.
Other specialties, months, and provider variants are not combined.

Mammography uses `default.public.fda_mammography_travel_bg`, joins `id` to block
group GEOID, and reads `mammo_per_100k` directly (9,466 distinct IDs). It does not
use the tract distance proxy or multiply the stored rate again.

NCI and CoC ACAD times use `coc_travelmatrix_blockgroup_to_center_car_p50` and the
authoritative center lookup through the existing FR56 reader. Missing routes
remain missing; values retain source units and are not labeled as minutes.

Patient linkage uses `naaccr_patient_census_linking`, `geographic_level=block_group`,
`year=2023`. Every one of its 43,920 patient links matches the mammography surface;
the older 2013/2018 vintages match only 36,031 of 43,921. The default is pinned to
2023, not the latest year in unrelated geography tables. An explicit
`POPCASE_PATIENT_ACCESS_LINKAGE_YEAR` setting can change it after verifying source
compatibility. The export includes the chosen year and linkage status.

Identical duplicate links collapse; conflicting or invalid links produce blank
access values. Missing links do not drop otherwise eligible records. When county
scope is selected, the county comes from the chosen block group's first five
digits; records without a usable link cannot establish county membership.
Source duplicates, nonfinite/negative measurements, and failed sources produce
blank values. Reported zero remains zero. Patient addresses are not queried.

## Validation

Regression tests cover exact sources, scaling, missing/duplicate data, preserved
tumors, filters, authorization, stale wizard state, uncached exports, and empty
CSV headers. Live read-only checks for 2020q1–2022q4 returned 14,972 records for
14,832 patients, including one record with a missing link. Nonmissing values:
14,951 for each provider measure, 14,971 mammography, 14,796 CoC ACAD, and 7,439 NCI.
Missing routes were not treated as zero. No migrations or data writes are required.
