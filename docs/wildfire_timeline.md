# Wildfire exposure and disruption timeline

Issue #5. This file defines the wildfire exposure table, the event register, the event windows
and the exclusion rules. It also states what the data can and cannot separate. Source IDs
(S1-S17, U1-U4) refer to `docs/sources.md` (issue #1, PR #15). Geography rules are in
`docs/schema.md` (issue #2).

Outputs:

- `data/wildfire_zip.csv`: one row per 2020 ZCTA in the region (section 2).
- `data/event_register.csv`: one row per event (section 4).
- `data/interim/unmatched/wildfire_zip.csv`: S2 ZIP-year rows with no region ZCTA.

Rebuild from the snapshots in `data/raw/wildfire/` with `uv run python -m ingest.wildfire build`.
`uv run python -m ingest.wildfire fetch` downloads new snapshots and rewrites
`data/raw/wildfire/manifest.json`. The fetch takes about 30 minutes because it sends one TIGERweb
query per damaged structure and two per ZCTA. `model.py` does not read these files.

## 1. Four different things

The design keeps these four apart. A row in one group is never used as a measure of another.

| Concept | What it measures | Measure in this repo | Source |
|---------|------------------|----------------------|--------|
| Physical risk | Expected hazard before any loss | `cdi_avg_fire_risk_<year>`, `cdi_high_extreme_share_<year>` | S2, Fire Risk Scores slice |
| Realized damage | Structures destroyed or damaged by a fire | `burned`, `structures_destroyed`, `structures_major_damage`, `fire` events | CAL FIRE DINS, CAL FIRE incident list |
| Insurer actions | Decisions of insurers to stop or limit business | `insurer_withdrawal` events | Insurer and CDI announcements |
| Regulatory changes | Rules that change what insurers may do | `nonrenewal_moratorium`, `rule_change`, `fair_plan_change` events | CDI bulletins, orders and releases |

A risk score is not an insurance shock and not a fire. No result in this project may claim an
effect of insurance from a risk score alone.

## 2. `data/wildfire_zip.csv`

### 2.1 Rows

One row per 2020 ZCTA with land in Nevada, Placer, Sierra or Yuba County (S14 2020
ZCTA-to-county file, `AREALAND_PART > 0`). `geo_tier` is `core` or `fringe` as in
`docs/schema.md` section 2, `excluded_east` for the eastern Nevada County ZCTAs that schema.md
excludes, and `region` for the other ZCTAs. The `region` rows are there for neighbor and donor
checks. They are not target ZCTAs. `county_fips_main` is the county with the largest land share.
`hu2020` is the 2020 Census housing-unit count (TIGERweb `HU100`).

### 2.2 Risk measures

Each risk measure has four label columns: `_source`, `_vintage`, `_aggregation_method` and
`_pretreatment` (1 for a pre-treatment vintage).

| Column | Vintage | Pre-treatment | Aggregation |
|--------|---------|---------------|-------------|
| `cdi_avg_fire_risk_2018` | calendar year 2018 | 1 | Mean of `Avg Fire Risk` (0-4) over forms HO RT CO MH DO DT, weighted by the scored policy count `N+L+M+H+E` of each form |
| `cdi_avg_fire_risk_2023` | calendar year 2023 | 0 | Same |
| `cdi_high_extreme_share_2018` | calendar year 2018 | 1 | `(H+E) / (N+L+M+H+E)` summed over the same forms |
| `cdi_high_extreme_share_2023` | calendar year 2023 | 0 | Same |

- USPS ZIP to 2020 ZCTA by identity (`map_zip_to_zcta(..., zctas=...)`). 95712 and 95924 have no
  ZCTA and go to the unmatched file.
- `FP` rows are dropped (`docs/schema.md` section 6). Rows with a blank score or no scored
  policies are dropped before weighting. `cdi_risk_missing = 1` when either vintage is blank.
- 2018 is the earliest S2 year. It is labeled pre-treatment because it is before the first SB 824
  moratorium (October 2019), the 2019 nonrenewal wave and every insurer pause in the register. It
  is **not** before the October 2017 Lobo and McCourtney fires, which burned in 95975, 95959 and
  95949.
- The S2 score is the insurers' own vendor score, reported only by insurers that use one. A
  change between 2018 and 2023 can be a change in hazard, in the vendor model, or in which
  insurers report. Do not read the 2018-2023 difference as a hazard change.

### 2.3 Risk maps not joined

| Map | Status | Reason |
|-----|--------|--------|
| CAL FIRE Fire Hazard Severity Zones, 2007-2011 maps (SRA 2007, LRA recommendations 2008-2011) | not joined | Polygons only. A ZCTA share needs a polygon overlay, which needs a geospatial dependency. Issue #5 does not allow one. No pre-aggregated ZIP or ZCTA table was found. |
| CAL FIRE Fire Hazard Severity Zones, 2023-2025 maps | not joined | Same. The new maps change the class of many parcels, so both vintages are needed. |
| USFS Wildfire Risk to Communities | not joined | `wildfirerisk.org/download/` returned HTTP 403 to a scripted GET on 2026-10-05. Its tables are by community, county and tract, not ZIP. |

Until one of these is joined, the only physical-risk measure is the S2 score. Because the FHSZ
vintage changes the exposure class of the same ZCTA, any later join must keep both vintages as
separate columns with their own labels.

### 2.4 Burned and spillover flags

- **Burned**: `burned = 1` when at least one CAL FIRE DINS record in the ZCTA has damage
  `Destroyed (>50%)` or `Major (25-50%)`. `burned_event_ids` lists the fire events;
  `structures_destroyed`, `structures_major_damage` and `first_burn_date` give size and timing.
  The ZCTA is found from each record's coordinates with a TIGERweb point query on the 2020 ZCTA
  layer, not from the address ZIP. Records without coordinates are counted in the fire event's
  notes and not assigned.
- **Spillover candidate**: `spillover_candidate = 1` when the ZCTA shares a boundary with a ZCTA
  burned by a fire that did not also burn it (TIGERweb `esriSpatialRelTouches` on the 2020 ZCTA
  polygons). `spillover_event_ids` lists those fires. This flag marks possible insurance and
  demand spillovers. It is not a measure of exposure.
- `moratorium_event_ids` lists the SB 824 moratoria whose bulletin lists the ZCTA's ZIP.

DINS limits: DINS covers incidents inspected by CAL FIRE damage teams, from 2013. Small or
federal-lead fires can be missing. A fire that burned land but no structure is not flagged.
Without fire perimeters (a polygon overlay), burned area and distance to a perimeter are not
measured.

Target ZCTAs on the build of 2026-10-05:

| ZCTA | Tier | Avg risk 2018 | High+extreme 2018 | Burned by | Destroyed | Spillover from | Moratoria |
|------|------|---------------|-------------------|-----------|-----------|----------------|-----------|
| 95602 | fringe | 1.96 | 0.42 | none | 0 | 2017 McCourtney, 2021 River, 2022 Still | 2021-08-05 |
| 95945 | core | 2.15 | 0.46 | 2021 River | 61 | 2017 Lobo, 2017 McCourtney, 2017 Pleasant, 2020 Jones, 2022 Still | 2020-08-18, 2021-08-05 |
| 95946 | core | 2.14 | 0.46 | none | 0 | 2017 Lobo, McCourtney, Pleasant; 2020 Jones, Willow; 2021 River; 2022 Rices, Still, Winding | 2020-08-18, 2021-08-05 |
| 95949 | core | 2.52 | 0.67 | 2017 McCourtney, 2021 River, 2022 Still | 33 | 2017 Lobo, 2017 Spenceville, 2021 Intanko | 2021-08-05 |
| 95959 | core | 2.71 | 0.72 | 2017 Pleasant, 2017 Lobo, 2020 Jones | 26 | 2021 River, 2022 Rices | 2020-08-18, 2021-08-05 |
| 95960 | fringe | 2.84 | 0.80 | 2022 Rices | 4 | 2017 Lobo, 2017 Pleasant, 2020 Jones, 2020 Willow, 2022 Winding | 2020-08-18 |
| 95975 | core | 2.85 | 0.82 | 2017 Lobo | 40 | 2017 McCourtney, 2017 Pleasant, 2020 Jones, 2021 River, 2022 Still | 2020-08-18, 2021-08-05 |
| 95977 | fringe | 2.16 | 0.46 | none | 0 | 2017 Cascade, McCourtney, Spenceville; 2020 Willow; 2021 Intanko, River; 2022 Still, Winding | none |
| 95986 | core | 3.21 | 0.91 | none | 0 | 2017 Lobo, 2017 Pleasant, 2020 Jones | 2020-08-18, 2021-08-05 |

Five of the nine target ZCTAs are burned and all nine are spillover candidates. The spillover
flag therefore cannot split the target into treated and untreated groups; it matters for donors.

## 3. Event register: `data/event_register.csv`

Columns: `event_id, event_type, date_start, date_end, date_uncertainty, geography,
zctas_affected, source_url, notes`.

- `event_type` is one of `fire`, `insurer_withdrawal`, `nonrenewal_moratorium`, `rule_change`,
  `fair_plan_change`.
- Dates are ISO `YYYY-MM-DD`. `date_end` is blank when the event has no end or the end is not
  published.
- `date_uncertainty` is the precision of `date_start`: `day`, `week`, `month`, `quarter` or
  `year`. Other timing problems are written in `notes`.
- `zctas_affected` holds region ZCTAs only, separated by `;`. For fires it is the ZCTAs with
  damaged structures. For moratoria it is the bulletin ZIPs that are region ZCTAs; other listed
  ZIPs (PO-box ZIPs such as 95712 and 95924, and ZIPs outside the four counties) are named in
  `notes`. Statewide events have a blank `zctas_affected` and `geography = California statewide`.
- Regional `fire` rows are generated from DINS and dated from the CAL FIRE incident list
  (converted to Pacific time). The other rows are in `data/raw/wildfire/events_curated.csv`, each
  with its source.

What the register holds:

- Fires: every fire in the four counties with a destroyed or major-damage DINS record, plus nine
  statewide fires for context (Tubbs, Camp, Woolsey, Kincade, Dixie, Caldor, Park, Palisades,
  Eaton).
- Every SB 824 moratorium declaration from October 2019 to January 2025 listed on the CDI
  moratorium page, with its bulletin.
- Insurer actions: State Farm new-business stop (2023-05-27) and nonrenewals (announced
  2024-03-20), Allstate pause and Farmers cap (dates not verified, see section 3.1).
- Rules: SB 824, the 2019 voluntary statewide request, the Sustainable Insurance Strategy
  (2023-09-21), the rate review bulletin (2024-08-09), the catastrophe model regulation
  (2024-12-13) and the reinsurance regulation (2024-12-30).
- FAIR Plan: the 2019 and 2021 coverage orders and the 2025 assessment.

### 3.1 Timing uncertainty

- **Moratoria.** Protection starts on the Governor's declaration date, but the ZIP list is
  published later in a CDI bulletin: 46 days later for the River Fire (2021-08-05 to 2021-09-20),
  and about 8 weeks for the 2019 fires. Insurers and buyers learn the list on the bulletin date.
  Both dates are in each row (start in `date_start`, bulletin in `notes`).
- **Insurer actions.** An announcement and the date a policyholder is affected differ. State Farm
  announced nonrenewals on 2024-03-20; notices began 2024-07-03 and rolled for a year. The
  register uses the announcement as `date_start` and gives the effective dates in `notes`.
- **Unverified dates.** The Allstate pause (late 2022) and Farmers cap (July 2023) are dated from
  press reports, not from a primary document. They have `date_uncertainty = quarter`, and the
  cited CDI release confirms only that both insurers later announced plans to resume. Use them
  only in sensitivity windows.
- **Fire end dates.** `date_end` for fires is the CAL FIRE "extinguished" field. For the 2017
  Wind Complex it is 2018-02-09, months after containment. Use `date_start` for timing.
- **Rules.** An announcement (SIS, 2023-09-21) is not a rule. The catastrophe model rule took
  effect 2024-12-13, but it changes rates only through later rate filings, so its effect on
  premiums has no fixed date.

## 4. No single statewide shock date

Each event keeps its own dates. For the core ZCTAs the dated disruptions are:

| Date | Event | Core ZCTAs touched |
|------|-------|--------------------|
| 2017-08-30 | Pleasant Fire, 1 structure destroyed | 95959 |
| 2017-10-08 | Lobo Fire (Wind Complex), 48 destroyed | 95959, 95975 |
| 2017-10-09 | McCourtney Fire (Wind Complex), 13 destroyed | 95949 |
| 2019-01-01 | SB 824 in force | statewide |
| 2019-11-14 | FAIR Plan coverage order | statewide |
| 2019-12-05 | First moratorium bulletins (no target ZIP listed); voluntary statewide request | statewide |
| 2020-08-17 | Jones Fire, 17 destroyed | 95959 |
| 2020-08-18 | Moratorium declaration covering the Jones Fire list (ZIP list in Bulletin 2020-11, amended 2020-11-06) | 95945, 95946, 95959, 95975, 95986 |
| 2021-08-04 | River Fire, 142 destroyed (80 in core ZCTAs) | 95945, 95949 |
| 2021-08-05 | Moratorium declaration covering the River Fire list (Bulletin 2021-09-20) | all six core ZCTAs |
| 2022-08-27 | Still Fire, 1 destroyed | 95949 |
| 2023-05-27 | State Farm stops new business | statewide |
| 2023-09-21 | Sustainable Insurance Strategy announced | statewide |
| 2024-03-20 | State Farm nonrenewals announced (notices from 2024-07-03) | statewide |
| 2024-12-13 | Catastrophe model regulation in force | statewide |

A single "insurance shock year" for all ZCTAs would mix these events. The windows below use
them one at a time.

## 5. Event windows

Each window is used alone; results are reported for all windows that the data cover.

- **W1. Local staggered events.** Event time per ZCTA from its own register events. W1a uses
  `first_burn_date` (destruction). W1b uses the first moratorium `date_start` that lists the
  ZCTA. Pre-period: the years before the event year. ZCTAs without the event are comparisons
  only if they pass the exclusions below.
- **W2. Statewide nonrenewal wave.** Pre 2015-2018, post 2019-2023 (S3 coverage). The break is
  2019: SB 824 in force (2019-01-01), the first moratoria (declarations October 2019, bulletins
  December 2019) and the first FAIR Plan order (2019-11-14). The 2020 S3 series break
  (`docs/sources.md` S3) falls inside the post-period, so 2020 is reported with and without.
- **W3. Insurer retreat.** Pre 2019-2022, post from 2023-05-27 (State Farm new-business stop).
  Sensitivity: start 2022-10-01 (Allstate, unverified). ZIP-level insurance outcomes end in 2023
  (U2), so W3 has one post-year for S2 and S3 outcomes. Price outcomes (S11) cover 2023-2025.
- **W4. Regulatory reform.** Announcement 2023-09-21; rules in force 2024-12-13 and 2024-12-30.
  No ZIP-level insurance outcome exists after 2023 (U2), so W4 cannot be estimated yet. It is
  defined now so that it is fixed before outcomes are seen.

## 6. Exclusion rules

- **X1. Destruction years.** For any insurance-shock estimate, drop ZCTA-years from the year of
  `first_burn_date` through two years after it, for that ZCTA. Report the estimate with these
  years kept as a sensitivity check.
- **X2. Spillover.** A ZCTA with `spillover_candidate = 1` is not a comparison (donor) ZCTA. In
  the target it is reported separately.
- **X3. Moratorium years.** Insurer-initiated nonrenewals are suppressed by law between a
  moratorium's `date_start` and `date_end`. In those ZCTA-years, nonrenewal outcomes are not
  used as untreated observations. A moratorium is its own treatment, not a control period.
- **X4. Donors.** A donor ZCTA has `burned = 0`, `spillover_candidate = 0` and no
  `moratorium_event_ids` in the study period. This applies criteria 3 and 4 of
  `docs/estimands.md` section 5. Donors from outside the region need the same flags built for
  their area first.
- **X5. Tiers.** `fringe` results are reported with and without; `excluded_east` and `region`
  ZCTAs are never in the target.
- **X6. Unverified dates.** Events with `date_uncertainty` of `quarter` or `year` define
  sensitivity windows only, never the main window.

## 7. Can the design separate insurance shocks from risk repricing and destruction?

**Not for the target ZCTAs with the current data.** The reasons:

1. **Insurance protection and destruction come from the same fires.** Every core ZCTA was on the
   2021 River Fire moratorium list, and all but 95949 on the 2020 Jones Fire list. Each list
   follows a fire that destroyed structures in or next to the target. A local moratorium effect cannot be told apart
   from the effect of the local fire. X1 removes the direct destruction years, not the fire's
   effect on buyers.
2. **Insurer retreat and risk repricing happen at the same time.** The statewide pauses
   (2022-2023) and the 2024 catastrophe model rule are both repricing of wildfire risk. An
   outcome change after 2023 that grows with 2018 risk could be insurer retreat, buyers
   repricing risk, or the new rules. Separating them needs insurer-by-ZIP market shares (to
   measure exposure to a given insurer's exit), which are not published (see S2, S3).
3. **The risk score is not fixed.** The S2 score is the insurers' own vendor score. It moves with
   vendor models and with which insurers report. Only the 2018 vintage is usable as a
   pre-treatment measure, and only as a ranking.
4. **Destruction is measured, but only as structure counts.** DINS gives destroyed structures by
   ZCTA, so X1 can remove destroyed stock. Burned area and distance to perimeters are not
   measured (section 2.4).

What is possible: descriptive event-time series by window (W1-W3), with the exclusions. Partial
variation exists outside the target: some region ZCTAs were listed only because of fires
elsewhere (for example Yuba County ZIPs on the 2024 Thompson Fire list), which gives moratorium
variation not tied to local destruction. That variation does not reach the core ZCTAs.

Consequence for `docs/estimands.md`: E2 and E3 stay **not supported** as causal estimates. No
statement in any output may attribute a price or population change to insurance on the basis
of a risk score or a risk map alone.

## Change log

- Version 1 (issue #5): first exposure table, register, windows and exclusions.
