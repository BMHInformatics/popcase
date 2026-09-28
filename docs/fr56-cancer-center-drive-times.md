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

The requested list has four matrices and one center lookup, not five matrices.
On September 28, 2026, both configured databases were checked: County, Tract,
ZCTA and Block Group matrices exist in `default`; no Place matrix exists.
Place choices are present with an unavailable-data message. The reader supports
`coc_travelmatrix_place_to_center_car_p50` if supplied with the same columns and
seven-digit Place GEOIDs. Remove the temporary Place message when enabling that
source, and restart application workers to clear cached lookups/results.
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
