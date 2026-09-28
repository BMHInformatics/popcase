# FR59: Youngest and Oldest age-group menus

The Filters page now has two dropdowns labeled **Youngest** and **Oldest**.
County and Census Tract use the existing 20 groups (00 years through 90+);
Place and ZCTA use the existing 18 groups (0–4 through 85+).

The range includes both endpoints and every intervening group. Selecting the
same group in both menus selects just that group. Reversed ranges and choices
outside the current geography's age groups are rejected. Defaults span all ages
and preserve the existing unrestricted-age behavior, including unknown ages.

The form expands narrower ranges into the existing `age_groups` filter so case
queries, population denominators, rates, stratification, and selection summaries
continue using the same age bands. Submitted stale checkbox data cannot override
the new menus. Saved checkbox selections initialize the endpoint menus; if gaps
are filled, a message asks the user to review the inclusive range. Saved endpoints
also adapt to the available boundary groups after a geography change.

Verification covers both sets of menus, default/full ranges, same-group and
open-ended ranges, invalid/reversed selections, saved selections, geography
switches, and the actual ORM-generated age query against in-memory records.
No database schema or source data changes are required.
