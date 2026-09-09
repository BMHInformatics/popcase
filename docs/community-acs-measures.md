# Community ACS measures

The Measures page now connects the twelve previously empty ACS choices, and
extends internet access into component outputs. Limited English proficiency is
corrected to sum all relevant language groups. These measures describe the
community population, not the filtered NAACCR case cohort.

## Sources and definitions

The reader prefers `census_build.acs_39_<CODE>_<geography>_<end_year>` and falls
back to `acs_5yr_<CODE>` using the requested year and geographic level. It does
not substitute a later period when a historical source is missing. Geography
keys come from `GEO_ID`. The same output fields drive the results table and CSV.

Below, cell numbers identify `<CODE>_<cell>E`; margins use the corresponding
`M` fields. Unless otherwise stated, each numerator is divided by cell `001E`
of the same table and multiplied by 100.

| Measure | Code | Numerators / estimate | Denominator population |
| --- | --- | --- | --- |
| Age distribution | B01001 | Male cells 003–005, 006–013, 014–015, 016–019, 020–023, 024–025, plus female counterparts offset by 24 | Total population; bands 0–14, 15–39, 40–49, 50–64, 65–79, 80+ |
| Marital status | B07008 | Married 003; not married 002+004+005+006 | Population age 15+; separated is not married |
| Education | B06009 | 002 through 006, separately | Population age 25+ |
| Language at home | C16001 | English 002; language totals 003,006,009,…,036 | Population age 5+; use published combined language categories |
| Limited English | C16001 | 005+008+011+…+038 | Population age 5+; all languages with English less than very well |
| Citizenship | B07007 | Native 002, naturalized 004, non-citizen 005 | Population age 1+; excludes infants because of the table's mobility universe |
| Income-to-poverty ratio | B17026 | 002 through 013, separately | Families; twelve ratio bands, not an estimated average ratio |
| Median housing costs | B25105 | Published 001E; no division | Median monthly housing costs in dollars |
| Overcrowding | B25014 | 005+006+007+011+012+013 | Occupied housing units; more than one occupant per room |
| Complete plumbing | B25048 | 002 | Occupied housing units |
| Complete kitchen facilities | B25052 | 002 | Occupied housing units |
| Female householder, no spouse present | B11005 | 007+010+016+019 | All households; includes family and nonfamily households, with and without people under 18. Historical labels say “no husband present.” |
| Grandparents responsible for grandchildren | B10063 | 003 | All households; responsibility for own grandchildren under 18, not merely co-residence |
| Internet subscription/access | B28002 | Any subscription 002, dial-up only 003, any broadband 004, cable/fiber/DSL 007, cellular only 006, satellite only 010, access without subscription 012, no access 013 | All households; these selected categories overlap and must not be added together |

Definitions were checked against the official 2023 Census group metadata and
the 2013/2018 versions for the mapped cells. C16001 and B28002 are not available
in the checked 2013 release/layout. Missing periods remain unavailable.

## Approximate 95% confidence intervals

ACS `M` values are published at 90% confidence. For a direct estimate E:

`CI95 = E ± (1.96 / 1.645) × M90`.

For a derived percentage, let N and D be numerator and denominator estimates,
and let Mn and Md be their 90% margins of error. Set p = N / D:

`MOE95(percent) = 100 × (1.96 / 1.645) × sqrt(Mn² − p² Md²) / D`.

If the expression under the square root is negative, use the ACS ratio
approximation with addition instead of subtraction. Tiny floating-point
residuals around mathematical zero are clamped to zero. For sums of disjoint
categories, add the estimates and combine MOEs using root-sum-of-squares.
Percentage bounds are restricted to 0–100. These are approximation formulas,
not calculations using Census replicate weights.

Every component has its own CI. The section's “Display 95% Confidence
Intervals” checkbox controls these columns for both current and historical
output and CSV export. An aggregate distribution does not receive a fictitious
single CI.

`EA` and `MA` are annotations. Null annotations are normal. Missing numeric
values, invalid values, annotated bounds, and most negative Census sentinel
values remain unavailable. An unavailable MOE does not remove a valid estimate,
but no CI is invented. The specific controlled-total MOE `-555555555` / `*****`
contributes zero sampling error, as Census specifies. Zero denominators produce
unavailable percentages, not zero percentages.

This handling applies to the new catalog. Older independent ACS readers retain
their existing behavior; this change does not claim to re-audit all older
community metrics or the deferred redlining/index measures.

## Validation

Automated tests cover ratio/sum MOEs, controlled totals, missing values,
annotations, denominators, category mappings, year selection, UI labels,
column ordering, and CI visibility for current/historical output.
Read-only live checks use public ACS data and exclude registry patient records.

The September 9, 2026 live verification exercised all fourteen catalog choices
at county (88 rows), tract (3,168), place (1,265), and ZCTA (33,772) levels.
Every county had valid estimates and CIs for every choice. Other levels retain
unavailable values for zero denominators, missing estimates, or Census special
values. ZCTA row counts reflect the nationwide source table, not Ohio-only
coverage. Current, historical, and combined-timeframe dataset/column paths were
also checked using Adams County: married percentage is 54.30 for 2019–2023 and
48.23 for 2014–2018. Detailed local counts are in
`tmp/community_live_verification.json` (not committed).

## References

- [Official Census variable groups](https://api.census.gov/data/2023/acs/acs5/groups.html)
- [B07008 marital-status variables](https://api.census.gov/data/2023/acs/acs5/groups/B07008.html)
- [ACS margin-of-error guidance](https://www.census.gov/content/dam/Census/programs-surveys/acs/guidance/training-presentations/20170419_MOE.pdf)
- [Census annotations and controlled totals](https://www.census.gov/data/developers/data-sets/acs-1year/notes-on-acs-estimate-and-annotation-values.html)
