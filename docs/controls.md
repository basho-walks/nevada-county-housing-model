# Housing outcomes, covariates and candidate controls

Issue #4. This file lists what `ingest/housing.py` puts in `data/housing_zip_year.csv`, and the
availability of each candidate control at ZIP/ZCTA level. Source IDs (S11-S17) refer to
`docs/sources.md` (PR #15). Geography, key and missing codes follow `docs/schema.md`.

Rebuild with `uv run python -m ingest.housing` (add `--refresh` to download again). The run
writes `data/housing_zip_year.csv`, `data/housing_zip_year.meta.json` and
`data/interim/unmatched/housing_zip_year.csv`, and records each raw snapshot in
`data/raw/housing/manifest.json`.

## 1. What the table contains

Key: `(zcta, year)`, unique. `policy_form = ALL` on every row, so the canonical key
`(zcta, year, policy_form)` from `docs/schema.md` is unique too. Rows are the union of
ZCTA-years with any Zillow month and ZCTA-years with an ingested ACS window. Missing values are
not filled.

Build of 2026-10-05: 39,400 rows, 1,802 California 2020 ZCTAs, years 2000-2026. 1,541 of the
1,543 California Zillow ZIPs map to a ZCTA. 95424 and 95433 have no 2020 ZCTA and are in the
unmatched file.

### 1.1 Zillow ZIP ZHVI (S11)

- Product: `zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month` (all homes, 33rd-67th percentile tier,
  smoothed, seasonally adjusted). This is the same product as the city file used by `model.py`
  (S12), at ZIP level. Column `zhvi_product`.
- Revision: Zillow recomputes the whole history every month. The `Last-Modified` date of the
  source file is stored in `zhvi_revision` (2026-09-16 in this build) and in the manifest.
- IDs: `zillow_region_id` (Zillow `RegionID`), `zip_source` (Zillow `RegionName`, read as a
  string), `zillow_county` (Zillow `CountyName`).
- Geography: Zillow ZIP to 2020 ZCTA by identity (`zcta_method = identity`, schema section 4.1).
- Streaming: the 124 MB national file is read line by line and only `State == CA` rows are written.
  The California snapshot is not committed (Zillow terms allow derived works only). Its hash is
  in the manifest, so `verify_manifest(RAW)` on a fresh clone always lists that file until
  `ingest/housing.py` downloads it again.
- Monthly to annual: calendar-year mean, kept only when the year has 10 or more months. This is
  the `annual_mean` rule in `fetch_data.py`. `zhvi_months` gives the month count, and `zhvi_rule
  = mean_ge10m` marks the rule. A year with 1-9 months has `zhvi` empty and
  `zhvi_missing = 5`. 2026 has 8 months, so it is excluded.
- Units: `zhvi` is nominal US dollars. `zhvi_real` is in `BASE_YEAR` (2025) dollars:
  `nominal * CPI[2025] / CPI[year]`, with CPI-U from `data/national_annual.csv`. `BASE_YEAR` in
  `ingest/housing.py` must equal `BASE_YEAR` in `model.py`. A test checks this.

### 1.2 ACS 5-year ZCTA covariates (S13)

| Column | ACS table | Unit |
|--------|-----------|------|
| `population` | B01003 | persons, 5-year average |
| `households` | B11001 | households, 5-year average |
| `median_hh_income` | B19013 | US dollars of the window end year |
| `median_hh_income_real` | B19013 and CPI-U | `BASE_YEAR` (2025) dollars |
| `housing_units` | B25001 | housing units |
| `vacant_units` | B25002 line 3 | housing units |
| `vacancy_rate` | B25002 lines 3 and 1 | share 0-1 |

- Each value has a `_moe` column (90% margin of error). The vacancy-rate MOE uses the Census
  proportion formula (ratio formula when the term under the root is negative). The real-income
  MOE is scaled by the same CPI factor. Census annotation `-555555555` (controlled estimate) is
  stored as MOE 0. Other negative annotation values are stored as empty.
- `year` is the last year of the window. `acs_window` holds the window (for example
  `2020-2024`) and `acs_window_start` holds its first year. Adjacent windows share four of five
  years. Do not use them as independent annual observations or difference them as annual
  changes (`docs/estimands.md` section 6).
- Ingested windows: 2017-2021, 2018-2022, 2019-2023, 2020-2024. They come from the table-based
  summary file, which starts with the 2021 release and needs no API key. All four use 2020 ZCTAs
  (`GEO_ID` prefix `860Z200US`). The code rejects a file with other summary levels.
- Not ingested: windows 2007-2011 to 2016-2020. They exist only as sequence-based summary files
  or through the Census API, which needs a key (S13). Windows ending 2011-2019 also use 2010
  ZCTAs and need the S14 crosswalk. Rows for those years have `acs_missing = 5`. Rows before
  2011 or after 2024 have `acs_missing = 3`.
- `acs_missing = 2` marks a row where at least one estimate is annotated (198-212 ZCTAs per
  window, mostly small or zero-population ZCTAs, where median income is not published).
- Small ZCTAs have large MOEs. For example, Nevada County ZCTA 95986 has a population MOE
  larger than its estimate in every ingested window (2020-2024: 115, MOE 133). Use the MOE in any comparison.

### 1.3 Bay Area spillover sources

Low-fire-exposure Bay Area ZCTAs are rows with `candidate_spillover_source = 1`. They are a
possible source of demand that moves to the foothills. They are **not** controls or donors
(`docs/estimands.md` section 5). Use them, for example, as a lagged Bay Area price-trend measure.

The screen is `spillover_screen = provisional_v0`:

- County (Zillow `CountyName`): Alameda, Contra Costa, San Francisco, San Mateo, Santa Clara.
  Marin, Napa and Sonoma are not included because `docs/estimands.md` names them as exposed.
  Solano is not included because of the 2020 LNU Lightning Complex.
- Hill ZCTAs inside those counties are removed: Oakland and Berkeley hills, Orinda, Moraga, the
  Santa Cruz Mountains, and Mount Hamilton. The list is in `SPILLOVER_EXCLUDED_ZCTAS` in
  `ingest/housing.py` and in the meta file.
- 180 ZCTAs pass. This is a judgment screen, not a measurement. Replace it with the CDI S2
  high/extreme exposure share and the nonrenewal rate (estimands section 5, criteria 1-2) when
  the insurance ingest exists. Increase the screen version when you do.

### 1.4 Baseline model inputs

`model.py` inputs are not changed: `data/city_zhvi_annual.csv` (Zillow city, S12) and the
county panel stay as they are. Use the ZIP table to compare with them. For example, ZCTA 95945
2025 ZHVI is $480,727. The Grass Valley city series is $508,998. They describe different areas
(`docs/schema.md` section 1).

Replacement cost (Coverage A, S2) belongs to the insurance table, not to this table. Keep it in
nominal and real columns there, the same as ZHVI here.

## 2. Candidate controls: availability

"Available" means published at ZIP or ZCTA level. "County-only" means the finest public
geography is the county. "Absent" means no public source was found. Status "ingested" means it
is in `data/housing_zip_year.csv` now.

| Candidate | Measure and source | ZIP/ZCTA availability | Status |
|-----------|--------------------|-----------------------|--------|
| Population | ACS B01003, 5-year | available (5-year average only) | ingested 2017-2021 to 2020-2024 |
| Population, annual | Census population estimates (S17) | county-only | in `data/ca_county_panel.csv`; never moved to ZCTAs |
| Households | ACS B11001, 5-year | available | ingested |
| Income | ACS B19013 median household income | available | ingested |
| Income | BEA per-capita personal income (S16 PCPI) | county-only | in county panel |
| Housing supply | ACS B25001 housing units | available | ingested |
| Housing supply | Census building permits (S16 BPPRIV) | county-only | in county panel |
| Vacancy | ACS B25002 vacant units and rate | available | ingested |
| Seasonal vacancy (second homes) | ACS B25004 | available | not ingested |
| Migration flows by origin | IRS SOI county-to-county flows; ACS county-to-county flows | county-only | not ingested |
| Migration, in-movers | ACS B07001 (moved from another county or state, no origin) | available (5-year) | not ingested |
| Migration, address changes | USPS change-of-address counts | absent (not public) | none |
| 30-year mortgage rate | Freddie Mac PMMS (S16 MORTGAGE30US) | national only (absent below national) | in `data/national_annual.csv`; absorbed by year effects |
| Pandemic indicator | Calendar dummy for 2020-2022 | national (no data needed) | not stored; build in the model |
| Pandemic, remote work | ACS work-from-home share (B08301) | available (5-year) | not ingested |
| Pandemic, COVID case rates | CDPH | county-only | not ingested |
| Local employment | BLS QCEW, LAUS | county-only | not ingested |
| Local employment | Census ZIP Business Patterns (workplace jobs by ZIP) | available (ZIP, not ZCTA) | not ingested |
| Local employment | LEHD LODES (jobs by block, sums to ZCTA) | available | not ingested |
| Bay Area demand | Zillow ZIP ZHVI for screened Bay Area ZCTAs | available | ingested as tagged rows (section 1.3) |
| House prices, second source | FHFA HPI (S16 ATNHPI) | county in use; FHFA also lists a ZIP5 HPI | ZIP5 not verified, not ingested |

Availability of the sources that are not ingested was not checked by download in this issue.
Confirm it in `docs/sources.md` before you use one of them.

## 3. Rules

- ZIP or ZCTA population is never derived from county growth rates or county totals. A ZCTA-year
  without an ACS window has empty `population`, `households` and `housing_units`. A test checks
  this.
- County series stay in county tables. If a later issue joins them to the ZCTA panel, they get
  a `county_` prefix and `lineage = county_control` (`docs/schema.md` section 4.5).
