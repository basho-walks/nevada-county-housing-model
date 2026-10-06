"""Join the insurance, housing and wildfire tables into data/panel_zip_year.csv keyed on (zcta, year).

Reads only data/ (no network). Every universe ZCTA-year is kept; gaps get a missing code and reason, never a fill.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from ingest.housing import BASE_YEAR
from ingest.insurance import TARGET_ZCTAS
from ingest.keys import assert_unique_keys, canonical_keys
from ingest.manifest import sha256
from ingest.wildfire import parse_register

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
PANEL = DATA / "panel_zip_year.csv"
MANIFEST = DATA / "panel_manifest.json"
AUDIT = DOCS / "panel_audit.md"
DICTIONARY = DOCS / "data_dictionary.csv"

INPUTS = {
    "insurance": ("data/insurance_zip_year.csv", "uv run python -m ingest.insurance", "data/raw/insurance"),
    "housing": ("data/housing_zip_year.csv", "uv run python -m ingest.housing", "data/raw/housing"),
    "wildfire": ("data/wildfire_zip.csv", "uv run python -m ingest.wildfire build", "data/raw/wildfire"),
    "events": ("data/event_register.csv", "uv run python -m ingest.wildfire build", "data/raw/wildfire"),
    "county_panel": ("data/ca_county_panel.csv", "uv run python fetch_data.py", None),
    "county_names": ("data/ca_county_names.csv", "uv run python fetch_data.py", None),
    "national": ("data/national_annual.csv", "uv run python fetch_data.py", None),
    "zcta_county_share": ("data/raw/wildfire/zcta_county_region.csv", "uv run python -m ingest.wildfire fetch", "data/raw/wildfire"),
    "unmatched_insurance": ("data/interim/unmatched/insurance_zip_year.csv", "uv run python -m ingest.insurance", None),
    "unmatched_housing": ("data/interim/unmatched/housing_zip_year.csv", "uv run python -m ingest.housing", None),
    "unmatched_wildfire": ("data/interim/unmatched/wildfire_zip.csv", "uv run python -m ingest.wildfire build", None),
}

SCHEMA_VERSION = 1  # docs/schema.md
YEAR_MIN = 2000
NEVADA_FIPS = "06057"
REAL = "_real"
FORMS = ("admitted_homeowners", "dwelling_fire", "fair_plan")

# Columns kept per policy form. Constant metadata (exposure_unit, premium_basis, nonrenewal_scope,
# denominator_source) and identity-mapping columns go to the dictionary, not the panel.
FORM_COLUMNS = {
    "admitted_homeowners": [
        "sources", "exposures", "earned_premium", "avg_premium_per_exposure", "avg_coverage_a", "avg_coverage_c",
        "coverage_basis", "premium_per_1000_coverage_a", "form_share_of_admitted", "policies_new", "policies_renewed",
        "nonrenewed_total", "nonrenewed_insurer", "nonrenewed_insured", "nonrenewal_rate", "s3_vintage",
        "series_break", "coverage_def_change", "mix_shift", "combined_premium_unavailable",
    ],
    "dwelling_fire": [
        "sources", "exposures", "earned_premium", "avg_premium_per_exposure", "avg_coverage_a", "avg_coverage_c",
        "coverage_basis", "premium_per_1000_coverage_a", "form_share_of_admitted", "owner_occupied_share",
        "coverage_def_change", "mix_shift",
    ],
    "fair_plan": ["sources", "voluntary_dwelling_units", "fair_plan_dwelling_units", "fair_plan_share", "suppressed"],
}
DOLLAR_COLUMNS = ("earned_premium", "avg_premium_per_exposure", "avg_coverage_a", "avg_coverage_c")
FLAG_COLUMNS = ("series_break", "coverage_def_change", "mix_shift", "combined_premium_unavailable", "suppressed")

# block -> (form, indicator column, first year, last year, source). Coverage years from docs/sources.md S2, S3, S6.
INSURANCE_BLOCKS = {
    "admitted_homeowners__premium": ("admitted_homeowners", "avg_premium_per_exposure", 2018, 2023, "S2"),
    "admitted_homeowners__counts": ("admitted_homeowners", "nonrenewal_rate", 2015, 2023, "S3"),
    "dwelling_fire__premium": ("dwelling_fire", "avg_premium_per_exposure", 2018, 2023, "S2"),
    "fair_plan__share": ("fair_plan", "fair_plan_share", 2022, 2022, "S6"),
}

HOUSING_COLUMNS = ["zhvi_months", "zhvi", f"zhvi{REAL}"]
ACS_VALUES = [
    "population", "households", "median_hh_income", f"median_hh_income{REAL}", "housing_units", "vacant_units",
    "vacancy_rate",
]
ACS_COLUMNS = ["acs_window", "acs_window_start", "zcta_vintage"] + [
    f"acs5_{c}{s}" for c in ACS_VALUES for s in ("", "_moe")
]
ACS_INGESTED = (2021, 2024)  # window end years in housing_zip_year (docs/controls.md 1.2)

S2_RISK_VINTAGES = (2018, 2023)
DINS_START = 2013  # DINS inspections start in 2013 (docs/wildfire_timeline.md 2.4)

# Year-over-year thresholds: a change above them is not removed, only flagged.
JUMP_THRESHOLDS = {
    f"zhvi{REAL}": ("abs_log", 0.25),
    f"admitted_homeowners__avg_premium_per_exposure{REAL}": ("abs_log", 0.40),
    f"dwelling_fire__avg_premium_per_exposure{REAL}": ("abs_log", 0.40),
    "admitted_homeowners__nonrenewal_rate": ("abs_diff", 0.10),
    "county_population": ("abs_log", 0.10),
}

COMPLETE_CASE_COLUMNS = [
    f"zhvi{REAL}",
    f"admitted_homeowners__avg_premium_per_exposure{REAL}",
    "admitted_homeowners__nonrenewal_rate",
    "cdi_avg_fire_risk_latest",
]

MISSING_TEXT = {
    0: "present",
    1: "not published",
    2: "suppressed or annotated by source",
    3: "outside source coverage years",
    4: "unmatched in crosswalk",
    5: "excluded by documented rule",
}


# ---------------------------------------------------------------- inputs


def load_inputs() -> dict[str, pd.DataFrame]:
    s = {"zcta": str, "zip": str, "zip_source": str, "fips": str, "county_fips": str, "county_fips_main": str}
    out = {}
    for name, (rel, _, _) in INPUTS.items():
        if name == "events":
            out[name] = pd.read_csv(ROOT / rel, dtype=str, keep_default_na=False)
        else:
            out[name] = pd.read_csv(ROOT / rel, dtype=s)
    return out


def _cpi_factor(national: pd.DataFrame) -> pd.Series:
    cpi = national.set_index("year")["cpi"]
    return cpi.loc[BASE_YEAR] / cpi


# ---------------------------------------------------------------- universe


def universe(inp: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """ZCTA x year frame: target and region ZCTAs from the wildfire table plus Bay Area spillover sources."""
    wf = inp["wildfire"][["zcta", "geo_tier", "county_fips_main", "hu2020"]].copy()
    targets = set(wf.loc[wf["geo_tier"].isin(["core", "fringe"]), "zcta"])
    if targets != set(TARGET_ZCTAS):
        raise ValueError(f"wildfire target ZCTAs {sorted(targets)} != insurance targets {sorted(TARGET_ZCTAS)}")
    wf["panel_role"] = wf["geo_tier"].map({"core": "target", "fringe": "target"}).fillna(wf["geo_tier"])
    wf = wf.rename(columns={"county_fips_main": "county_fips"})

    h = inp["housing"]
    names = inp["county_names"].assign(zillow_county=lambda d: d["name"] + " County")
    sp = h.loc[h["candidate_spillover_source"] == 1, ["zcta", "zillow_county"]].drop_duplicates()
    sp = sp[~sp["zcta"].isin(wf["zcta"])].merge(names[["zillow_county", "fips"]], on="zillow_county", how="left")
    if sp["zcta"].duplicated().any() or sp["fips"].isna().any():
        raise ValueError("spillover ZCTAs need exactly one known county")
    sp = pd.DataFrame({
        "zcta": sp["zcta"], "geo_tier": "outside_region", "county_fips": sp["fips"], "hu2020": np.nan,
        "panel_role": "spillover_source",
    })
    zctas = pd.concat([wf, sp], ignore_index=True).sort_values("zcta")
    years = pd.concat([inp["housing"]["year"], inp["insurance"]["year"]])
    grid = pd.MultiIndex.from_product([zctas["zcta"], range(YEAR_MIN, int(years.max()) + 1)], names=["zcta", "year"])
    out = grid.to_frame(index=False).merge(zctas, on="zcta", how="left")
    out.insert(2, "policy_form", "ALL")
    out["schema_version"] = SCHEMA_VERSION
    return out


# ---------------------------------------------------------------- blocks


def _missing(panel: pd.DataFrame, block: str, code: pd.Series, detail: pd.Series) -> None:
    code = code.astype(int)
    panel[f"{block}_missing"] = code
    text = code.map(MISSING_TEXT)
    panel[f"{block}_missing_reason"] = np.where(detail.fillna("") == "", text, text + ": " + detail.fillna(""))


def add_insurance(panel: pd.DataFrame, ins: pd.DataFrame, cpi: pd.Series) -> pd.DataFrame:
    for form in FORMS:
        g = ins.loc[ins["policy_form"] == form, ["zcta", "year", *FORM_COLUMNS[form]]].copy()
        for c in FLAG_COLUMNS:
            if c in g:
                g[c] = g[c].map({True: 1, False: 0, "True": 1, "False": 0}).astype("Int64")
        for c in DOLLAR_COLUMNS:
            if c in g:
                g[c + REAL] = g[c] * g["year"].map(cpi)
        g = g.rename(columns={c: f"{form}__{c}" for c in g.columns if c not in ("zcta", "year")})
        panel = panel.merge(g, on=["zcta", "year"], how="left")
    for block, (form, ind, y0, y1, src) in INSURANCE_BLOCKS.items():
        present = panel[f"{form}__{ind}"].notna()
        supp = panel[f"{form}__suppressed"].eq(1).fillna(False) if f"{form}__suppressed" in panel else False
        outside = ~panel["year"].between(y0, y1)
        not_target = ~panel["zcta"].isin(TARGET_ZCTAS)
        code = pd.Series(np.select([not_target, outside, present, supp], [5, 3, 0, 2], 1), index=panel.index)
        detail = pd.Series(np.select(
            [not_target, outside, present, supp],
            ["insurance ingest (#3) covers the 9 target ZCTAs only", f"{src} covers {y0}-{y1}", "",
             f"{src} omits ZIPs with fewer than 5 insured structures"],
            f"{src} has no row for this ZCTA-year",
        ), index=panel.index)
        _missing(panel, block, code, detail)
    return panel


def add_housing(panel: pd.DataFrame, h: pd.DataFrame) -> pd.DataFrame:
    h = h.rename(columns={c: f"acs5_{c}" for c in h.columns if c.removesuffix("_moe") in ACS_VALUES})
    cols = ["zcta", "year", *HOUSING_COLUMNS, "zhvi_missing", *ACS_COLUMNS, "acs_missing"]
    panel = panel.merge(h[cols].rename(columns={"zhvi_missing": "_zm", "acs_missing": "_am"}), on=["zcta", "year"], how="left")
    in_ca = panel["zcta"].str.startswith("9")  # every California ZCTA starts with 9; 89439 is in Nevada
    zm = panel["_zm"].where(panel["_zm"].notna(), np.where(in_ca, 1, 5)).astype(int)
    zd = np.select(
        [~in_ca, zm.eq(0), zm.eq(5), panel["_zm"].isna()],
        ["outside California; housing ingest (#4) covers California only", "",
         "fewer than 10 months of ZHVI in the year (mean_ge10m rule)", "no Zillow ZIP series for this ZCTA-year"],
        "Zillow value not published for this ZCTA-year",
    )
    _missing(panel, "zhvi", zm, pd.Series(zd, index=panel.index))
    y = panel["year"]
    fallback = np.select([~in_ca, (y < 2011) | (y > ACS_INGESTED[1]), y < ACS_INGESTED[0]], [5, 3, 5], 1)
    am = panel["_am"].where(panel["_am"].notna(), fallback).astype(int)
    ad = np.select(
        [~in_ca, am.eq(0), am.eq(2), am.eq(3), am.eq(5)],
        ["outside California; housing ingest (#4) covers California only", "",
         "at least one ACS estimate annotated (median income in most cases)",
         f"no ingested ACS 5-year window ends in this year (ingested: {ACS_INGESTED[0]}-{ACS_INGESTED[1]})",
         "window ends 2011-2020 and was not ingested (needs Census API key or 2010-ZCTA crosswalk)"],
        "ACS has no row for this ZCTA",
    )
    _missing(panel, "acs", am, pd.Series(ad, index=panel.index))
    return panel.drop(columns=["_zm", "_am"])


def _event_zctas(events: pd.DataFrame, kind: str) -> pd.DataFrame:
    e = events[events["event_type"] == kind].copy()
    e["zcta"] = e["zctas_affected"].fillna("").str.split(";")
    e = e.explode("zcta")
    return e[e["zcta"].fillna("") != ""]


def add_wildfire(panel: pd.DataFrame, wf: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Fixed 2018 risk vintage, latest-vintage-to-date risk, and event status known by the end of each year."""
    w = wf.set_index("zcta")
    for c in ("cdi_avg_fire_risk_2018", "cdi_high_extreme_share_2018"):
        panel[c] = panel["zcta"].map(w[c])
    y = panel["year"]
    in_region = panel["zcta"].isin(w.index)
    vint = pd.Series(np.nan, index=panel.index)
    for v in S2_RISK_VINTAGES:
        vint = vint.mask(y >= v, v)
    panel["cdi_risk_vintage_latest"] = vint.where(in_region).astype("Int64")
    for m in ("avg_fire_risk", "high_extreme_share"):
        val = pd.Series(np.nan, index=panel.index)
        for v in S2_RISK_VINTAGES:
            val = val.mask(vint == v, panel["zcta"].map(w[f"cdi_{m}_{v}"]))
        panel[f"cdi_{m}_latest"] = val
    present = panel["cdi_avg_fire_risk_latest"].notna()
    conds = [~in_region, y < S2_RISK_VINTAGES[0], present]
    code = pd.Series(np.select(conds, [5, 3, 0], 1), index=panel.index)
    detail = pd.Series(np.select(conds, [
        "outside the four-county wildfire region (#5)", f"before the first S2 risk vintage ({S2_RISK_VINTAGES[0]})", ""],
        "S2 risk score not published for this ZCTA and vintage"), index=panel.index)
    _missing(panel, "wildfire_risk", code, detail)

    ev = parse_register(events)
    fires = _event_zctas(ev, "fire")
    fires["fire_year"] = fires["date_start"].dt.year
    first = fires.groupby("zcta")["fire_year"].min()
    damage = fires.groupby(["zcta", "fire_year"]).size()
    known = in_region & (y >= DINS_START)
    panel["fire_damage_in_year"] = pd.Series(
        [int((z, yr) in damage.index) for z, yr in zip(panel["zcta"], y)], index=panel.index
    ).where(known).astype("Int64")
    fy = panel["zcta"].map(first)
    panel["burned_to_date"] = (fy <= y).astype(int).where(known).astype("Int64")
    panel["years_since_first_burn"] = (y - fy).where(known & (fy <= y)).astype("Int64")
    panel["x1_destruction_window"] = ((fy <= y) & (y <= fy + 2)).astype(int).where(known).astype("Int64")

    mor = _event_zctas(ev, "nonrenewal_moratorium")
    start_y = mor["date_start"].dt.year
    end_y = mor["date_end"].dt.year.fillna(9999)
    active, started = [], []
    for z, yr in zip(panel["zcta"], y):
        m = mor["zcta"].eq(z)
        active.append(int((m & (start_y <= yr) & (end_y >= yr)).any()))
        started.append(int((m & (start_y <= yr)).sum()))
    panel["moratorium_active_in_year"] = pd.Series(active, index=panel.index).where(known).astype("Int64")
    panel["moratoria_started_to_date"] = pd.Series(started, index=panel.index).where(known).astype("Int64")
    conds = [~in_region, y < DINS_START]
    code = pd.Series(np.select(conds, [5, 3], 0), index=panel.index)
    detail = pd.Series(np.select(conds, [
        "outside the four-county wildfire region (#5)", f"before DINS coverage ({DINS_START})"], ""), index=panel.index)
    _missing(panel, "wildfire_events", code, detail)
    return panel


def add_county(panel: pd.DataFrame, cp: pd.DataFrame, cpi: pd.Series) -> pd.DataFrame:
    c = cp.rename(columns={"fips": "county_fips"})[["county_fips", "year", "population", "income_pc", "permits"]].copy()
    c["county_income_pc" + REAL] = c["income_pc"] * c["year"].map(cpi)
    c = c.rename(columns={"population": "county_population", "permits": "county_permits"}).drop(columns="income_pc")
    panel = panel.merge(c, on=["county_fips", "year"], how="left")
    cols = ["county_population", "county_income_pc" + REAL, "county_permits"]
    gaps = panel[cols].isna()
    detail = gaps.apply(lambda r: ", ".join(k for k, v in r.items() if v), axis=1)
    detail = np.where(detail != "", "ca_county_panel.csv has no value for " + detail, "")
    _missing(panel, "controls", gaps.any(axis=1).astype(int), pd.Series(detail, index=panel.index))
    return panel


def add_quality_flags(panel: pd.DataFrame) -> pd.DataFrame:
    panel = panel.sort_values(["zcta", "year"]).reset_index(drop=True)
    prev_year = panel.groupby("zcta")["year"].shift()
    consecutive = prev_year.eq(panel["year"] - 1)
    any_jump = pd.Series(0, index=panel.index)
    for col, (kind, limit) in JUMP_THRESHOLDS.items():
        x = panel[col].astype(float)
        prev = panel.groupby("zcta")[col].shift().astype(float)
        if kind == "abs_log":
            change = (np.log(x.where(x > 0)) - np.log(prev.where(prev > 0))).abs()
        else:
            change = (x - prev).abs()
        flag = (change > limit).astype(int).where(change.notna() & consecutive).astype("Int64")
        panel[f"jump_flag__{col}"] = flag
        any_jump = any_jump | flag.fillna(0).astype(int)
    panel["jump_flag_any"] = any_jump.astype(int)
    panel["complete_case"] = panel[COMPLETE_CASE_COLUMNS].notna().all(axis=1).astype(int)
    return panel


def build(inp: dict[str, pd.DataFrame]) -> pd.DataFrame:
    cpi = _cpi_factor(inp["national"])
    panel = universe(inp)
    panel = add_insurance(panel, inp["insurance"], cpi)
    panel = add_housing(panel, inp["housing"])
    panel = add_wildfire(panel, inp["wildfire"], inp["events"])
    panel = add_county(panel, inp["county_panel"], cpi)
    panel = add_quality_flags(panel)
    assert_unique_keys(panel, ("zcta", "year"))
    canonical_keys(panel)
    order = [c["column"] for c in column_spec()]
    if set(order) != set(panel.columns):
        raise ValueError(f"column spec mismatch: {sorted(set(order) ^ set(panel.columns))}")
    return panel[order]


# ---------------------------------------------------------------- dictionary and timing


def _c(column, type_, unit, source, lineage, missing, timing, geography="ZCTA 2020"):
    return {"column": column, "type": type_, "unit": unit, "source": source, "geography": geography,
            "lineage": lineage, "missing_flag": missing, "timing": timing}


INSURANCE_META = {
    "sources": ("string", "source IDs separated by ;", "observed"),
    "exposures": ("float", "earned house-years (S2 All Companies)", "crosswalked"),
    "earned_premium": ("float", "USD nominal", "crosswalked"),
    "avg_premium_per_exposure": ("float", "USD nominal per earned house-year", "derived"),
    "avg_coverage_a": ("float", "USD nominal", "crosswalked"),
    "avg_coverage_c": ("float", "USD nominal", "crosswalked"),
    "coverage_basis": ("string", "end_of_year (2018-2020) or start_of_year (2021+)", "derived"),
    "premium_per_1000_coverage_a": ("float", "USD premium per USD 1000 of Coverage A (same-year dollars)", "derived"),
    "form_share_of_admitted": ("float", "share 0-1 of S2 admitted earned exposure", "derived"),
    "owner_occupied_share": ("float", "share 0-1 of dwelling-fire earned exposure (DO / (DO+DT))", "derived"),
    "policies_new": ("float", "policy count (S3 voluntary market all forms)", "crosswalked"),
    "policies_renewed": ("float", "policy count (S3 voluntary market all forms)", "crosswalked"),
    "nonrenewed_total": ("float", "policy count (S3 voluntary market all forms)", "crosswalked"),
    "nonrenewed_insurer": ("float", "policy count (S3 2015-2021 vintage only)", "crosswalked"),
    "nonrenewed_insured": ("float", "policy count (S3 2015-2021 vintage only)", "crosswalked"),
    "nonrenewal_rate": ("float", "share 0-1: nonrenewed_total / (policies_renewed + nonrenewed_total)", "derived"),
    "s3_vintage": ("string", "2015-2021 or 2020-2023", "observed"),
    "series_break": ("integer", "0 or 1 (1 in 2020, S3 definition break)", "derived"),
    "coverage_def_change": ("integer", "0 or 1 (1 in 2021, S2 coverage basis change)", "derived"),
    "mix_shift": ("integer", "0 or 1 (form or occupancy share moved more than the ingest threshold vs year-1)", "derived"),
    "combined_premium_unavailable": ("integer", "0 or 1", "derived"),
    "voluntary_dwelling_units": ("float", "dwelling units insured in the voluntary market", "crosswalked"),
    "fair_plan_dwelling_units": ("float", "dwelling units insured by the FAIR Plan", "crosswalked"),
    "fair_plan_share": ("float", "share 0-1 of insured dwelling units", "derived"),
    "suppressed": ("integer", "0 or 1 (S6 cell under 5 structures)", "derived"),
}
INSURANCE_SOURCE = {"admitted_homeowners": "S2 HO and S3", "dwelling_fire": "S2 DO and DT", "fair_plan": "S6 (CDI 2022)"}
ACS_META = {
    "population": ("persons (5-year average)", "B01003"),
    "households": ("households (5-year average)", "B11001"),
    "median_hh_income": ("USD of window end year", "B19013"),
    f"median_hh_income{REAL}": (f"USD of {BASE_YEAR}", "B19013 and CPI-U"),
    "housing_units": ("housing units (5-year average)", "B25001"),
    "vacant_units": ("housing units (5-year average)", "B25002"),
    "vacancy_rate": ("share 0-1", "B25002"),
}


COUNT_COLUMNS = frozenset({
    "policies_new", "policies_renewed", "nonrenewed_total", "nonrenewed_insurer", "nonrenewed_insured",
    "nonrenewal_rate", "s3_vintage", "series_break",
})


def _insurance_block(form: str, col: str) -> str:
    if form == "admitted_homeowners":
        return f"{form}__counts" if col in COUNT_COLUMNS else f"{form}__premium"
    return f"{form}__premium" if form == "dwelling_fire" else f"{form}__share"


def _missing_cols(block: str, source: str, geography: str = "ZCTA 2020") -> list[dict]:
    return [
        _c(f"{block}_missing", "integer", "0-5 code (docs/schema.md section 7)", source, "derived", "none", "same_year", geography),
        _c(f"{block}_missing_reason", "string", "code text and cause", source, "derived", "none", "same_year", geography),
    ]


def column_spec() -> list[dict]:
    spec = [
        _c("zcta", "string", "5-digit code", "ingest/keys.py", "key", "none", "key"),
        _c("year", "integer", "calendar year", "all", "key", "none", "key"),
        _c("policy_form", "string", "ALL (insurance forms are pivoted into column prefixes)", "build_panel.py", "key", "none", "key"),
        _c("geo_tier", "string", "core fringe region excluded_east or outside_region", "wildfire_zip and docs/schema.md section 2", "derived", "none", "static_geography"),
        _c("county_fips", "string", "5-digit FIPS", "wildfire_zip county_fips_main or Zillow CountyName", "derived", "none", "static_geography", "county"),
        _c("hu2020", "float", "housing units, 2020 Census (HU100)", "wildfire_zip (TIGERweb 2020)", "observed", "empty when panel_role is spillover_source", "fixed_vintage_2020"),
        _c("panel_role", "string", "target region excluded_east or spillover_source", "build_panel.py", "derived", "none", "static_geography"),
        _c("schema_version", "integer", "version number", "docs/schema.md", "derived", "none", "static_geography", "none"),
    ]
    for form in FORMS:
        for col in FORM_COLUMNS[form]:
            t, unit, lin = INSURANCE_META[col]
            missing = _insurance_block(form, col)
            src = f"insurance_zip_year ({INSURANCE_SOURCE[form]})"
            spec.append(_c(f"{form}__{col}", t, unit, src, lin, f"{missing}_missing", "same_year"))
            if col in DOLLAR_COLUMNS:
                spec.append(_c(f"{form}__{col}{REAL}", "float", unit.replace("USD nominal", f"USD of {BASE_YEAR}"),
                               src + " and CPI-U", "derived", f"{missing}_missing", "same_year"))
    for block, (form, _, _, _, s) in INSURANCE_BLOCKS.items():
        spec += _missing_cols(block, f"build_panel.py ({s} coverage)")
    spec += [
        _c("zhvi_months", "integer", "months with ZHVI in year", "housing_zip_year (S11)", "derived", "zhvi_missing", "same_year"),
        _c("zhvi", "float", "USD nominal (calendar-year mean of 10+ months)", "housing_zip_year (S11)", "crosswalked", "zhvi_missing", "same_year"),
        _c(f"zhvi{REAL}", "float", f"USD of {BASE_YEAR}", "housing_zip_year (S11 and CPI-U)", "derived", "zhvi_missing", "same_year"),
        *_missing_cols("zhvi", "housing_zip_year zhvi_missing and build_panel.py"),
        _c("acs_window", "string", "5-year window, e.g. 2020-2024", "housing_zip_year (S13)", "observed", "acs_missing", "acs_window_ending_in_year"),
        _c("acs_window_start", "integer", "first year of 5-year window", "housing_zip_year (S13)", "observed", "acs_missing", "acs_window_ending_in_year"),
        _c("zcta_vintage", "integer", "2020", "housing_zip_year (S13)", "observed", "acs_missing", "acs_window_ending_in_year"),
    ]
    for c in ACS_VALUES:
        unit, table = ACS_META[c]
        lin = "derived" if c in ("vacancy_rate", f"median_hh_income{REAL}") else "observed"
        spec.append(_c(f"acs5_{c}", "float", unit, f"housing_zip_year (S13 {table})", lin, "acs_missing", "acs_window_ending_in_year"))
        spec.append(_c(f"acs5_{c}_moe", "float", unit + ", 90% margin of error", f"housing_zip_year (S13 {table})", lin, "acs_missing", "acs_window_ending_in_year"))
    spec += _missing_cols("acs", "housing_zip_year acs_missing and build_panel.py")
    spec += [
        _c("cdi_avg_fire_risk_2018", "float", "0-4 scale, calendar year 2018", "wildfire_zip (S2 Fire Risk Scores)", "crosswalked", "wildfire_zip cdi_risk_missing (empty outside region)", "fixed_vintage_2018"),
        _c("cdi_high_extreme_share_2018", "float", "share 0-1, calendar year 2018", "wildfire_zip (S2 Fire Risk Scores)", "derived", "wildfire_zip cdi_risk_missing (empty outside region)", "fixed_vintage_2018"),
        _c("cdi_risk_vintage_latest", "integer", "S2 vintage year used (latest vintage <= year)", "build_panel.py", "derived", "wildfire_risk_missing", "as_of_year"),
        _c("cdi_avg_fire_risk_latest", "float", "0-4 scale, vintage in cdi_risk_vintage_latest", "wildfire_zip (S2 Fire Risk Scores)", "crosswalked", "wildfire_risk_missing", "as_of_year"),
        _c("cdi_high_extreme_share_latest", "float", "share 0-1, vintage in cdi_risk_vintage_latest", "wildfire_zip (S2 Fire Risk Scores)", "derived", "wildfire_risk_missing", "as_of_year"),
        *_missing_cols("wildfire_risk", "build_panel.py"),
        _c("fire_damage_in_year", "integer", "0 or 1: a register fire starting in year damaged structures in the ZCTA", "event_register (DINS)", "derived", "wildfire_events_missing", "same_year"),
        _c("burned_to_date", "integer", "0 or 1: a register fire damaged structures in the ZCTA in or before year", "event_register (DINS)", "derived", "wildfire_events_missing", "as_of_year"),
        _c("years_since_first_burn", "integer", "years since first damaging fire (empty before it)", "event_register (DINS)", "derived", "wildfire_events_missing", "as_of_year"),
        _c("x1_destruction_window", "integer", "0 or 1: year is first burn year to first burn year + 2 (rule X1)", "event_register and docs/wildfire_timeline.md section 6", "derived", "wildfire_events_missing", "as_of_year"),
        _c("moratorium_active_in_year", "integer", "0 or 1: an SB 824 moratorium listing the ZCTA is in force during year", "event_register (CDI bulletins)", "derived", "wildfire_events_missing", "same_year"),
        _c("moratoria_started_to_date", "integer", "count of moratoria listing the ZCTA that started in or before year", "event_register (CDI bulletins)", "derived", "wildfire_events_missing", "as_of_year"),
        *_missing_cols("wildfire_events", "build_panel.py"),
        _c("county_population", "float", "persons at 1 July", "ca_county_panel (S17)", "county_control", "controls_missing", "same_year", "county"),
        _c(f"county_income_pc{REAL}", "float", f"USD of {BASE_YEAR}", "ca_county_panel (S16 PCPI) and CPI-U", "county_control", "controls_missing", "same_year", "county"),
        _c("county_permits", "float", "private housing units authorized", "ca_county_panel (S16 BPPRIV)", "county_control", "controls_missing", "same_year", "county"),
        *_missing_cols("controls", "build_panel.py", "county"),
    ]
    for col, (kind, limit) in JUMP_THRESHOLDS.items():
        spec.append(_c(f"jump_flag__{col}", "integer", f"0 or 1: {kind.replace('_', ' ')} change vs year-1 above {limit}",
                       "build_panel.py", "derived", "none", "backward_difference"))
    spec += [
        _c("jump_flag_any", "integer", "0 or 1", "build_panel.py", "derived", "none", "backward_difference"),
        _c("complete_case", "integer", "0 or 1: all COMPLETE_CASE_COLUMNS present", "build_panel.py", "derived", "none", "same_year"),
    ]
    return spec


def write_dictionary(spec: list[dict]) -> None:
    d = pd.read_csv(DICTIONARY, dtype=str, keep_default_na=False)
    rows = pd.DataFrame([{k: v for k, v in s.items() if k != "timing"} for s in spec]).assign(table="panel")
    d = pd.concat([d[d["table"] != "panel"], rows[d.columns]], ignore_index=True)
    d.to_csv(DICTIONARY, index=False, lineterminator="\n")


# ---------------------------------------------------------------- manifest


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def manifest(panel: pd.DataFrame, inp: dict[str, pd.DataFrame], spec: list[dict]) -> dict:
    inputs = {}
    for name, (rel, cmd, raw) in INPUTS.items():
        entry = {"sha256": sha256(ROOT / rel), "rows": len(inp[name]), "produced_by": cmd}
        if raw:
            entry["raw_manifest"] = {"path": f"{raw}/manifest.json" if (ROOT / raw / "manifest.json").exists()
                                     else f"{raw}/*/*/manifest.json"}
        inputs[rel] = entry
    raw_manifests = {str(p.relative_to(ROOT)): sha256(p) for p in sorted((DATA / "raw").rglob("manifest.json"))}
    input_hash = hashlib.sha256("".join(f"{k}:{v['sha256']}\n" for k, v in inputs.items()).encode()).hexdigest()
    sha = _git("rev-parse", "HEAD")
    tracked = [rel for rel, _, _ in INPUTS.values()] + ["build_panel.py"]
    dirty = _git("status", "--porcelain", "--", *tracked)
    return {
        "panel_version": f"s{SCHEMA_VERSION}-{sha[:12]}-{input_hash[:12]}",
        "schema_version": SCHEMA_VERSION,
        "git_sha": sha,
        "git_dirty": bool(dirty) if dirty != "unknown" else None,
        "input_hash": input_hash,
        "build_command": "uv run python build_panel.py",
        "base_year": BASE_YEAR,
        "output": {"path": str(PANEL.relative_to(ROOT)), "sha256": sha256(PANEL), "rows": len(panel),
                   "columns": len(panel.columns), "zctas": int(panel["zcta"].nunique()),
                   "years": [int(panel["year"].min()), int(panel["year"].max())]},
        "key": ["zcta", "year"],
        "canonical_key": ["zcta", "year", "policy_form"],
        "universe": panel.drop_duplicates("zcta")["panel_role"].value_counts().sort_index().to_dict(),
        "inputs": inputs,
        "raw_manifests": raw_manifests,
        "imputation": "none: missing values stay missing; no fill, carry-forward or interpolation",
        "acs_alignment": "ACS 5-year values sit only on the window end year; never repeated on other years",
        "complete_case_columns": COMPLETE_CASE_COLUMNS,
        "jump_thresholds": {k: {"kind": v[0], "limit": v[1]} for k, v in JUMP_THRESHOLDS.items()},
        "column_timing": {s["column"]: s["timing"] for s in spec},
    }


# ---------------------------------------------------------------- audit


def _md(df: pd.DataFrame) -> str:
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for row in df.itertuples(index=False):
        lines.append("| " + " | ".join("" if pd.isna(v) else str(v) for v in row) + " |")
    return "\n".join(lines)


SOURCE_BLOCKS = {
    "S2 HO premium": "admitted_homeowners__premium",
    "S3 counts": "admitted_homeowners__counts",
    "S2 DO+DT premium": "dwelling_fire__premium",
    "S6 FAIR share": "fair_plan__share",
    "S11 ZHVI": "zhvi",
    "S13 ACS": "acs",
    "S2 risk (latest)": "wildfire_risk",
    "Events (DINS, CDI)": "wildfire_events",
    "County controls": "controls",
}


def join_losses(inp: dict[str, pd.DataFrame], panel: pd.DataFrame) -> pd.DataFrame:
    keys = set(zip(panel["zcta"], panel["year"]))
    pz = set(panel["zcta"])
    rows = []

    def step(source, stage, n_in, n_lost, note):
        rows.append({"source": source, "step": stage, "rows in": n_in, "rows lost": n_lost, "note": note})

    ins, uins = inp["insurance"], inp["unmatched_insurance"]
    step("insurance_zip_year", "ZIP to 2020 ZCTA (ingest #3)", len(ins) + len(uins), len(uins),
         f"PO-box ZIPs {', '.join(sorted(uins['zip'].unique()))} have no ZCTA; in data/interim/unmatched")
    lost = sum((z, y) not in keys for z, y in zip(ins["zcta"], ins["year"]))
    step("insurance_zip_year", "join to panel universe", len(ins), lost, "pivot by policy_form: 3 forms to column prefixes")
    h, uh = inp["housing"], inp["unmatched_housing"]
    step("housing_zip_year", "ZIP to 2020 ZCTA (ingest #4)", len(h) + len(uh), len(uh),
         f"Zillow ZIPs {', '.join(sorted(uh['zip'].unique()))} have no CA 2020 ZCTA")
    lost = sum((z, y) not in keys for z, y in zip(h["zcta"], h["year"]))
    step("housing_zip_year", "join to panel universe", len(h), lost,
         f"by design: {h['zcta'].nunique() - len(pz & set(h['zcta']))} California ZCTAs are neither target, region nor spillover source")
    w, uw = inp["wildfire"], inp["unmatched_wildfire"]
    step("wildfire_zip", "S2 ZIP to region ZCTA (ingest #5)", len(uw), len(uw),
         f"S2 ZIP-years with no region ZCTA (ZIPs {', '.join(sorted(uw['zip'].unique()))})")
    step("wildfire_zip", "join to panel universe", len(w), int((~w["zcta"].isin(pz)).sum()), "one row per region ZCTA, repeated on every year")
    ev = inp["events"]
    listed = ev["zctas_affected"].str.split(";").explode()
    listed = listed[listed.fillna("") != ""]
    step("event_register", "ZCTA-level join", len(ev), int((ev["zctas_affected"] == "").sum()),
         f"statewide events have no ZCTA and are not joined; {int((~listed.isin(pz)).sum())} listed ZCTAs outside universe")
    cp = inp["county_panel"]
    used = cp[cp["fips"].isin(set(panel["county_fips"]))]
    step("ca_county_panel", "join on county_fips and year", len(used), sum(
        (f, y) not in set(zip(panel["county_fips"], panel["year"])) for f, y in zip(used["fips"], used["year"])),
        "county years outside the panel years are not joined")
    return pd.DataFrame(rows)


def reconciliation(inp: dict[str, pd.DataFrame], panel: pd.DataFrame) -> pd.DataFrame:
    rows = []
    share = inp["zcta_county_share"]
    share = share[share["county_fips"] == NEVADA_FIPS].set_index("zcta")["county_land_share"]
    h = inp["housing"]
    cp = inp["county_panel"]
    cpop = cp[cp["fips"] == NEVADA_FIPS].set_index("year")["population"]
    for end in range(ACS_INGESTED[0], ACS_INGESTED[1] + 1):
        a = h[(h["year"] == end) & h["zcta"].isin(share.index)].set_index("zcta")["population"]
        zsum = float((a * share.reindex(a.index)).sum())
        county = float(cpop.loc[end - 4:end].mean())
        rows.append({
            "check": f"ACS {end - 4}-{end} population, land-share weighted over {len(a)} ZCTAs touching Nevada County",
            "ZIP sum": round(zsum), "county total": round(county),
            "discrepancy": f"{(zsum - county) / county:+.1%}",
            "definition gap": "5-year ACS average vs mean of 1 July estimates; weights are land area, not population",
        })
    fires = inp["events"][inp["events"]["event_type"] == "fire"]
    stated = fires["notes"].str.extract(r"DINS: (\d+) destroyed")[0].dropna().astype(int)
    regional = fires["zctas_affected"] != ""
    stated_regional = fires.loc[regional, "notes"].str.extract(r"DINS: (\d+) destroyed")[0].dropna().astype(int)
    zsum = int(inp["wildfire"]["structures_destroyed"].sum())
    rows.append({
        "check": "DINS structures destroyed: sum over region ZCTAs vs register fire totals (regional fires)",
        "ZIP sum": zsum, "county total": int(stated_regional.sum()),
        "discrepancy": f"{(zsum - stated_regional.sum()) / stated_regional.sum():+.1%}",
        "definition gap": "regional register rows are built from the same DINS records, with the ZCTA from coordinates; "
                          f"the {int((~regional).sum())} statewide context fires are outside the region and not counted",
    })
    last = panel[panel["year"] == panel["year"].max()].set_index("zcta")["burned_to_date"].dropna()
    burned = inp["wildfire"].set_index("zcta")["burned"].reindex(last.index)
    rows.append({
        "check": "Burned ZCTAs: panel burned_to_date in the last year vs wildfire_zip burned",
        "ZIP sum": int(last.sum()), "county total": int(burned.sum()),
        "discrepancy": f"{int((last != burned).sum())} ZCTAs differ",
        "definition gap": "same DINS records; panel side uses register fire dates, wildfire_zip side the ever-burned flag",
    })
    rows.append({
        "check": "S3 ZIP policy counts vs S4 county totals", "ZIP sum": "", "county total": "",
        "discrepancy": "not computed",
        "definition gap": "S4 county totals are PDF tables not parsed by ingest #3; S3 also omits PO-box ZIPs 95712 and 95924",
    })
    rows.append({
        "check": "S2 ZIP premium vs county or state totals", "ZIP sum": "", "county total": "",
        "discrepancy": "not computed",
        "definition gap": "no county S2 total exists; S1 statewide averages use a different exposure base",
    })
    return pd.DataFrame(rows)


def audit(panel: pd.DataFrame, inp: dict[str, pd.DataFrame], man: dict) -> str:
    t = panel[panel["panel_role"] == "target"]
    counts = []
    for label, block in SOURCE_BLOCKS.items():
        code = panel[f"{block}_missing"]
        present = panel[code == 0]
        counts.append({
            "source": label, "present ZCTA-years": int((code == 0).sum()),
            "ZCTAs with data": int(present["zcta"].nunique()),
            "years with data": f"{present['year'].min()}-{present['year'].max()}" if len(present) else "none",
            "target present": int((t[f"{block}_missing"] == 0).sum()),
            **{f"code {k}": int((code == k).sum()) for k in range(6)},
        })
    by_role = []
    for role, g in panel.groupby("panel_role"):
        r = {"panel_role": role, "ZCTAs": g["zcta"].nunique()}
        for label, block in SOURCE_BLOCKS.items():
            r[label] = f"{(g[f'{block}_missing'] == 0).mean():.0%}"
        by_role.append(r)
    years = list(range(2013, int(panel["year"].max()) + 1))
    grid = []
    for z, g in t.groupby("zcta"):
        g = g.set_index("year")
        for label, block in SOURCE_BLOCKS.items():
            grid.append({"zcta": z, "source": label, **{str(y): int(g.loc[y, f"{block}_missing"]) for y in years}})
    early = t[t["year"] < years[0]]
    early_codes = {label: sorted(early[f"{b}_missing"].unique().tolist()) for label, b in SOURCE_BLOCKS.items()}
    jumps = [{"column": c, "rule": f"{k.replace('_', ' ')} > {v}",
              "flagged ZCTA-years": int(panel[f"jump_flag__{c}"].eq(1).sum()),
              "flagged in target": int(t[f"jump_flag__{c}"].eq(1).sum())} for c, (k, v) in JUMP_THRESHOLDS.items()]
    flagged = panel[panel["jump_flag_any"] == 1]
    tflag = t[t["jump_flag_any"] == 1][["zcta", "year"] + [f"jump_flag__{c}" for c in JUMP_THRESHOLDS]]
    tflag_list = ", ".join(
        f"{r['zcta']} {r['year']} ({', '.join(c.removeprefix('jump_flag__') for c in tflag.columns[2:] if r[c] == 1)})"
        for r in tflag.fillna(0).to_dict("records")) or "none"
    cc = panel[panel["complete_case"] == 1]
    ins_supp = int(sum((t[f"{b}_missing"] == 2).sum() for b in INSURANCE_BLOCKS))
    ev = inp["events"][inp["events"]["zctas_affected"] != ""]
    first_fire = ev.loc[ev["event_type"] == "fire", "date_start"].str[:4].min()
    first_mor = ev.loc[ev["event_type"] == "nonrenewal_moratorium", "date_start"].str[:4].min()
    return f"""# ZIP panel audit

Issue #6. Generated by `uv run python build_panel.py`; do not edit by hand. Panel version
`{man['panel_version']}` (schema {SCHEMA_VERSION}, git `{man['git_sha'][:12]}`, input hash `{man['input_hash'][:12]}`).
Column definitions are in `docs/data_dictionary.csv` (table `panel`); lineage and hashes are in
`data/panel_manifest.json`.

## 1. Panel shape

- File: `data/panel_zip_year.csv`, {len(panel):,} rows, {len(panel.columns)} columns.
- Key: `(zcta, year)`, unique. `policy_form = ALL` on every row, so the canonical key
  `(zcta, year, policy_form)` of `docs/schema.md` is unique too. Insurance values are pivoted
  by policy form into column prefixes `admitted_homeowners__`, `dwelling_fire__` and `fair_plan__`.
- Universe: every ZCTA in `data/wildfire_zip.csv` (target core and fringe, region, excluded east)
  and every Bay Area spillover-source ZCTA from `data/housing_zip_year.csv`, crossed with every
  year {panel['year'].min()}-{panel['year'].max()}. No row is dropped because a source is missing.

{_md(pd.DataFrame([{'panel_role': k, 'ZCTAs': v, 'rows': v * panel['year'].nunique()} for k, v in man['universe'].items()]))}

## 2. Observations per source

A ZCTA-year counts as present when the block's `*_missing` code is 0. Codes follow
`docs/schema.md` section 7: 1 not published, 2 suppressed or annotated, 3 outside source
coverage years, 4 unmatched, 5 excluded by a documented rule. Each code has a
`*_missing_reason` text column in the panel.

{_md(pd.DataFrame(counts))}

Share of ZCTA-years present, by role:

{_md(pd.DataFrame(by_role))}

## 3. Join losses

{_md(join_losses(inp, panel))}

## 4. Missingness and suppression, target ZCTAs by year

Missing code per target ZCTA, source and year, {years[0]}-{years[-1]} (0 = present). Years before
{years[0]} have these codes for every target ZCTA: {'; '.join(f'{k} {v}' for k, v in early_codes.items())}.
The full ZCTA x year x source grid for all {panel['zcta'].nunique()} ZCTAs is the `*_missing`
columns of the panel.

{_md(pd.DataFrame(grid))}

Suppression: S6 omits ZIPs with fewer than 5 insured structures (code 2 in `fair_plan__share`);
ACS annotations give code 2 in `acs` ({int((panel['acs_missing'] == 2).sum())} ZCTA-years, the row keeps
its non-annotated values). Target ZCTA-years with code 2 in an insurance block: {ins_supp}. The
other target insurance gaps are years outside source coverage (code 3).

## 5. Reconciliation with county totals

{_md(reconciliation(inp, panel))}

## 6. Design decisions

- **ACS 5-year windows.** An ACS value sits only on the last year of its window (`acs_window`,
  `acs_window_start`). It is not copied to the other four years and not interpolated, so
  `acs5_*` columns are empty in years without a window end. Adjacent windows share four years;
  the panel computes no year-over-year change or jump flag on ACS columns (`docs/estimands.md`
  section 6). Only windows ending {ACS_INGESTED[0]}-{ACS_INGESTED[1]} are ingested.
- **Time-invariant wildfire attributes.** `wildfire_zip.csv` holds snapshots over the whole study
  period (ever burned, 2023 risk). Those are future information for early years, so the panel
  does not copy them. It derives year-specific columns instead: `burned_to_date`,
  `years_since_first_burn`, `x1_destruction_window`, `moratorium_active_in_year`,
  `moratoria_started_to_date` (from `data/event_register.csv` dates), and
  `cdi_*_latest` (the latest S2 vintage at or before the year, named in `cdi_risk_vintage_latest`).
  The fixed 2018 risk vintage and 2020 housing units are kept and carry their vintage in the name.
- **Event coverage.** Event columns start in {DINS_START} (DINS). The register's first damaging fire
  in the region is in {first_fire} and the first moratorium listing a region ZCTA in {first_mor}, so 0 before
  then means none recorded.
- **County controls.** `county_*` columns repeat the county value on each ZCTA row
  (`lineage = county_control`). They never fill a ZCTA value or count as ZCTA coverage.
- **Real dollars.** `*{REAL}` columns use CPI-U annual means from `data/national_annual.csv`
  with base year {BASE_YEAR}. Nominal columns are kept.

## 7. Temporal leakage

Every panel column has a timing class in `data/panel_manifest.json` (`column_timing`):
`same_year`, `as_of_year` (uses only information dated at or before the year),
`acs_window_ending_in_year`, `backward_difference` (year vs year-1), `fixed_vintage_<year>`
(name ends with the vintage year) and `static_geography`. `tests/test_panel.py` rebuilds the
panel from inputs cut at a year and checks that earlier rows do not change.

## 8. Implausible jumps

A jump is flagged, not removed. Flags compare a year with the previous year of the same ZCTA.

{_md(pd.DataFrame(jumps))}

{len(flagged)} ZCTA-years have at least one flag (`jump_flag_any`). Target ZCTA-years flagged:
{tflag_list}. Insurance jumps in 2020 and 2021 coincide with the S3 definition break
(`admitted_homeowners__series_break`) and the S2 coverage basis change (`*__coverage_def_change`).

## 9. Imputation and complete-case subset

No value is imputed. Missing values stay empty with a code; nothing is filled with zero,
carried forward or interpolated. `complete_case = 1` marks rows where all of
{', '.join(f'`{c}`' for c in COMPLETE_CASE_COLUMNS)} are present: {len(cc)} rows,
{cc['zcta'].nunique()} ZCTAs, years {cc['year'].min() if len(cc) else 'none'}-{cc['year'].max() if len(cc) else 'none'}.
Report main results on all available rows and repeat them on `complete_case == 1` as the
sensitivity subset.

## 10. Restricted data

FAIR Plan S9 and S10 files have no reuse grant (`docs/schema.md` section 8). No panel column comes
from them, and `data/raw/restricted/` is in `.gitignore`. The `fair_plan__*` columns come from the
public CDI S6 table.
"""


# ---------------------------------------------------------------- main


def main() -> int:
    inp = load_inputs()
    panel = build(inp)
    PANEL.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(PANEL, index=False, lineterminator="\n")
    spec = column_spec()
    write_dictionary(spec)
    man = manifest(panel, inp, spec)
    MANIFEST.write_text(json.dumps(man, indent=2) + "\n")
    AUDIT.write_text(audit(panel, inp, man))
    print(f"wrote {PANEL.relative_to(ROOT)}: {len(panel)} rows, {panel['zcta'].nunique()} ZCTAs, "
          f"{len(panel.columns)} columns; version {man['panel_version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
