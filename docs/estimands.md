# Outcome estimands

Issue #2. This file defines what the insurance extension of the model is allowed to estimate,
and what it is not. Geography and keys are in `docs/schema.md`. Source IDs refer to
`docs/sources.md` (issue #1, PR #15).

## 1. Notation

For 2020 ZCTA `z` (or county `c` for the existing model) and calendar year `t`:

- `Y[z,t]`: log real home value (Zillow ZIP ZHVI, S11, mapped to ZCTA; real `BASE_YEAR` dollars).
  The outcome. Growth `dY` is the annual log change.
- `I[z,t]`: insurance conditions. A vector, not one number. Candidate components, each with its
  own coverage years:
  - voluntary-market insurer-initiated nonrenewal rate (S3; 2015-2019 split, total only from 2020);
  - FAIR Plan share of insured dwellings (S6, 2022 only, ZIP; S4, county, 2015-2023);
  - average earned premium per house-year, HO form (S2, 2018-2023, real dollars);
  - FAIR Plan policies in force (S9, FY2021-FY2025; restricted, see `docs/schema.md` section 8).
- `P[z,t]`: population growth. Observed annually only at county level (S17). At ZCTA level only
  as ACS 5-year estimates (section 6).
- `W[z,t]`: wildfire hazard and fire events (fire risk score mix from S2; fire perimeters, to be
  inventoried by a later issue).
- `X[z,t]`: other controls: real income, permits, mortgage rate (absorbed by year effects).

The existing `model.py` estimates the county-level association of `dY` with `P`. The insurance
work adds `I` to that picture. There are three different questions, and each needs a different
estimate.

## 2. The three quantities

### E1. Population association conditional on insurance

The coefficient on population growth in a regression of `dY` on `P`, with `I` (and `W`, `X`,
unit and year effects) as controls:

    E1 = d E[dY | P, I, W, X, unit, year] / d P

- This is what `model.py` reports today, plus insurance controls. It answers: among places and
  years with the same insurance conditions, how does price growth move with population growth?
- It is an **association**, not a causal effect of population. `P` responds to prices and to
  `I` itself (reverse causation and mediation, section 3).
- Conditioning on `I` blocks the path `I -> P -> Y` only in the sense that `I` is held fixed. If
  `I` is a mediator of something that also drives `P`, conditioning on it can open collider paths.
  Report E1 with and without `I` to show how much it moves.
- Supported with current data: yes, at county level for 2015-2023 (S4 counts, S16, S17). At ZCTA
  level only for years with ZIP insurance data (2015-2023 for S3, 2018-2023 for S2), and with ACS
  5-year population (section 6).

### E2. Total insurance effect, including migration

The effect on `Y` of a change in insurance conditions, through every path, including people
moving in or out because of insurance cost or availability:

    E2 = E[Y(t) | do(I = i1)] - E[Y(t) | do(I = i0)]

- Paths included: `I -> Y` (capitalization of higher premiums and lost coverage into price),
  `I -> P -> Y` (migration), `I -> sales volume / buyer pool -> Y`, `I -> new construction -> Y`.
- Do **not** control for `P`, permits or sales volume when estimating E2. They are mediators.
- Identification needs a source of variation in `I` that is not driven by local fire hazard or
  local price trends. Candidates: the 2019-2020 statewide nonrenewal wave in ZCTAs with similar
  pre-period hazard; CDI one-year mandatory nonrenewal moratoria (exogenous ZIP lists tied to
  declared fires elsewhere); insurer exits that hit areas by insurer market share. None is
  inventoried yet. Until one is, E2 is **not supported** and must not be reported as causal.
- Comparison design: Nevada County target ZCTAs against donor ZCTAs chosen by the exposure
  criteria in section 5, with pre-trend checks over 2001-2017 for prices and 2015-2018 for
  insurance counts.

### E3. Direct effect of insurance

The effect of insurance on `Y` holding population (migration) fixed:

    E3 = E[Y(t) | do(I = i1, P = p)] - E[Y(t) | do(I = i0, P = p)]

- This is a controlled direct effect. It isolates capitalization of insurance cost into price
  from the migration channel. `E2 - E3` is the part that runs through population, under the
  assumptions below.
- Needs, in addition to the E2 assumptions: no unmeasured confounding of `P -> Y`, and no
  `I`-caused confounder of `P -> Y` (for example a fire that changes both migration and prices).
  Fires are exactly such a variable. Control for `W` and treat the decomposition as a bound.
- `P` at ZCTA level is an ACS 5-year average (section 6), which smooths migration over five
  years and is measured with error. That biases a ZCTA-level E3 toward E2. Report E3 only at
  county level, or as a sensitivity range at ZCTA level.
- Supported with current data: **not supported** as a point estimate. A county-level bound is
  possible after E2 is identified.

## 3. Confounders and mediators

| Variable | Role | Why | Data |
|----------|------|-----|------|
| Wildfire hazard (`W`, risk score mix) | confounder | Drives insurer pricing and nonrenewals, and buyer demand | S2 Fire Risk Scores slice, 2018-2023; S8 county, one vintage |
| Recent fire events nearby | confounder (and possible `I`-preceding shock) | Raise premiums, trigger moratoria, cut demand, destroy housing stock | Fire perimeters: not yet inventoried |
| Regional demand shocks (remote work 2020-2022, Bay Area outflow) | confounder | Raise prices and in-migration in foothill markets at the same time as the nonrenewal wave | Year effects absorb statewide parts only |
| Mortgage rates | confounder (time) | Common to all units | Year fixed effects |
| Local income | confounder | Affects prices and insurance take-up | S16 county income (county control only) |
| Housing stock age, construction type, defensible space | confounder | Drives risk score and price | Not available at ZCTA level |
| Population growth (`P`) | mediator of E2; held fixed in E3 | Migration responds to insurance cost | S17 county; ACS 5-year ZCTA |
| New construction (permits) | mediator | Insurance cost affects building | S16 county only |
| Sales volume, days on market | mediator | Uninsurable homes sell slowly or for cash | Not inventoried |
| FAIR Plan take-up | part of `I`, also a response | Rises when voluntary market exits | S4, S6, S9 |
| Coverage A (insured value) | collider/outcome-adjacent | Moves with rebuild cost and with price | S2; definition changes in 2021 |

## 4. Quantities not supportable with the inventoried data

- Any ZIP or ZCTA insurance premium effect before 2018 (U1). The only earlier price is the
  statewide average (S1).
- Any ZIP insurance effect after 2023 from CDI data (U2). 2024-2025 have only FAIR Plan
  snapshots, which are restricted and cover one segment.
- Annual ZCTA population or migration. ACS gives only overlapping 5-year averages; no annual
  ZCTA population series exists.
- Effects for the PO-box ZIPs 95712 and 95924 until the HUD crosswalk is available (they have
  no ZCTA).
- Surplus lines effects below state level (U4).
- Separate effects of insurer- versus insured-initiated nonrenewals after 2019 (S3 dropped the
  split in the 2020-2023 vintage).
- Splicing S3 levels across the 2020 definition break without an overlap adjustment.
- Household-level effects (who moved, who dropped coverage). All inputs are aggregates.
- Any ZCTA value derived by allocating a county figure, reported as observed.

## 5. Donor markets: exposure criteria, not a fixed list

Donor (comparison) ZCTAs are chosen by measured exposure, not by region. Many Bay Area ZCTAs
were exposed: the Oakland and Berkeley hills, the Santa Cruz Mountains, Marin, Napa and Sonoma
had nonrenewals, moratoria and fires in the study period. A Bay Area location says nothing about
exposure, so "Bay Area" is never used as a control group.

A California ZCTA is a candidate donor only if all of these hold, measured at ZCTA level:

1. **Insurance exposure low**: insurer-initiated nonrenewal rate in 2019-2023 (S3) below the
   statewide median, and below the target ZCTAs' pre-2019 level; FAIR Plan share in 2022 (S6)
   below a threshold set before looking at outcomes (record the value here when set).
2. **Hazard low**: share of earned exposure in high or extreme fire-risk categories (S2, Fire
   Risk Scores slice) below a pre-set threshold in every year 2018-2023.
3. **No moratorium**: not on any CDI mandatory nonrenewal moratorium list in the study period
   (lists to be inventoried).
4. **No large fire**: no fire perimeter within a pre-set distance in the study period (perimeters
   to be inventoried).
5. **Comparable market**: data present in S11 for 2001-2025, and a pre-period (2001-2017) real
   price trend that passes the pre-trend test against the target.
6. **Not a spillover destination**: excluded if net in-migration from Nevada County or from
   fire-affected counties is large (to be measured; ACS county-to-county flows are a candidate).

Thresholds are fixed and written in this file before any outcome comparison is run. The donor
list is an output of these rules, stored with its rule version, not an input.

## 6. Time alignment, deflator and ACS windows

**Calendar year.** The panel year is the calendar year. Monthly series (Zillow ZHVI, CPI-U) are
converted to annual values by the calendar-year mean, kept only when the year has at least 10
months of data. This is `annual_mean` in `fetch_data.py`; the ingest for ZIP ZHVI uses the same
rule. With that rule, 2025 CPI is an 11-month mean (October 2025 was not published) and 2026 is
excluded until it has 10 months.

**Other timing conventions.**

- Weekly mortgage rates: calendar-year mean (same 10-month rule on months with data).
- CDI S2 and S3: calendar-year flows and exposures for the stated year. S2 coverage amounts
  switch from end-of-year to start-of-year in 2021.
- FAIR Plan S9: snapshots at 30 September. `year` is the fiscal year that ends on that date. They
  are point-in-time stocks, not annual flows; never average them with calendar-year flows.
- County FHFA HPI and population (S16, S17): annual values as published (population at July 1).
  The existing model takes the last value of each FRED county series per year.
- Insurance affects prices with a lag. Lags are a model choice for later issues; the panel stores
  contemporaneous values only.

**Deflator and base year.** All money values are converted to real dollars of `BASE_YEAR` with
CPI-U (`CPIAUCSL`, S16, all items, seasonally adjusted, annual mean as above):

    real[t] = nominal[t] * CPI[BASE_YEAR] / CPI[t]

`BASE_YEAR = 2025`, as set in `model.py`. When `model.py` changes `BASE_YEAR`, every real
column is recomputed; the nominal column is always kept with `lineage = observed` and the real
column has `lineage = derived`.

**ACS 5-year windows.** ACS ZCTA estimates (S13) exist only as 5-year averages.

- The panel stores an ACS value at `year` = the last year of its window, with the window start
  in `acs_window_start`. It describes the average over five years, not that year.
- Adjacent vintages share four of five years. The difference between them is mostly the change
  between the dropped year and the added year, divided by five. Do not read consecutive
  differences as annual changes, and do not use them as annual growth in a regression.
- Valid comparisons use non-overlapping windows (for example 2010-2014, 2015-2019, 2020-2024).
- Margins of error (`_M` columns) are stored and carried into any derived value. Small ZCTAs
  (95986, 96111) have large margins.
- Vintages through 2015-2019 use 2010 ZCTAs and need the 2010-to-2020 crosswalk
  (`docs/schema.md` section 4.2).
- ACS dollar values are in dollars of the window's last year. Deflate them with CPI-U of that
  year to `BASE_YEAR` dollars.
