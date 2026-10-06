# FR56: cancer-center drive times

The Measures page now includes NCI and CoC-accredited Academic Comprehensive
Cancer Program (ACAD) choices under “Drive time to nearest cancer center from
[ZCTA/Place/County] centroid.” The tract prompt now says “Drive time to nearest
cancer center from census tract centroid.” Existing survey and provider measures
retain their own groups and select-all behavior.

The implementation reads the `default` database, schema `public`. It joins each
geography's `coc_travelmatrix_*_to_center_car_p50.to_id` to
`coc_travelmatrix_center_lookup.id`, filters the lookup's `Type` to `NCI` or
`Academic Comprehensive Cancer Program`, and takes the minimum nonnegative
`travel_time_p50` independently for each origin and category. Null and negative
times and unmatched centers do not qualify. A valid zero remains zero. Missing
categories remain null, including origins without matching cancer patients.
Geography IDs retain their standard zero-padded widths. Existing geography scope
filters apply before output. The measures flow into results, stratified reports'
community rows, and CSV exports.

On October 5, 2026, `default.public.coc_travelmatrix_place_to_center_car_p50`
was verified through the existing geography-aware reader. Its 4,113 routes cover
1,215 seven-digit Place GEOIDs: all have CoC ACAD routes and 447 have NCI routes.
The temporary Place unavailable-data message has been removed. Restart
application workers after deploying to clear cached lookups/results created
before the Place table was loaded. Missing NCI routes remain blank.
The block-group matrix is available to the reader; this FR does not add a new
patient-level reporting workflow or provider-density calculations.

Live coverage: 85 county origins (27 with NCI routes), 3,105 tract origins
(1,367 with NCI routes), and 1,186 ZCTA origins (435 with NCI routes); all have
ACAD routes. ZCTA counts here describe source coverage before Ohio scope filters.
Sample category minima were independently checked against individual route rows.

Source values range from 0 to 120 across the matrices. Neither table comments nor
column names establish units or a route cutoff. Values are preserved without
conversion; unit and cutoff confirmation has been requested. No missing route is
interpreted as zero or as a confirmed travel time exceeding 120.

Regression tests cover category-specific minima, invalid and missing routes,
zero preservation, padded IDs, missing Place tables, all four geographic forms,
dataset values, and CSV headers. No source data or database schema is changed.
