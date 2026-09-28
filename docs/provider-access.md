# Primary care provider measure

## Tract access completion — 2026-09-28

This update supersedes the tract gaps recorded in the historical sections below.
All four tract access choices now have SDD-based calculations:

- PCP retains its approved `pcp1_1220_tr.xlsx.xlsx` source and existing override.
- Oncology uses `cnc1_1220_tr.xlsx.xlsx` from `popcase_manual_etl.public.travel_tract_2020`.
- Extended cancer care uses `cnc2_1220_tr.xlsx.xlsx` from that same table.
- All three provider measures use numeric `count.x * 100000`. The source stores
  `count.x` as text; the reader validates/converts it before multiplication.
- Mammography uses `default.public.fda_mammography_travel_tract.id` as tract GEOID
  and reads `mammo_per_100k` directly, without multiplying it again.

The tract mammography selection no longer calls the distance/20-mile proxy.
New oncology, extended-care, and mammography output keys and table/CSV labels
identify travel-time-adjusted values separately from county provider densities.
Missing/invalid values remain unavailable; real zeros remain zero. Duplicate
tract IDs are omitted, and failed sources do not return partial results.

Validation: 31 regression tests passed. Every connected value was compared with
its live source: 3,150 oncology, 3,150 extended-care, and 3,162 mammography values.
A live Cuyahoga dataset returned 461 tract rows with matching values and no old
mammography proxy columns. No database data or schemas were modified. Restart
application workers when deploying to clear cached reports and lookups.

Block-group/patient reporting remains separate work: connect the patient measure
selections; load the approved block-group provider sources and mammography rate;
join patients to block groups using a defined linking vintage and duplicate-link
policy; implement patient results/export (including the intended administrator
access restriction); and support the intended patient subgroup summaries. The
current SDD block-group filenames refer to tract-style XLSX names, whereas the
loaded block-group provider sources use `bg_2020_familypractice_fixe...1220.RDS`
names. That source mapping must be reconciled before connecting those measures.

## Authoritative source

Use the current [Google Drive Software Design Document](https://docs.google.com/document/d/1N--cVxlRRnP5vkEdPGafroTbYUHvEYFbfxnhm2SBC_Y/edit), inspected 2026-09-09 (modified 2026-09-08). It supersedes the local DOCX used for the initial audit.

For tract PCP, it explicitly specifies:
- Table: travel_tract_2020.
- Filter: source_file = pcp1_1220_tr.xlsx.xlsx.
- Calculation: count.x * 100000.

The older local copy specified weighted_SA_final and did not identify the file. That earlier mapping and the associated missing-source blocker are superseded. pcp1, pcp2, and pcp3 are filename prefixes stored in source_file, not database columns. The current SDD selects pcp1_1220_tr.xlsx.xlsx; it does not explain every other variant's meaning.

The [Lookup Tables](https://docs.google.com/spreadsheets/d/1suHwC3miACkjXwB7YHr9oo0y0W1K4z4cc20rZcsUgGY/edit) define the provider categories through primary and secondary specialties.

## Implementation and verification

Both PCP readers use get_pcp_tract_lookup. Its default is the exact SDD filename, and it reads the Django count_x field (database column count.x), multiplying by 100,000. It retains genuine zeros, omits invalid values and duplicate tract IDs, and discards partial results on database failures. The output key remains primary_care_access_score; the label states the units and travel adjustment.

An explicit POPCASE_PCP_TRACT_SOURCE_FILE setting or environment variable can override the default for an approved replacement dataset. An explicitly blank configuration disables output. Restart workers after source changes to clear dataset caches.

Read-only verification found 3,150 rows, 3,150 distinct tract IDs, and 3,150 non-null count.x values for the SDD-selected file. The reader returned 3,150 tract values. All 64 project tests passed.

## Other confirmed sources and remaining work

The current SDD also specifies tract oncology as cnc1_1220_tr.xlsx.xlsx and expanded cancer care as cnc2_1220_tr.xlsx.xlsx, both using count.x * 100000. These are not implemented by this PCP correction.

The Source Data Background/FDA_mammography folder contains MammoTravel_OH_tract.csv with id, count, and mammo_per_100k, plus the block-group equivalent and facility files. The current SDD directs tract mammography to FDA_Mammography_travel_tract.mammo_per_100k.

The Drs_downloadable_file_cms folder contains clinician CSVs for 2016 and 2020, DAC_NationalDownloadableFile.csv, and CMS_providers_geocoded_12_24.csv. The connector could not read the roughly 1 GB geocoded CSV (HTTP 413); its columns were not verified from that file. The NPI folder contains readme/code-value PDFs and header-only CSVs, not a complete provider dataset.

## County measures completed on 2026-09-09

The four requested county measures are connected: PCP, oncology, extended cancer care, and mammography facilities. The user's explicit per-100,000 instruction resolves the SDD's inconsistent per-10,000 controller text.

- Provider source: popcase_manual_etl.public.ohio_doctorsclinicians; NPI, pri_spec, sec_spec_1, geom, adr_ln_1, ZIP Code.
- Facility source: popcase_manual_etl.public.fda_mammography_facilities; Facility_Name, Address1, city, state, geom.
- County assignment: public.tiger_2021_us_county_shapefiles, limited to statefp=39. The loaded provider table has no FIPS column. Its points and these polygons both use SRID 4326. ST_Covers assigns locations; multiple matches are rejected. Do not use tiger_ohio_county_shapefiles without a vintage filter: it contains 9,701 rows spanning multiple vintages and states.
- Denominator: public.acs_5y_b01001, ACSyear=ACS5yr2020, county GEO_ID prefix 0500000US39, column B01001_001E. All 88 Ohio county denominators are present. The default database's ACS table lacks 2020, so it must not substitute 2018 or 2023.
- Count each NPI once per county and qualifying category. Multiple locations/enrollments within a county do not multiply the provider count. A provider in multiple counties counts once in each. Oncology is also included in extended cancer care; categories must not be summed as disjoint groups.
- Normalize specialty case/whitespace and the Lookup Tables' NURSE PRACTIONER / INTERVENTINOAL spelling errors to match loaded values. Nurse practitioners with blank or general-practice secondary specialties qualify under the lookup.
- Count facilities by normalized facility name/address/city, rejecting locations that cannot be uniquely assigned. All 336 Ohio facility records were distinct and matched exactly one county.
- Divide each count by its county population and multiply by 100,000. Zero is retained when a populated source has no qualifying provider/facility in the county. Empty sources and nonpositive/missing denominators produce missing rates, not zero.

### Provider validation and remaining source correction

The source has 96,465 Ohio records and 54,857 distinct NPIs. No missing or malformed 10-digit NPI values were found. Of these records, 96,452 matched exactly one Ohio county; 13 had out-of-state coordinates. Two nurse-practitioner records were recovered through a unique, exact normalized address and five-digit ZIP match to another located record. No source data was modified.

Eleven records remain unassigned, of which one qualifies for the selected provider categories (internal medicine). It is excluded and the output includes a provider source data note that counts may be incomplete. The data owner should correct that practice location's geocode; another practice location of the same NPI does not establish this location's county. This is a source-data limitation, not an unconnected UI control.

### Survey measures

Routine checkup, lack of reliable transportation, uninsured adults 18-64, and dentist visits read the CDC PLACES county table. Their crude prevalence/95% CI and age-adjusted prevalence/95% CI are connected. Adjusted intervals are displayed/exported only when both the CI and age-adjustment options are selected. Crude and adjusted intervals remain separately labeled. The same interval plumbing applies to the place survey option.

### Validation and scope

All 72 project tests pass, including distinct NPI counting, overlapping specialty categories, exact address recovery, zero versus unavailable sources, all CI/age-adjustment checkbox combinations, and CSV headers. Live county lookup returned values for all four measures in all 88 Ohio counties; all four survey measures and each crude/adjusted interval also had values for all 88. The complete dataset path was exercised. The deduplicated county-category membership totals were 21,829 PCP, 1,128 oncology, and 8,111 extended cancer care; these are sums across counties, not statewide unique clinician totals.

This completes the eight county items requested in this change. It does not complete the broader Access to Care section across all geographies: tract oncology/extended care and replacement of the tract mammography proxy, plus CoC/NCI travel measures, are separate remaining work. Restart application workers after deploying to clear cached datasets. Changes are not committed automatically.
