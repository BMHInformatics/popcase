# FR54 behavior eligibility review

The app already applied behavior 3 or bladder-site behavior 2 in the shared
`apply_naaccr_filters` function. Incidence, disease summaries, and stratification
use this function. Regional stage codes 2, 3, 4, and 5 are already grouped together.

The missing detail was the histology exclusions used by the existing Urinary
Bladder definition (`popcase/cancer_site_logic.csv`): 9050–9055, 9140, and 9590–9992.
These now exclude behavior-2 cases from the bladder exception even without a
cancer-site selection. Behavior-3 cases remain eligible regardless of site or
histology, subject to any separately selected filters. Behavior-2 cases require
a C670–C679 site and nonmissing histology outside the excluded ranges.

The supplied `FR54_behavior_code_filter.sql` captures the intended histology
exclusions, but its complementary `NOT` branch can discard malignant records
when site or histology is NULL. The implementation instead uses the simpler
predicate: behavior 3 OR (behavior 2 AND qualifying urinary bladder cancer).
Blank histology is also treated as missing for the in-situ exception.

The separate mortality path continues to use cause-of-death eligibility.
No database schema or stored records are changed.

Regression coverage in `popcase/tests/test_fr54.py` executes ORM-generated SQL
against synthetic in-memory records, covering behavior codes, site and histology
boundaries, missing values, and individual/overlapping cancer selections.
