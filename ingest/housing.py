"""Build data/housing_zip_year.csv: Zillow ZIP ZHVI and ACS 5-year ZCTA covariates for California (#4).

Sources are streamed and filtered to California before anything is written; the full files are never loaded.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Iterable
from email.utils import parsedate_to_datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from ingest.keys import assert_unique_keys, canonical_keys, map_zip_to_zcta, normalize_zcta
from ingest.manifest import write_manifest

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "housing"
NATIONAL = ROOT / "data" / "national_annual.csv"
OUT = ROOT / "data" / "housing_zip_year.csv"
META = ROOT / "data" / "housing_zip_year.meta.json"
UNMATCHED = ROOT / "data" / "interim" / "unmatched" / "housing_zip_year.csv"

# Must equal BASE_YEAR in model.py; ingest may not import model.py (ingest/__init__.py).
BASE_YEAR = 2025
REAL = f"_real_{BASE_YEAR}"

ZHVI_PRODUCT = "zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month"
ZILLOW_URL = f"https://files.zillowstatic.com/research/public_csvs/zhvi/Zip_{ZHVI_PRODUCT}.csv"
ZILLOW_RAW = "zillow_zip_zhvi_ca_monthly.csv"
ZILLOW_KEEP = ("RegionID", "RegionName", "RegionType", "State", "City", "Metro", "CountyName")
MIN_MONTHS = 10
ANNUAL_RULE = f"calendar-year mean of monthly values, kept only when the year has {MIN_MONTHS}+ months (fetch_data.annual_mean)"
ANNUAL_RULE_CODE = f"mean_ge{MIN_MONTHS}m"

# The table-based ACS summary file starts with the 2021 release; earlier vintages use sequence files (docs/controls.md).
ACS_YEARS = (2021, 2022, 2023, 2024)
ACS_URL = "https://www2.census.gov/programs-surveys/acs/summary_file/{year}/table-based-SF/data/5YRData/acsdt5y{year}-{table}.dat"
ACS_FIRST_WINDOW_END = 2011  # 2007-2011 is the first ACS 5-year release with ZCTAs (docs/sources.md S13)
ACS_GEO_PREFIX = "860Z200US"  # 2020 ZCTA summary level; a 2010-ZCTA file would fail this check
ACS_VARS = {
    "population": ("B01003", "001"),
    "households": ("B11001", "001"),
    "median_hh_income": ("B19013", "001"),
    "housing_units": ("B25001", "001"),
    "occupancy_total": ("B25002", "001"),
    "vacant_units": ("B25002", "003"),
}
ACS_DOLLARS = ("median_hh_income",)
ACS_CONTROLLED_MOE = -555555555  # Census annotation: estimate is controlled, MOE is zero

# Provisional screen until CDI S2 exposure shares are ingested (docs/estimands.md section 5, criterion 2).
# Marin, Napa, Sonoma are named as exposed in estimands.md; Solano had the 2020 LNU complex.
SPILLOVER_SCREEN = "provisional_v0"
SPILLOVER_COUNTIES = frozenset({"Alameda County", "Contra Costa County", "San Francisco County", "San Mateo County", "Santa Clara County"})
SPILLOVER_EXCLUDED_ZCTAS = {
    "94611": "Oakland hills", "94618": "Oakland hills", "94619": "Oakland hills",
    "94705": "Berkeley hills", "94708": "Berkeley hills", "94563": "Orinda hills", "94556": "Moraga hills",
    "94020": "Santa Cruz Mountains", "94028": "Santa Cruz Mountains", "94060": "Santa Cruz Mountains",
    "94062": "Santa Cruz Mountains", "94074": "Santa Cruz Mountains", "95030": "Santa Cruz Mountains",
    "95033": "Santa Cruz Mountains", "95070": "Santa Cruz Mountains", "95120": "Santa Cruz Mountains",
    "95140": "Mount Hamilton (2020 SCU complex)",
}

ACS_COLS = [
    "acs_window", "acs_window_start", "zcta_vintage",
    "population", "population_moe", "households", "households_moe",
    "median_hh_income", "median_hh_income_moe", f"median_hh_income{REAL}", f"median_hh_income{REAL}_moe",
    "housing_units", "housing_units_moe", "vacant_units", "vacant_units_moe",
    "vacancy_rate", "vacancy_rate_moe", "acs_missing",
]
COLUMNS = [
    "zcta", "year", "policy_form", "zip_source", "zcta_method",
    "zillow_region_id", "zillow_county", "zhvi_product", "zhvi_revision", "zhvi_rule",
    "zhvi_months", "zhvi_nominal", f"zhvi{REAL}", "zhvi_missing",
    *ACS_COLS,
    "candidate_spillover_source", "spillover_screen",
]


def is_ca_zcta(code: str) -> bool:
    """California ZIPs and ZCTAs are 90001-96162; 962-966 are military and Pacific."""
    return len(code) == 5 and code.isdigit() and 900 <= int(code[:3]) <= 961


def _date_columns(header: list[str]) -> int:
    return next(i for i, h in enumerate(header) if h[:2] in ("19", "20"))


def filter_zillow(lines: Iterable[str], out: Path, revision: str) -> int:
    """Write California rows of a Zillow ZIP file to `out`, keeping IDs, product, revision and months."""
    reader = csv.reader(lines)
    header = next(reader)
    idx = {h: header.index(h) for h in ZILLOW_KEEP}
    first = _date_columns(header)
    n = 0
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([*ZILLOW_KEEP, "zhvi_product", "zhvi_revision", *header[first:]])
        for row in reader:
            if row[idx["State"]] != "CA":
                continue
            w.writerow([*(row[idx[h]] for h in ZILLOW_KEEP), ZHVI_PRODUCT, revision, *row[first:]])
            n += 1
    return n


def filter_acs(lines: Iterable[str], out: Path) -> int:
    """Write the header and California 2020-ZCTA rows of a pipe-delimited ACS table file to `out` as CSV."""
    it = iter(lines)
    header = next(it).split("|")
    n = 0
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for line in it:
            if line.startswith(ACS_GEO_PREFIX) and is_ca_zcta(line[len(ACS_GEO_PREFIX):len(ACS_GEO_PREFIX) + 5]):
                w.writerow(line.split("|"))
                n += 1
    return n


def _stream(url: str):
    r = requests.get(url, stream=True, timeout=300)
    r.raise_for_status()
    r.encoding = "utf-8"
    return r


def download(raw: Path = RAW, refresh: bool = False) -> None:
    raw.mkdir(parents=True, exist_ok=True)
    zpath = raw / ZILLOW_RAW
    if refresh or not zpath.exists():
        with _stream(ZILLOW_URL) as r:
            modified = r.headers.get("Last-Modified")
            revision = parsedate_to_datetime(modified).date().isoformat() if modified else "unknown"
            n = filter_zillow(r.iter_lines(decode_unicode=True), zpath, revision)
        write_manifest(zpath, ZILLOW_URL, f"Zillow Terms of Use 4.C; cite 'Data Provided by Zillow Group'; source Last-Modified {modified}; CA rows only")
        print(f"zillow: {n} CA ZIP rows, revision {revision}")
    for year in ACS_YEARS:
        for table in sorted({t for t, _ in ACS_VARS.values()}):
            path = raw / f"acs5y{year}_{table.lower()}.csv"
            if path.exists() and not refresh:
                continue
            url = ACS_URL.format(year=year, table=table.lower())
            with _stream(url) as r:
                n = filter_acs(r.iter_lines(decode_unicode=True), path)
            write_manifest(path, url, "U.S. Census Bureau, public domain; CA 2020 ZCTA rows only")
            print(f"acs {year} {table}: {n} CA ZCTA rows")


def annual_align(monthly: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (annual means, month counts) by calendar year for a month-indexed frame.

    Same rule as fetch_data.annual_mean: years with fewer than MIN_MONTHS months are NaN, not partial means.
    """
    years = monthly.index.year
    months = monthly.notna().groupby(years).sum()
    means = monthly.groupby(years).mean().where(months >= MIN_MONTHS)
    means.index.name = months.index.name = "year"
    return means, months


def load_zillow(path: Path) -> pd.DataFrame:
    """Long (zip, year) table of annual ZHVI from the filtered snapshot, with month counts and lineage."""
    wide = pd.read_csv(path, dtype=str)
    meta_cols = [*ZILLOW_KEEP, "zhvi_product", "zhvi_revision"]
    dates = [c for c in wide.columns if c not in meta_cols]
    monthly = wide[dates].apply(pd.to_numeric, errors="coerce").T
    monthly.index = pd.to_datetime(monthly.index)
    monthly.columns = wide["RegionName"]
    means, months = annual_align(monthly)
    long = means.reset_index().melt(id_vars="year", var_name="zip", value_name="zhvi_nominal")
    counts = months.reset_index().melt(id_vars="year", var_name="zip", value_name="zhvi_months")
    long = long.merge(counts, on=["year", "zip"], validate="one_to_one")
    long = long[long["zhvi_months"] > 0]
    info = wide.set_index("RegionName")[["RegionID", "CountyName", "zhvi_product", "zhvi_revision"]]
    info = info.rename(columns={"RegionID": "zillow_region_id", "CountyName": "zillow_county"})
    long = long.join(info, on="zip")
    long["zhvi_rule"] = ANNUAL_RULE_CODE
    long["zhvi_missing"] = np.where(long["zhvi_nominal"].notna(), 0, 5)
    return long.reset_index(drop=True)


def _acs_value(s: pd.Series) -> pd.Series:
    v = pd.to_numeric(s, errors="coerce")
    return v.where(v >= 0)


def _acs_moe(s: pd.Series) -> pd.Series:
    v = pd.to_numeric(s, errors="coerce")
    return v.mask(v == ACS_CONTROLLED_MOE, 0.0).where(lambda x: x >= 0)


def load_acs(raw: Path, years: Iterable[int] = ACS_YEARS) -> pd.DataFrame:
    """One row per (zcta, window end year) with estimates, MOEs and window labels."""
    frames = []
    for year in years:
        cols = {}
        for name, (table, line) in ACS_VARS.items():
            df = pd.read_csv(raw / f"acs5y{year}_{table.lower()}.csv", dtype=str).set_index("GEO_ID")
            if not df.index.str.startswith(ACS_GEO_PREFIX).all():
                raise ValueError(f"{table} {year}: rows outside the 2020 ZCTA summary level")
            cols[name] = df[f"{table}_E{line}"]
            cols[f"{name}_moe"] = df[f"{table}_M{line}"]
        raw_df = pd.DataFrame(cols)
        out = pd.DataFrame(index=raw_df.index)
        annotated = pd.Series(False, index=raw_df.index)
        for name in ACS_VARS:
            out[name] = _acs_value(raw_df[name])
            out[f"{name}_moe"] = _acs_moe(raw_df[f"{name}_moe"])
            annotated |= out[name].isna()
        out["acs_missing"] = np.where(annotated, 2, 0)
        out["zcta"] = [normalize_zcta(g) for g in out.index]
        out["year"] = year
        out["acs_window_start"] = year - 4
        out["acs_window"] = f"{year - 4}-{year}"
        out["zcta_vintage"] = 2020
        frames.append(out.reset_index(drop=True))
    acs = pd.concat(frames, ignore_index=True)
    t, v = acs["occupancy_total"], acs["vacant_units"]
    p = v / t.where(t > 0)
    rad = acs["vacant_units_moe"] ** 2 - p**2 * acs["occupancy_total_moe"] ** 2
    # Census proportion MOE; switch to the ratio form when the radicand is negative.
    rad = rad.where(rad >= 0, acs["vacant_units_moe"] ** 2 + p**2 * acs["occupancy_total_moe"] ** 2)
    acs["vacancy_rate"] = p
    acs["vacancy_rate_moe"] = np.sqrt(rad) / t.where(t > 0)
    return acs.drop(columns=["occupancy_total", "occupancy_total_moe"])


def deflator(national: Path = NATIONAL, base_year: int = BASE_YEAR) -> pd.Series:
    """CPI[BASE_YEAR] / CPI[year], indexed by year; NaN where the annual CPI is missing."""
    cpi = pd.read_csv(national, index_col="year")["cpi"]
    if pd.isna(cpi.get(base_year)):
        raise ValueError(f"no annual CPI for base year {base_year}")
    return cpi[base_year] / cpi


def spillover_flags(zcta: pd.Series, county: pd.Series) -> pd.Series:
    """1 for Bay Area ZCTAs that pass the provisional low-exposure screen; a demand source, never a control."""
    ok = county.isin(SPILLOVER_COUNTIES) & ~zcta.isin(SPILLOVER_EXCLUDED_ZCTAS.keys())
    return ok.astype(int)


def build(raw: Path = RAW, national: Path = NATIONAL, acs_years: Iterable[int] = ACS_YEARS) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (panel keyed on (zcta, year), Zillow rows whose ZIP has no California 2020 ZCTA)."""
    acs_years = tuple(acs_years)
    acs = load_acs(raw, acs_years)
    zillow = load_zillow(raw / ZILLOW_RAW)
    matched, unmatched = map_zip_to_zcta(zillow, zctas=set(acs["zcta"]), zip_col="zip")
    matched = matched.drop(columns="zcta_weight").rename(columns={"zip": "zip_source"})

    panel = matched.merge(acs, on=["zcta", "year"], how="outer", validate="one_to_one")
    factor = deflator(national)
    by_year = panel["year"].map(factor)
    panel[f"zhvi{REAL}"] = panel["zhvi_nominal"] * by_year
    panel[f"median_hh_income{REAL}"] = panel["median_hh_income"] * by_year
    panel[f"median_hh_income{REAL}_moe"] = panel["median_hh_income_moe"] * by_year

    panel["zhvi_missing"] = panel["zhvi_missing"].fillna(1)
    # Windows ending 2011 to before the first ingested year exist but use sequence files, not ingested (code 5).
    no_acs, y = panel["acs_window"].isna(), panel["year"]
    code = np.select([y.isin(acs_years), y.between(ACS_FIRST_WINDOW_END, min(acs_years) - 1)], [1, 5], 3)
    panel["acs_missing"] = panel["acs_missing"].where(~no_acs, pd.Series(code, index=panel.index))

    county = panel.groupby("zcta")["zillow_county"].transform("first")
    panel["candidate_spillover_source"] = spillover_flags(panel["zcta"], county)
    panel["spillover_screen"] = SPILLOVER_SCREEN
    panel["policy_form"] = "ALL"
    for c in ("zhvi_months", "zhvi_missing", "acs_missing", "acs_window_start", "zcta_vintage"):
        panel[c] = panel[c].astype("Int64")
    panel["zhvi_months"] = panel["zhvi_months"].fillna(0)

    panel = canonical_keys(panel)
    assert_unique_keys(panel, ("zcta", "year"))
    panel = panel[COLUMNS].sort_values(["zcta", "year"]).reset_index(drop=True)
    return panel, unmatched


def _round(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if c.startswith(("vacancy_rate",)):
            out[c] = out[c].round(6)
        elif out[c].dtype == float:
            out[c] = out[c].round(2)
    return out


def write_outputs(panel: pd.DataFrame, unmatched: pd.DataFrame) -> None:
    _round(panel).to_csv(OUT, index=False)
    UNMATCHED.parent.mkdir(parents=True, exist_ok=True)
    cols = ["zip", "year", "zhvi_nominal", "zhvi_months", "zillow_region_id", "zillow_county"]
    _round(unmatched[cols]).sort_values(["zip", "year"]).to_csv(UNMATCHED, index=False)
    revision = sorted(panel["zhvi_revision"].dropna().unique())
    meta = {
        "table": OUT.name,
        "key": ["zcta", "year"],
        "canonical_key": ["zcta", "year", "policy_form"],
        "zhvi_product": ZHVI_PRODUCT,
        "zhvi_revision": revision,
        "zhvi_units": "USD nominal (zhvi_nominal); USD of BASE_YEAR (zhvi_real_*), not index points",
        "zhvi_annual_rule": ANNUAL_RULE,
        "zhvi_geography": "Zillow ZIP mapped to 2020 ZCTA by identity (docs/schema.md 4.1)",
        "deflator": f"CPI-U CPIAUCSL annual mean from data/national_annual.csv; real = nominal * CPI[{BASE_YEAR}] / CPI[year]",
        "base_year": BASE_YEAR,
        "acs_windows": sorted(panel["acs_window"].dropna().unique()),
        "acs_rule": "year = last year of the 5-year window; adjacent windows share 4 years and are not annual observations",
        "acs_dollars": "median_hh_income in dollars of the window end year; real column deflated with CPI of that year",
        "missing_codes": "docs/schema.md section 7",
        "spillover": {
            "screen": SPILLOVER_SCREEN,
            "counties": sorted(SPILLOVER_COUNTIES),
            "excluded_zctas": SPILLOVER_EXCLUDED_ZCTAS,
            "use": "candidate demand-spillover source; never a control or donor",
        },
        "rows": len(panel),
        "zctas": int(panel["zcta"].nunique()),
        "unmatched_zillow_zips": sorted(unmatched["zip"].unique()),
    }
    META.write_text(json.dumps(meta, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh", action="store_true", help="re-download raw snapshots even if present")
    args = ap.parse_args(argv)
    download(RAW, refresh=args.refresh)
    panel, unmatched = build(RAW, NATIONAL)
    write_outputs(panel, unmatched)
    print(f"wrote {OUT.relative_to(ROOT)}: {len(panel)} rows, {panel['zcta'].nunique()} ZCTAs")
    print(f"unmatched Zillow ZIPs (no CA 2020 ZCTA): {unmatched['zip'].nunique()} ZIPs, {len(unmatched)} ZIP-years")
    return 0


if __name__ == "__main__":
    sys.exit(main())
