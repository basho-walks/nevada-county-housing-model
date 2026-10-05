"""Download source data into data/ as annual CSVs.

The Zillow city file is ~95 MB, so only the Grass Valley and Nevada City rows are kept.
"""

import csv
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

DATA = Path(__file__).parent / "data"

NATIONAL = {"mortgage_rate": "MORTGAGE30US", "cpi": "CPIAUCSL"}
# FRED county series IDs embed the 5-digit FIPS code.
COUNTY_FRED = {
    "hpi": "ATNHPIUS{fips}A",  # FHFA all-transactions house price index
    "income_pc": "PCPI{fips}",  # per-capita personal income
    "permits": "BPPRIV0{fips}",  # new private housing units authorized
}
CENSUS = "https://www2.census.gov/programs-surveys/popest/datasets"
POP_FILES = {
    "2000s": f"{CENSUS}/2000-2010/intercensal/county/co-est00int-tot.csv",
    "2010s": f"{CENSUS}/2010-2020/counties/totals/co-est2020-alldata.csv",
    "2020s": f"{CENSUS}/2020-2025/counties/totals/co-est2025-alldata.csv",
}
ZILLOW_URL = (
    "https://files.zillowstatic.com/research/public_csvs/zhvi/"
    "City_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv"
)
CITIES = {"Grass Valley", "Nevada City"}


def fred_series(series_id: str) -> pd.Series:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    text = requests.get(url, timeout=60).text
    if not text.startswith("observation_date"):
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))  # FRED has no such series (some small counties)
    df = pd.read_csv(io.StringIO(text), parse_dates=[0])
    s = pd.to_numeric(df.iloc[:, 1], errors="coerce").dropna()
    s.index = df.iloc[:, 0][s.index]
    return s


def annual_mean(s: pd.Series) -> pd.Series:
    """Calendar-year mean of years with at least 10 months of data.

    BLS did not publish October 2025 CPI, so a strict 12-month rule drops 2025.
    """
    s = s.dropna()
    months = s.index.to_period("M").to_series().groupby(s.index.year).nunique()
    return s.groupby(s.index.year).mean()[months >= 10]


def fetch_national() -> pd.DataFrame:
    df = pd.DataFrame({name: annual_mean(fred_series(sid)) for name, sid in NATIONAL.items()})
    df.index.name = "year"
    return df


def census_wide(url: str) -> pd.DataFrame:
    df = pd.read_csv(url, encoding="latin-1", dtype={"STATE": str, "COUNTY": str})
    df["STATE"], df["COUNTY"] = df["STATE"].str.zfill(2), df["COUNTY"].str.zfill(3)
    df = df[(df["STATE"] == "06") & (df["COUNTY"] != "000")]
    pop = df.filter(regex=r"^POPESTIMATE\d{4}$")
    pop.columns = [int(c[-4:]) for c in pop.columns]
    pop.index = "06" + df["COUNTY"]
    return pop


def fetch_population() -> pd.DataFrame:
    """July 1 county population, 2000 to latest, with the 2020 Census seam smoothed.

    Census has no 2010-2020 intercensal county file, so the gap between the V2020 and V2025
    estimates for 2020 is spread linearly over 2011-2019, as Census does for intercensal series.
    """
    p00, p10, p20 = (census_wide(u) for u in POP_FILES.values())
    ratio = p20[2020] / p10[2020]
    weights = pd.Series(np.arange(1, 11) / 10, index=range(2011, 2021))
    p10_adj = p10[list(range(2011, 2020))].mul(1 + np.outer(ratio - 1, weights.loc[2011:2019]), axis=None)
    pop = pd.concat([p00[list(range(2000, 2010))], p10[[2010]], p10_adj, p20], axis=1)
    return pop.stack().rename("population").rename_axis(["fips", "year"]).to_frame()


def fetch_county_names() -> pd.Series:
    df = pd.read_csv(POP_FILES["2020s"], encoding="latin-1", dtype={"STATE": str, "COUNTY": str})
    df = df[(df["STATE"] == "06") & (df["COUNTY"] != "000")]
    return pd.Series(df["CTYNAME"].str.removesuffix(" County").values, index="06" + df["COUNTY"], name="name").rename_axis("fips")


def fetch_county_fred(fips: str) -> pd.DataFrame:
    cols = {}
    for name, pattern in COUNTY_FRED.items():
        s = fred_series(pattern.format(fips=fips))
        cols[name] = s.groupby(s.index.year).last()
    return pd.DataFrame(cols).assign(fips=fips).rename_axis("year").reset_index()


def fetch_panel() -> pd.DataFrame:
    pop = fetch_population()
    fips_codes = pop.index.get_level_values("fips").unique()
    with ThreadPoolExecutor(8) as pool:
        fred = pd.concat(pool.map(fetch_county_fred, fips_codes))
    return fred.set_index(["fips", "year"]).join(pop, how="outer").sort_index()


def fetch_cities() -> pd.DataFrame:
    rows = {}
    with requests.get(ZILLOW_URL, stream=True, timeout=300) as r:
        r.encoding = "utf-8"
        reader = csv.reader(r.iter_lines(decode_unicode=True))
        header = next(reader)
        name_i, state_i = header.index("RegionName"), header.index("State")
        first_date = next(i for i, h in enumerate(header) if h[:2] in ("19", "20"))
        for row in reader:
            if row[state_i] == "CA" and row[name_i] in CITIES:
                rows[row[name_i]] = pd.to_numeric(pd.Series(row[first_date:]), errors="coerce").values
    monthly = pd.DataFrame(rows, index=pd.to_datetime(header[first_date:]))
    annual = monthly.apply(annual_mean)
    annual.index.name = "year"
    return annual


if __name__ == "__main__":
    DATA.mkdir(exist_ok=True)
    fetch_national().to_csv(DATA / "national_annual.csv")
    fetch_panel().to_csv(DATA / "ca_county_panel.csv")
    fetch_county_names().to_csv(DATA / "ca_county_names.csv")
    fetch_cities().to_csv(DATA / "city_zhvi_annual.csv")
    print(f"wrote national_annual.csv, ca_county_panel.csv, ca_county_names.csv, city_zhvi_annual.csv to {DATA}")
