"""County provider counts: distinct clinicians, not enrollment/address rows."""

from collections import defaultdict
import logging
import math

from django.db import connections

logger = logging.getLogger(__name__)

OUTPUTS = {
    'pcp_access_score': 'primary_care_providers_per_100k',
    'onc': 'oncology_providers_per_100k',
    'ext_care': 'extended_cancer_care_providers_per_100k',
    'mammo_access': 'mammography_facilities_per_100k',
}


def normalized(value):
    return str(value or '').strip().upper()


def provider_categories(primary, secondary):
    """SDD Lookup Tables, including corrections of its spelling errors."""
    p, s = normalized(primary), normalized(secondary)
    p = {'NURSE PRACTIONER': 'NURSE PRACTITIONER',
         'INTERVENTINOAL RADIOLOGY': 'INTERVENTIONAL RADIOLOGY'}.get(p, p)
    s = {'INTERVENTINOAL RADIOLOGY': 'INTERVENTIONAL RADIOLOGY'}.get(s, s)
    categories = set()
    general = {'', 'GENERAL PRACTICE'}
    if (p == 'FAMILY PRACTICE'
        or p in {'PEDIATRIC MEDICINE', 'INTERNAL MEDICINE'}
        and s in general | {'PEDIATRIC MEDICINE', 'INTERNAL MEDICINE'}
        or p in {'GERIATRIC MEDICINE', 'NURSE PRACTITIONER'} and s in general):
        categories.add('pcp_access_score')
    oncology = {'HEMATOLOGY/ONCOLOGY', 'RADIATION ONCOLOGY', 'MEDICAL ONCOLOGY',
                'SURGICAL ONCOLOGY', 'GYNECOLOGICAL ONCOLOGY', 'HEMATOLOGY'}
    expanded = {'UROLOGY', 'THORACIC SURGERY', 'GENERAL SURGERY', 'GASTROENTEROLOGY',
                'DERMATOLOGY', 'PATHOLOGY', 'DIAGNOSTIC RADIOLOGY',
                'INTERVENTIONAL RADIOLOGY', 'COLORECTAL SURGERY', 'NEUROSURGERY',
                'OTOLARYNGOLOGY'}
    if p in oncology or s in oncology:
        categories.update({'onc', 'ext_care'})
    if p in expanded:
        categories.add('ext_care')
    return categories


def count_providers(rows):
    """Resolve exact address/ZIP matches, then deduplicate NPI per county/category.

    A clinician practicing in two counties counts once in each. Specialty rows
    contribute category membership by union; repeated rows never add providers.
    """
    addresses = defaultdict(set)
    for npi, primary, secondary, address, zipcode, counties in rows:
        if len(counties) == 1:
            addresses[(normalized(address), str(zipcode or '')[:5])].update(counties)
    members = defaultdict(lambda: defaultdict(set))
    audit = {'records': len(rows), 'recovered_records': 0, 'unassigned_records': 0,
             'unassigned_qualifying_records': 0, 'invalid_npi_records': 0}
    for npi, primary, secondary, address, zipcode, counties in rows:
        categories = provider_categories(primary, secondary)
        if not counties and normalized(address) and zipcode:
            candidates = addresses[(normalized(address), str(zipcode)[:5])]
            if len(candidates) == 1:
                counties = list(candidates)
                audit['recovered_records'] += 1
        if len(counties) != 1:
            audit['unassigned_records'] += 1
            audit['unassigned_qualifying_records'] += bool(categories)
            continue
        npi = str(npi or '').strip()
        if len(npi) != 10 or not npi.isdigit():
            audit['invalid_npi_records'] += 1
            continue
        for category in categories:
            members[counties[0]][category].add(npi)
    return {g: {t: len(ids) for t, ids in groups.items()} for g, groups in members.items()}, audit


def get_county_access_lookup(requested):
    from . import services
    requested = set(requested) & OUTPUTS.keys()
    if not requested:
        return {}
    # Exactly the denominator vintage named in the shared SDD.
    populations = {}
    counts, audit = {}, {}
    available = set()
    facility_unassigned = 0
    with connections['popcase_manual_etl'].cursor() as cur:
        cur.execute('''SELECT "GEO_ID", "B01001_001E"
                       FROM public.acs_5y_b01001
                       WHERE "ACSyear"='ACS5yr2020' AND "GEO_ID" LIKE '0500000US39%%' ''')
        for geoid, population in cur.fetchall():
            geoid = str(geoid).strip()[-5:]
            if geoid in populations:
                raise ValueError('Duplicate ACS2020 county population')
            populations[geoid] = {'total_population': population}
        if requested - {'mammo_access'}:
            cur.execute('''
                SELECT p."NPI", p.pri_spec, p.sec_spec_1, p.adr_ln_1, p."ZIP Code",
                       ARRAY_REMOVE(ARRAY_AGG(DISTINCT c.geoid), NULL)
                FROM public.ohio_doctorsclinicians p
                LEFT JOIN public.tiger_2021_us_county_shapefiles c
                  ON c.statefp='39' AND c.geom && p.geom AND ST_Covers(c.geom,p.geom)
                WHERE UPPER(TRIM(p."State"))='OH'
                GROUP BY p.id,p."NPI",p.pri_spec,p.sec_spec_1,p.adr_ln_1,p."ZIP Code"
            ''')
            provider_rows = cur.fetchall()
            counts, audit = count_providers(provider_rows)
            if provider_rows:
                available.update(requested - {'mammo_access'})
        if 'mammo_access' in requested:
            cur.execute('''SELECT UPPER(TRIM(p."Facility_Name")),
                           UPPER(TRIM(p."Address1")), UPPER(TRIM(p.city)),
                           ARRAY_REMOVE(ARRAY_AGG(DISTINCT c.geoid), NULL)
                    FROM public.fda_mammography_facilities p
                    LEFT JOIN public.tiger_2021_us_county_shapefiles c
                      ON c.statefp='39' AND c.geom && p.geom AND ST_Covers(c.geom,p.geom)
                    WHERE UPPER(TRIM(p.state))='OH'
                    GROUP BY 1,2,3''')
            facility_rows = cur.fetchall()
            if facility_rows:
                available.add('mammo_access')
            for name, address, city, counties in facility_rows:
                if not name or not address or len(counties) != 1:
                    facility_unassigned += 1
                    continue
                bucket = counts.setdefault(counties[0], {})
                bucket['mammo_access'] = bucket.get('mammo_access', 0) + 1
    missing = audit.get('unassigned_qualifying_records', 0)
    notes = []
    if missing:
        notes.append(f'{missing} qualifying provider source record(s) could not be assigned to an Ohio county; counts may be incomplete.')
    if audit.get('invalid_npi_records'):
        notes.append(f"{audit['invalid_npi_records']} provider records have invalid identifiers and were excluded.")
    if facility_unassigned:
        notes.append(f'{facility_unassigned} facility locations could not be uniquely identified or assigned to an Ohio county.')
    if requested - available:
        notes.append('A selected source has no Ohio records; its rates are unavailable.')
    note = ' '.join(notes)
    if note:
        logger.warning(note)
    result = {}
    for geoid, row in populations.items():
        population = services._safe_float(row.get('total_population'))
        out = {}
        for token in requested:
            count = counts.get(geoid, {}).get(token, 0)
            out[OUTPUTS[token]] = round(count * 100000 / population, 2) if token in available and population and math.isfinite(population) and population > 0 else None
        if note:
            out['provider_data_note'] = note
        result[geoid] = out
    return result
