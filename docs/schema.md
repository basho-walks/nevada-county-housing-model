# Geography and panel schema

Issue #2. This file fixes the target geography, the boundary vintages, the crosswalks and the
canonical panel key. Source IDs (S1-S17, U1-U4) refer to `docs/sources.md` (issue #1, PR #15).
Column definitions are in `docs/data_dictionary.csv`. The quantities to estimate are in
`docs/estimands.md`. Schema version: **1**. Increase it when a key, a crosswalk or a target ZIP
changes, and record the change at the end of this file.

## 1. Three different geographies

The same five-digit number can name three different areas. The panel never treats them as equal
without a recorded mapping.

| Concept | Who defines it | What it is | Where it is used |
|---------|----------------|------------|------------------|
| USPS ZIP code | US Postal Service | A set of delivery routes. It is not a polygon. It can be a PO-box-only or single-address ZIP. It changes without notice. | CDI files (S2, S3, S6), FAIR Plan files (S9, S10), Zillow ZIP ZHVI (S11) |
| ZCTA (ZIP Code Tabulation Area) | Census Bureau | A polygon made from census blocks, assigned to the most common ZIP in each block. Fixed for a decade (2010 vintage, 2020 vintage). PO-box-only ZIPs have no ZCTA. | ACS (S13), Census relationship files (S14), all joins in the panel |
| City limits (Census place) | City, reported to Census as an incorporated place | The legal boundary of Grass Valley or Nevada City. Much smaller than the ZIPs that carry the city name. | Context only. Zillow "city" regions (S12) are Zillow definitions, not Census places. |

Rules:

- A "Grass Valley" ZIP (95945, 95949) covers large unincorporated areas outside the Grass Valley
  city limits. Do not report a ZIP value as a city value, or the reverse.
- The panel unit is the **2020 ZCTA**. ZIP-level inputs are mapped to ZCTAs (section 4). The
  original ZIP stays in the row as `zip_source`.
- Zillow city series (S12, used by `model.py`) are not joined to the ZCTA panel. They stay in
  `data/city_zhvi_annual.csv` for the existing model.

## 2. Target ZIPs

The target is Grass Valley, Nevada City and the surrounding **western** Nevada County. The
Truckee area (eastern Nevada County) has a different housing market (resort and second homes,
Tahoe commuters) and a different fire and insurance profile. It is excluded from the target.

Shares below are land-area shares of each 2020 ZCTA by county, computed from
`tab20_zcta520_county20_natl.txt` (S14) as `AREALAND_PART / AREALAND_ZCTA5_20`. Land area is
not population. Replace these with housing-unit shares when the HUD crosswalk (S15) or a block
allocation is available.

| ZIP | Place name | 2020 ZCTA | Nevada County land share | Tier |
|-----|------------|-----------|--------------------------|------|
| 95945 | Grass Valley | 95945 | 1.00 | core |
| 95949 | Grass Valley (south, Alta Sierra) | 95949 | 1.00 | core |
| 95959 | Nevada City | 95959 | 1.00 | core |
| 95946 | Penn Valley | 95946 | 1.00 | core |
| 95975 | Rough and Ready | 95975 | 1.00 | core |
| 95986 | Washington | 95986 | 1.00 | core |
| 95960 | North San Juan | 95960 | 0.37 (Sierra 0.39, Yuba 0.24) | fringe |
| 95977 | Smartsville | 95977 | 0.39 (Yuba 0.61) | fringe |
| 95602 | Auburn (Lake of the Pines area) | 95602 | 0.21 (Placer 0.79) | fringe |
| 95712 | Chicago Park | none (PO box) | n/a | core, unmapped |
| 95924 | Cedar Ridge | none (PO box) | n/a | core, unmapped |

Tiers:

- **core**: inside western Nevada County. Always in the target.
- **fringe**: the ZCTA crosses into another county. It is in the panel with `geo_tier = fringe`.
  Every result is reported with and without fringe ZCTAs. 95602 is mostly the City of Auburn
  (Placer County). It is kept only for the Lake of the Pines area, and it is the first to drop
  in a sensitivity check.
- **core, unmapped**: a USPS ZIP with no 2020 ZCTA. Its rows go to the unmatched report
  (section 5) until the HUD crosswalk (S15) is available. Their delivery areas probably lie inside a
  neighboring core ZCTA (likely 95945). This is not verified. Do not add them to any ZCTA by
  assumption.

Excluded (eastern Nevada County): 96160, 96161, 96162 (Truckee), 95724 (Norden), 95728 (Soda
Springs), 96111 (Floriston). 95715 appears in the 2010 relationship file only.

Confirmation status: the list was checked against the 2020 ZCTA-to-county file on 2026-10-05.
All 12 ZCTAs that touch Nevada County in 2020 are classified above or in the excluded list.

## 3. Boundary vintages

| Input | Boundary vintage | Note |
|-------|------------------|------|
| ACS 5-year 2007-2011 to 2015-2019 | 2010 ZCTA | Crosswalk to 2020 ZCTA before joining (section 4.2) |
| ACS 5-year 2016-2020 onward | 2020 ZCTA | The 2016-2020 release is the first on 2020 ZCTAs |
| CDI ZIP files (S2, S3, S6) | USPS ZIP as of each report year | ZIP-to-county assignment by USPS City State Product (S2) |
| FAIR Plan ZIP files (S9, S10) | USPS ZIP as of the report date | A split ZIP is assigned to one county (S9 note 2) |
| Zillow ZIP ZHVI (S11) | Zillow ZIP regions, current | History is recomputed monthly on current regions |
| County series (S4, S5, S8, S16, S17) | County FIPS 06057 | Treated as a fixed boundary over the study period |
| Relationship files (S14) | 2010 and 2020 | Not annual |

The 2010/2020 split follows `docs/sources.md` (S13). Confirm the first 2020-ZCTA release
against the ACS geography notes when the first ACS file is ingested, and record it here.

## 4. Crosswalks

All crosswalk tables live in `data/crosswalk/` with a `manifest.json` written by
`ingest/manifest.py` (URL, retrieval time, SHA-256, size, license note).

### 4.1 USPS ZIP to 2020 ZCTA (`xw_zip_zcta`)

1. **Identity mapping** (default). A ZIP maps to the 2020 ZCTA with the same code when that ZCTA
   exists. Weight 1.0. `zcta_method = identity`. This is `map_zip_to_zcta(df, zctas=...)` in
   `ingest/keys.py`. It is an approximation: ZIP delivery areas and ZCTA polygons do not match
   exactly.
2. **HUD crosswalk** (when S15 is available). HUD publishes ZIP to tract, county and CBSA, not
   ZIP to ZCTA. Chain HUD ZIP-to-tract (`RES_RATIO`, the share of the ZIP's residential addresses
   in the tract) with the Census 2020 tract-to-ZCTA relationship file. The weight is the
   residential share of the ZIP in each ZCTA. `zcta_method = crosswalk`. This is
   `map_zip_to_zcta(df, crosswalk=...)` with columns `zip, zcta, weight`.

### 4.2 2010 ZCTA to 2020 ZCTA (`xw_zcta10_zcta20`)

File: `tab20_zcta510_zcta520_natl.txt` (S14). Weight column: `AREALAND_PART /
AREALAND_ZCTA5_10`, the share of the 2010 ZCTA land area in each 2020 ZCTA. The file has no
population or housing columns, so land area is the only weight available. Rows mapped this way
get `lineage = crosswalked`.

### 4.3 ZCTA to county (`xw_zcta_county`)

Files: `tab20_zcta520_county20_natl.txt` (2020; weight `AREALAND_PART / AREALAND_ZCTA5_20`) and
`zcta_county_rel_10.txt` (2010; weights `ZPOPPCT` and `ZHUPCT`, the percent of the ZCTA
population and housing units in the county). Used only to set `geo_tier` and to attach the
county ID for county-level controls. It is never used to move values from a county down to a
ZCTA.

### 4.4 Many-to-many joins

A ZIP can map to several ZCTAs, and a ZCTA can receive several ZIPs.

- The join repeats the source row once per target and carries the weight in `xw_weight`. The
  join does not apply the weight. `ingest/keys.py` enforces this: values pass through unweighted.
- **Additive counts** (policies, earned exposure, housing units, claims, premium dollars) are
  allocated as `value * xw_weight` and summed per target ZCTA.
- **Non-additive values** (medians, averages, rates, indexes, ZHVI) are never split by weight.
  The ZCTA gets the value of the source ZIP with the largest weight, with `lineage = crosswalked`.
  A weighted mean of averages is allowed only when the denominator (for example earned exposure)
  is in the same row, and the result is `lineage = derived`.
- For each source ZIP the weights must sum to 1 within 0.01. If they do not, the rows get
  `xw_incomplete = 1` and the residual share is listed in the unmatched report.
- Duplicate `(zip, zcta)` pairs in a crosswalk raise `DuplicateKeyError`.

### 4.5 County figures are never ZIP observations

County figures are never allocated to ZIPs or ZCTAs as observed values. County-only series
(S4, S5, S8, and the FRED and population series in `fetch_data.py`) enter the panel only as
county-level controls in columns with a `county_` prefix, repeated on every ZCTA row of that
county with `lineage = county_control`. They do not fill a missing ZCTA value, and they do not
count as ZCTA coverage. If a later issue needs a modeled small-area value from a county figure,
it goes in a separate column with `lineage = modeled` and is shown as modeled in every output.

## 5. Unmatched records

Every mapping step returns two tables: matched rows and unmatched rows (`map_zip_to_zcta`
returns `(matched, unmatched)`). Nothing is dropped silently.

- Unmatched rows are written to `data/interim/unmatched/<table>.csv` with the original columns.
- Each ingest run prints a count of unmatched rows and the sum of each additive column in them,
  so the share of the total that is lost is visible.
- Expected unmatched ZIPs in Nevada County: 95712, 95924 (target), and 95724, 96160, 96162
  (excluded) until S15 is available. CDI ZIPs with a blank county or a placeholder code such as
  `90000` (S3) are unmatched by design.
- A ZIP that is unmatched in one year and matched in another is a boundary or reporting change.
  Flag it in `data_dictionary.csv` lineage notes; do not interpolate across it.

## 6. Canonical key

Every panel table is keyed by **`(zcta, year, policy_form)`**, unique per row.

| Column | Type | Rule |
|--------|------|------|
| `zcta` | string, 5 digits | 2020 ZCTA. Zero-padded. Census prefixes (`860Z200US`, `ZCTA5 `) stripped. `normalize_zcta`. |
| `year` | integer | Calendar year (section 7 of `docs/estimands.md`). ACS rows use the end year of the 5-year window. FAIR Plan rows use the fiscal year end (30 Sep). |
| `policy_form` | string | One of `HO, RT, CO, MH, DO, DT` (CDI SB 824 codes, S2), `ALL` for tables without a policy form (housing, ACS, wildfire), or an insurance product name for `insurance_zip_year`: `admitted_homeowners, dwelling_fire, fair_plan, supplemental`. Codes are upper case, product names lower case. `FP` is rejected until CDI defines it. |

- Inputs keep their source identifier too (`zip_source` for ZIP inputs).
- `canonical_keys()` in `ingest/keys.py` normalizes the three columns and raises
  `DuplicateKeyError` if two rows share a key after normalization.
- ZIP inputs are normalized with `normalize_zip`: integers, floats from spreadsheets
  (`95945.0`), ZIP+4 (`95945-1234`) and short codes (`2134` to `02134`) are accepted; anything
  else raises `ValueError`.

## 7. Lineage and missingness

Each value column has a lineage value and a missingness flag column (named in
`docs/data_dictionary.csv`).

Lineage values:

- `observed`: published by the source at this geography and year.
- `crosswalked`: published at another ZIP/ZCTA vintage and mapped with a recorded weight.
- `derived`: computed from observed or crosswalked columns in the same row (ratios, real dollars).
- `county_control`: a county figure repeated on ZCTA rows as a control (section 4.5).
- `modeled`: estimated, not published. Never mixed into an observed column.
- `key`: a key column (section 6). Never missing.
- `observed or crosswalked`: ACS columns, which are `observed` for 2020-ZCTA vintages and
  `crosswalked` for 2010-ZCTA vintages. The row-level value is set from `zcta_vintage`.

A missing-flag of `none` in the dictionary means the column cannot be missing.

Missingness flag values (integer column, one per value column or group):

- `0` present.
- `1` not published (absent from the source for this unit and year, for example a ZIP missing
  from S2 or a Zillow ZIP with too few sales).
- `2` suppressed or annotated by the source (ACS annotation values, S6 cells under 5 structures).
- `3` outside the source's coverage years (for example ZIP premium before 2018, U1).
- `4` unmatched in the crosswalk (section 5).
- `5` excluded by a documented rule (for example `FP` rows, or the year after a series break
  when no overlap adjustment exists).

Missing values stay missing. They are not filled with zero, carried forward or interpolated.

## 8. Restricted files

FAIR Plan files (S9, S10) have no reuse grant. Raw copies go only in `data/raw/restricted/`,
which is in `.gitignore`. Derived values from them are not committed or published until written
permission exists (`docs/sources.md`, "Reuse rights").

## Change log

- Version 1 (issue #2): initial target ZIPs, crosswalks and key.
- Version 2 (issue #3): `policy_form` also accepts the four insurance product names used by
  `insurance_zip_year`.
