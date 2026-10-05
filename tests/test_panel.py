import json
import re
import subprocess

import numpy as np
import pandas as pd
import pytest

import build_panel as bp
from ingest.keys import canonical_keys
from ingest.manifest import sha256

TIMING = {"key", "same_year", "as_of_year", "acs_window_ending_in_year", "backward_difference", "static_geography"}


@pytest.fixture(scope="module")
def inputs():
    return bp.load_inputs()


@pytest.fixture(scope="module")
def panel(inputs):
    return bp.build(inputs)


@pytest.fixture(scope="module")
def manifest():
    return json.loads(bp.MANIFEST.read_text())


def _truncate(inp: dict[str, pd.DataFrame], cut: int) -> dict[str, pd.DataFrame]:
    """Inputs as they would look at the end of year `cut`."""
    out = dict(inp)
    for name in ("insurance", "housing", "county_panel"):
        out[name] = inp[name][inp[name]["year"] <= cut]
    ev = inp["events"]
    out["events"] = ev[ev["date_start"].str[:4].astype(int) <= cut]
    wf = inp["wildfire"].copy()
    for v in bp.S2_RISK_VINTAGES:
        if v > cut:
            wf[[c for c in wf.columns if c.startswith("cdi_") and f"_{v}" in c]] = np.nan
    out["wildfire"] = wf
    return out


def test_committed_panel_is_current(panel):
    assert panel.to_csv(index=False, lineterminator="\n") == bp.PANEL.read_text()


def test_keys_unique(panel):
    assert not panel.duplicated(["zcta", "year"]).any()
    assert (panel["policy_form"] == "ALL").all()
    canonical_keys(panel)


def test_zcta_set_stable_across_years(panel):
    sets = panel.groupby("year")["zcta"].apply(frozenset)
    assert sets.nunique() == 1
    years = range(panel["year"].min(), panel["year"].max() + 1)
    assert set(panel["year"]) == set(years)
    for col in ("geo_tier", "county_fips", "panel_role", "hu2020", "cdi_avg_fire_risk_2018"):
        assert (panel.groupby("zcta")[col].nunique(dropna=False) == 1).all(), col
    assert set(panel["zcta_vintage"].dropna()) == {2020}


def test_every_target_and_candidate_zcta_is_kept(panel, inputs):
    assert set(inputs["wildfire"]["zcta"]) <= set(panel["zcta"])
    sp = inputs["housing"].loc[inputs["housing"]["candidate_spillover_source"] == 1, "zcta"]
    assert set(sp) <= set(panel["zcta"])
    assert set(panel.loc[panel["panel_role"] == "target", "zcta"]) == set(bp.TARGET_ZCTAS)


def test_insurance_pivot_keeps_every_row(panel, inputs):
    p = panel.set_index(["zcta", "year"])
    for r in inputs["insurance"].itertuples():
        col = f"{r.policy_form}__{bp.INSURANCE_BLOCKS[next(b for b, v in bp.INSURANCE_BLOCKS.items() if v[0] == r.policy_form)][1]}"
        got, want = p.loc[(r.zcta, r.year), col], getattr(r, col.split("__")[1])
        assert (pd.isna(got) and pd.isna(want)) or got == pytest.approx(want), (r.zcta, r.year, col)


def test_no_imputation(panel, inputs):
    h = inputs["housing"].set_index(["zcta", "year"])
    p = panel.set_index(["zcta", "year"])
    both = p.index.intersection(h.index)
    pd.testing.assert_series_equal(p.loc[both, "zhvi_nominal"], h.loc[both, "zhvi_nominal"], check_names=False)
    pd.testing.assert_series_equal(p.loc[both, "acs5_population"], h.loc[both, "population"], check_names=False)
    assert p.drop(both)["zhvi_nominal"].isna().all()


def test_acs_values_only_on_window_end_year(panel):
    has = panel["acs5_population"].notna()
    assert has.any()
    end = panel.loc[has, "acs_window"].str[-4:].astype(int)
    assert (end == panel.loc[has, "year"]).all()
    assert (panel.loc[has, "acs_window_start"] == panel.loc[has, "year"] - 4).all()


def test_missing_codes_and_reasons(panel):
    blocks = [c.removesuffix("_missing") for c in panel.columns if c.endswith("_missing")]
    assert len(blocks) == 9
    for b in blocks:
        assert panel[f"{b}_missing"].isin(range(6)).all(), b
        reason = panel[f"{b}_missing_reason"]
        assert (reason.str.len() > 0).all(), b
        assert (reason == "present").eq(panel[f"{b}_missing"] == 0).all(), b
    indicator = {block: f"{v[0]}__{v[1]}" for block, v in bp.INSURANCE_BLOCKS.items()}
    indicator |= {"zhvi": "zhvi_nominal", "wildfire_risk": "cdi_avg_fire_risk_latest", "wildfire_events": "burned_to_date"}
    for b, col in indicator.items():
        assert panel[col].notna().eq(panel[f"{b}_missing"] == 0).all(), b
    assert (panel.loc[panel["acs_missing"] == 0, "acs5_population"].notna()).all()
    non_target = panel["panel_role"] != "target"
    assert (panel.loc[non_target, "admitted_homeowners__premium_missing"] == 5).all()


def test_every_column_has_timing_and_vintage_label(panel, manifest):
    timing = manifest["column_timing"]
    assert list(timing) == list(panel.columns)
    for col, t in timing.items():
        m = re.fullmatch(r"fixed_vintage_(\d{4})", t)
        assert t in TIMING or m, (col, t)
        if m:
            assert m.group(1) in col, f"{col} uses a fixed {m.group(1)} vintage without naming it"
        if t == "acs_window_ending_in_year":
            assert col.startswith("acs") or col == "zcta_vintage", col


@pytest.mark.parametrize("cut", [2017, 2019, 2021])
def test_no_future_information(inputs, panel, manifest, cut):
    early = bp.build(_truncate(inputs, cut))
    fixed = [c for c, t in manifest["column_timing"].items() if t.startswith("fixed_vintage_")]
    cols = [c for c in panel.columns if c not in fixed]
    # Spillover ZCTAs are tagged on housing rows, so one whose first row is after `cut` is absent from `early`.
    lost = set(panel["zcta"]) - set(early["zcta"])
    assert lost <= set(panel.loc[panel["panel_role"] == "spillover_source", "zcta"])
    assert not lost & set(inputs["housing"].loc[inputs["housing"]["year"] <= cut, "zcta"])
    a = panel.loc[(panel["year"] <= cut) & ~panel["zcta"].isin(lost), cols].reset_index(drop=True)
    b = early.loc[:, cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, check_dtype=False)


def test_jumps_above_threshold_are_flagged(panel):
    for col, (kind, limit) in bp.JUMP_THRESHOLDS.items():
        g = panel.sort_values(["zcta", "year"])
        prev = g.groupby("zcta")[col].shift()
        x = g[col].astype(float)
        change = (np.log(x) - np.log(prev.astype(float))).abs() if kind == "abs_log" else (x - prev).abs()
        big = change > limit
        assert (g.loc[big, f"jump_flag__{col}"] == 1).all(), col
        assert (g.loc[big, "jump_flag_any"] == 1).all(), col


def test_injected_jump_is_flagged(inputs):
    inp = dict(inputs)
    ins = inputs["insurance"].copy()
    hit = (ins["zcta"] == "95945") & (ins["year"] == 2022) & (ins["policy_form"] == "admitted_homeowners")
    ins.loc[hit, "avg_premium_per_exposure"] *= 3
    inp["insurance"] = ins
    p = bp.build(inp).set_index(["zcta", "year"])
    assert p.loc[("95945", 2022), f"jump_flag__admitted_homeowners__avg_premium_per_exposure{bp.REAL}"] == 1
    assert p.loc[("95945", 2023), f"jump_flag__admitted_homeowners__avg_premium_per_exposure{bp.REAL}"] == 1


def test_complete_case_flag(panel):
    want = panel[bp.COMPLETE_CASE_COLUMNS].notna().all(axis=1).astype(int)
    assert (panel["complete_case"] == want).all()
    cc = panel[panel["complete_case"] == 1]
    assert len(cc) > 0 and set(cc["panel_role"]) == {"target"}


def test_county_values_are_controls_only(panel):
    d = pd.read_csv(bp.DICTIONARY, dtype=str, keep_default_na=False)
    p = d[d["table"] == "panel"].set_index("column")
    county = [c for c in panel.columns if c.startswith("county_") and c != "county_fips" and "_missing" not in c]
    assert county and (p.loc[county, "lineage"] == "county_control").all()
    zcta_values = [c for c in panel.columns if p.loc[c, "lineage"] in ("observed", "crosswalked")]
    assert not any(c.startswith("county_") for c in zcta_values)


def test_dictionary_matches_outputs(panel):
    d = pd.read_csv(bp.DICTIONARY, dtype=str, keep_default_na=False)
    assert d.loc[d["table"] == "panel", "column"].tolist() == list(panel.columns)
    for table, path in [("insurance_zip_year", "insurance"), ("housing_zip_year", "housing"), ("wildfire_zip", "wildfire")]:
        header = pd.read_csv(bp.ROOT / bp.INPUTS[path][0], nrows=0).columns.tolist()
        assert d.loc[d["table"] == table, "column"].tolist() == header, table


def test_manifest_lineage(manifest, panel):
    assert manifest["panel_version"].startswith(f"s{bp.SCHEMA_VERSION}-")
    assert manifest["panel_version"].endswith(manifest["input_hash"][:12])
    assert manifest["git_sha"][:12] in manifest["panel_version"]
    for rel, entry in manifest["inputs"].items():
        assert sha256(bp.ROOT / rel) == entry["sha256"], rel
    assert manifest["output"]["sha256"] == sha256(bp.PANEL)
    assert manifest["output"]["rows"] == len(panel)
    assert manifest["imputation"].startswith("none")


def test_restricted_data_not_committed(panel):
    assert "data/raw/restricted/" in (bp.ROOT / ".gitignore").read_text().split()
    try:
        tracked = subprocess.run(["git", "ls-files", "data/raw/restricted"], cwd=bp.ROOT,
                                 capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git not available")
    assert tracked == ""
    assert not [c for c in panel.columns if re.search(r"(^|_)(pif|tiv)(_|$)", c)]
