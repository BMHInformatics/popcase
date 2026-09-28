# Patient-level access reporting

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
