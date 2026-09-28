# FR60: county filtering for ZCTA and Place

Verified on September 28, 2026: the `default` database contains
`public.county2020_zcta2020_crosswalk` (1,216 ZCTAs) and
`public.county2020_place2020_crosswalk` (1,265 Places). Both cover all 88 Ohio
counties. Each geography has one county assignment and an overlap fraction above
0.5. The app uses those supplied assignments, rather than inventing an any-overlap
or population-weighted rule.

Both sources store the target GEOID in `zcta_geoid`; in the Place table this is
actually the seven-digit Place GEOID (`statefp || placefp`). County membership is
read from `county_geoid`. The lookup is cached per application process.

The Filters form now accepts single-county, multiple-county, and NEO-15 catchment
selections when comparing ZCTAs or Places. The same assignment limits disease,
community, access, rate, and stratified output rows. Existing statewide behavior
is unchanged. Missing assignments exclude an area from a county-restricted run;
missing, empty, malformed, or conflicting crosswalk sources raise an availability
error instead of silently returning statewide results.

County selection chooses whole ZCTAs/Places. Registry queries use patient links
to the selected ZCTAs/Places, not the patients' own county links. This preserves
whole-area numerators alongside whole-area population denominators, community
estimates, and centroid travel times. Ohio reference rates remain statewide.
The Filters page explains this interpretation, including cross-county areas.

Regression coverage includes crosswalk validation/cache behavior, single/multiple
county and catchment membership, form acceptance/unavailability, whole-area
patient linking, and ordinary and stratified rates. No database tables or source
data are modified. Restart application workers on deployment to load the changes
and clear cached reports; restart after replacing the crosswalk data as well.

Validation: 76 regression tests passed. Live Cuyahoga County checks returned
52 ZCTA rows and 48 Place rows with available selected measures. Every result
matched a crosswalk assignment, and each row's case count and cancer-center
access value matched its whole-area value in the statewide run. The crosswalk
assigns 57 Places to Cuyahoga; not every Place has data for the selected measures.
