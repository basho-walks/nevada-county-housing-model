import re
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import fetch_data
from ingest import housing as H

FIX = Path(__file__).parent / "fixtures" / "housing"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def raw(tmp_path) -> Path:
    with open(FIX / "zillow_zip_sample.csv") as f:
        H.filter_zillow(f, tmp_path / H.ZILLOW_RAW, "2026-09-16")
    for p in FIX.glob("acs5y2024_*.csv"):
        shutil.copy(p, tmp_path / p.name)
    return tmp_path


@pytest.fixture
def built(raw):
    return H.build(raw, FIX / "national_annual.csv", acs_years=(2024,))


@pytest.fixture
def panel(built):
    return built[0].set_index(["zcta", "year"])


def test_filter_zillow_keeps_ca_rows_ids_product_and_revision(raw):
    snap = pd.read_csv(raw / H.ZILLOW_RAW, dtype=str)
    assert sorted(snap["RegionName"]) == ["94110", "94611", "95424", "95945", "96161"]
    assert list(snap.columns[: len(H.ZILLOW_KEEP)]) == list(H.ZILLOW_KEEP)
    assert set(snap["zhvi_product"]) == {H.ZHVI_PRODUCT}
    assert set(snap["zhvi_revision"]) == {"2026-09-16"}
    assert snap.columns[-1] == "2025-03-31"


def test_load_zillow_keeps_leading_zeros(tmp_path):
    with open(FIX / "zillow_zip_sample.csv") as f:
        text = f.read().replace(",MA,MA,", ",MA,CA,")  # let the 02134 row pass the CA filter
    H.filter_zillow(text.splitlines(), tmp_path / "z.csv", "r")
    zips = set(H.load_zillow(tmp_path / "z.csv")["zip"])
    assert "02134" in zips


def test_filter_acs_keeps_only_california_2020_zctas(tmp_path):
    with open(FIX / "acs_raw_sample.dat") as f:
        n = H.filter_acs((line.rstrip("\n") for line in f), tmp_path / "a.csv")
    out = pd.read_csv(tmp_path / "a.csv", dtype=str)
    assert n == 2
    assert out["GEO_ID"].tolist() == ["860Z200US95945", "860Z200US96162"]
    assert out.columns[0] == "GEO_ID"


def test_annual_align_matches_fetch_data_rule():
    idx = pd.date_range("2022-01-31", "2025-12-31", freq="ME")
    rng = np.random.default_rng(0)
    s = pd.Series(rng.uniform(1e5, 5e5, len(idx)), index=idx)
    s[(s.index.year == 2023) & (s.index.month <= 2)] = np.nan  # 10 months: kept
    s[(s.index.year == 2024) & (s.index.month <= 3)] = np.nan  # 9 months: dropped
    means, months = H.annual_align(s.to_frame("v"))
    expected = fetch_data.annual_mean(s)
    pd.testing.assert_series_equal(means["v"].dropna(), expected, check_names=False, check_index_type=False)
    assert months["v"].to_dict() == {2022: 12, 2023: 10, 2024: 9, 2025: 12}


def test_monthly_to_annual_alignment_on_fixture(panel):
    assert panel.loc[("95945", 2023), "zhvi"] == pytest.approx(105500.0)
    assert panel.loc[("95945", 2023), "zhvi_months"] == 12
    # A missing June still leaves 11 months, so 2024 is kept as the mean of the months present.
    assert panel.loc[("95945", 2024), "zhvi"] == pytest.approx(200000.0)
    assert panel.loc[("95945", 2024), "zhvi_months"] == 11
    for key in [("95945", 2025), ("96161", 2024)]:
        assert pd.isna(panel.loc[key, "zhvi"])
        assert panel.loc[key, "zhvi_missing"] == 5
    assert panel.loc[("95945", 2025), "zhvi_months"] == 3
    assert set(panel["zhvi_rule"].dropna()) == {H.ANNUAL_RULE_CODE}


def test_schema(built):
    df, _ = built
    assert list(df.columns) == H.COLUMNS
    dictionary = pd.read_csv(ROOT / "docs" / "data_dictionary.csv", dtype=str)
    assert dictionary.loc[dictionary["table"] == "housing_zip_year", "column"].tolist() == H.COLUMNS
    assert df["zcta"].str.fullmatch(r"\d{5}").all()
    assert set(df["policy_form"]) == {"ALL"}
    assert df["year"].dtype.kind == "i"
    assert set(df["zhvi_product"].dropna()) == {H.ZHVI_PRODUCT}
    assert set(df["zhvi_revision"].dropna()) == {"2026-09-16"}
    assert set(df["spillover_screen"]) == {H.SPILLOVER_SCREEN}


def test_join_to_zcta_list_has_no_duplicate_keys(built, raw):
    df, unmatched = built
    zctas = {g.removeprefix(H.ACS_GEO_PREFIX) for g in pd.read_csv(raw / "acs5y2024_b01003.csv", dtype=str)["GEO_ID"]}
    assert not df.duplicated(["zcta", "year"]).any()
    assert set(df["zcta"]) == zctas
    assert (df["zip_source"].dropna() == df.loc[df["zip_source"].notna(), "zcta"]).all()
    # Zillow ZIPs without a 2020 ZCTA are reported, not dropped or forced onto a ZCTA.
    assert set(unmatched["zip"]) == {"95424"}
    assert "95424" not in set(df["zcta"])


def test_acs_only_zcta_has_no_zhvi(panel):
    row = panel.loc[("95959", 2024)]
    assert pd.isna(row["zhvi"]) and row["zhvi_missing"] == 1
    assert pd.isna(row["zip_source"])


def test_units_are_dollars_and_real_uses_cpi(panel):
    zhvi = panel["zhvi"].dropna()
    assert (zhvi > 10000).all()  # dollars, not index points near 100
    cpi = {2023: 300.0, 2024: 310.0, 2025: 320.0}
    for (z, y), row in panel.dropna(subset=["zhvi"]).iterrows():
        assert row[f"zhvi{H.REAL}"] == pytest.approx(row["zhvi"] * cpi[2025] / cpi[y])
    r = panel.loc[("95945", 2024)]
    assert r[f"median_hh_income{H.REAL}"] == pytest.approx(65989 * 320 / 310)
    assert r[f"median_hh_income{H.REAL}_moe"] == pytest.approx(4438 * 320 / 310)


def test_acs_window_and_missing_codes(panel):
    r = panel.loc[("95945", 2024)]
    assert r["acs_window"] == "2020-2024" and r["acs_window_start"] == 2020 and r["zcta_vintage"] == 2020
    assert r["population"] == 27470 and r["population_moe"] == 1049 and r["acs_missing"] == 0
    # 2023 has a published window (2019-2023) that this fixture does not ingest; 2025 has none yet.
    assert panel.loc[("95945", 2023), "acs_missing"] == 5
    assert panel.loc[("95945", 2025), "acs_missing"] == 3
    annotated = panel.loc[("95959", 2024)]
    assert pd.isna(annotated["median_hh_income"]) and pd.isna(annotated["median_hh_income_moe"])
    assert annotated["acs_missing"] == 2
    assert annotated["population"] == 16000


def test_population_is_never_filled_outside_acs_windows(built):
    df, _ = built
    assert df.loc[df["acs_window"].isna(), ["population", "households", "housing_units"]].isna().all().all()
    src = (ROOT / "ingest" / "housing.py").read_text()
    assert "ca_county_panel" not in src and "population.csv" not in src


def test_vacancy_rate_and_controlled_moe(panel):
    r = panel.loc[("95945", 2024)]
    p = 943 / 12944
    assert r["vacancy_rate"] == pytest.approx(p)
    assert r["vacancy_rate_moe"] == pytest.approx(np.sqrt(300**2 - p**2 * 539**2) / 12944)
    sf = panel.loc[("94110", 2024)]
    assert sf["housing_units_moe"] == 0  # -555555555 means a controlled estimate
    assert sf["vacancy_rate_moe"] == pytest.approx(300 / 32000)


def test_spillover_sources_are_tagged_rows_not_controls(panel):
    flag = panel["candidate_spillover_source"].groupby(level="zcta").agg(["min", "max"])
    assert (flag["min"] == flag["max"]).all()
    assert flag.loc["94110", "max"] == 1
    assert flag.loc["94611", "max"] == 0  # Oakland hills excluded by the exposure screen
    assert flag.loc["95945", "max"] == 0
    assert "control" not in " ".join(H.COLUMNS)


def test_base_year_matches_model():
    m = re.search(r"^BASE_YEAR = (\d{4})", (ROOT / "model.py").read_text(), re.M)
    assert int(m.group(1)) == H.BASE_YEAR
