import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from ingest import wildfire as w
from ingest.manifest import verify_manifest

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures" / "wildfire"


@pytest.fixture
def built(tmp_path):
    z, r = w.build(FIX, tmp_path / "wz.csv", tmp_path / "er.csv", tmp_path / "unmatched.csv")
    return z.set_index("zcta"), r.set_index("event_id"), tmp_path


def _register(**overrides):
    row = {
        "event_id": "X",
        "event_type": "fire",
        "date_start": "2021-08-04",
        "date_end": "2021-08-13",
        "date_uncertainty": "day",
        "geography": "Nevada County",
        "zctas_affected": "95945",
        "source_url": "https://example.invalid",
        "notes": "",
    }
    row.update(overrides)
    return pd.DataFrame([row], columns=w.REGISTER_COLUMNS)


# ---- register schema and date parsing


def test_parse_register_parses_dates_and_allows_open_end():
    df = pd.concat([_register(), _register(event_id="Y", date_end="")], ignore_index=True)
    out = w.parse_register(df)
    assert out["date_start"].dtype.kind == "M"
    assert out.loc[0, "date_end"] == pd.Timestamp("2021-08-13")
    assert pd.isna(out.loc[1, "date_end"])


@pytest.mark.parametrize("bad", ["08/04/2021", "2021-13-01", "2021-8-4x", "2021-02-30"])
def test_parse_register_rejects_bad_dates(bad):
    with pytest.raises(ValueError):
        w.parse_register(_register(date_start=bad))


@pytest.mark.parametrize(
    "overrides",
    [
        {"date_end": "2021-08-01"},
        {"event_type": "earthquake"},
        {"date_uncertainty": "about a month"},
        {"source_url": "CDI bulletin"},
        {"geography": ""},
        {"zctas_affected": "95945,95949"},
    ],
)
def test_parse_register_rejects_invalid_rows(overrides):
    with pytest.raises(ValueError):
        w.parse_register(_register(**overrides))


def test_parse_register_rejects_duplicate_ids_and_wrong_columns():
    with pytest.raises(ValueError):
        w.parse_register(pd.concat([_register(), _register()], ignore_index=True))
    with pytest.raises(ValueError):
        w.parse_register(_register().drop(columns=["notes"]))


# ---- build on fixtures


def test_build_one_row_per_zcta(built):
    z, _, _ = built
    assert z.index.is_unique
    assert sorted(z.index) == ["95602", "95713", "95945", "95949"]
    assert z.loc["95602", "county_fips_main"] == "06061"
    assert z.loc["95945", "geo_tier"] == "core" and z.loc["95602", "geo_tier"] == "fringe"
    assert z.loc["95713", "geo_tier"] == "region"


def test_build_risk_is_count_weighted_and_drops_fp(built):
    z, _, _ = built
    assert z.loc["95945", "cdi_avg_fire_risk_2018"] == pytest.approx((3.0 * 40 + 1.0 * 10) / 50)
    assert z.loc["95945", "cdi_high_extreme_share_2018"] == pytest.approx(30 / 50)
    assert z.loc["95945", "cdi_avg_fire_risk_2023"] == pytest.approx(3.5)
    assert z.loc["95945", "cdi_risk_missing"] == 0
    assert z.loc["95949", "cdi_risk_missing"] == 1
    assert z.loc["95602", "cdi_risk_missing"] == 1  # 2023 score blank
    assert z.loc["95713", "cdi_risk_missing"] == 1


def test_build_labels_every_measure(built):
    z, _, _ = built
    for m in ("cdi_avg_fire_risk", "cdi_high_extreme_share"):
        for y, pre in w.S2_VINTAGES.items():
            for suffix in ("source", "vintage", "aggregation_method"):
                assert z[f"{m}_{y}_{suffix}"].str.len().gt(0).all()
            assert (z[f"{m}_{y}_pretreatment"] == pre).all()


def test_build_reports_unmatched_zips(built):
    _, _, tmp = built
    unmatched = pd.read_csv(tmp / "unmatched.csv", dtype={"zip": str})
    assert unmatched["zip"].tolist() == ["95712"]


def test_build_flags_burned_and_spillover(built):
    z, r, _ = built
    assert r.loc["FIRE_2021_RIVER", "zctas_affected"] == "95713;95945"
    assert r.loc["FIRE_2021_RIVER", "date_end"] == "2021-08-13"
    assert "1 damaged records without coordinates" in r.loc["FIRE_2021_RIVER", "notes"]
    assert z.loc["95945", "burned"] == 1 and z.loc["95945", "burned_event_ids"] == "FIRE_2021_RIVER"
    assert z.loc["95945", "structures_destroyed"] == 1 and z.loc["95945", "structures_major_damage"] == 1
    assert z.loc["95945", "first_burn_date"] == "2021-08-04"
    # 95949 has only minor damage, so it is a neighbor, not burned.
    assert z.loc["95949", "burned"] == 0 and z.loc["95949", "spillover_candidate"] == 1
    assert z.loc["95602", "spillover_candidate"] == 1
    # A burned ZCTA next to another burned ZCTA of the same fire is not its spillover.
    assert z.loc["95945", "spillover_candidate"] == 0
    assert "TINY" not in " ".join(r.index)


def test_build_maps_moratorium_zips_and_notes_unmatched(built):
    z, r, _ = built
    assert r.loc["MOR_2021_08_05", "zctas_affected"] == "95945;95949"
    assert "95712" in r.loc["MOR_2021_08_05", "notes"]
    assert z.loc["95949", "moratorium_event_ids"] == "MOR_2021_08_05"
    assert z.loc["95713", "moratorium_event_ids"] == ""


def test_build_register_round_trips(built):
    _, _, tmp = built
    reg = w.read_register(tmp / "er.csv")
    assert reg["date_start"].is_monotonic_increasing


# ---- pivot cache reader


def _xlsx(definition: str, records: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/pivotCache/pivotCacheDefinition1.xml", definition)
        zf.writestr("xl/pivotCache/pivotCacheRecords1.xml", records)
    return buf.getvalue()


def test_read_pivot_cache_resolves_shared_items():
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    definition = (
        f"<pivotCacheDefinition {ns}><cacheFields count='3'>"
        "<cacheField name='Source'><sharedItems><s v='All Companies'/><s v='Fire Risk Scores'/></sharedItems></cacheField>"
        "<cacheField name='ZIP Code'><sharedItems containsNumber='1'/></cacheField>"
        "<cacheField name='Avg Fire Risk '><sharedItems containsBlank='1'/></cacheField>"
        "</cacheFields></pivotCacheDefinition>"
    )
    records = (
        f"<pivotCacheRecords {ns}>"
        "<r><x v='1'/><n v='95945'/><n v='2.5'/></r>"
        "<r><x v='0'/><n v='95949'/><m/></r>"
        "</pivotCacheRecords>"
    )
    df = w.read_pivot_cache(_xlsx(definition, records))
    assert list(df.columns) == ["Source", "ZIP Code", "Avg Fire Risk"]
    assert df["Source"].tolist() == ["Fire Risk Scores", "All Companies"]
    assert df["ZIP Code"].tolist() == [95945.0, 95949.0]
    assert df.loc[0, "Avg Fire Risk"] == 2.5 and pd.isna(df.loc[1, "Avg Fire Risk"])


# ---- committed outputs


def test_committed_wildfire_zip_one_row_per_zcta():
    z = pd.read_csv(ROOT / "data" / "wildfire_zip.csv", dtype={"zcta": str})
    assert z["zcta"].is_unique
    assert z["zcta"].str.fullmatch(r"\d{5}").all()
    assert set(w.CORE + w.FRINGE) <= set(z["zcta"])
    assert set(z["burned"]) <= {0, 1} and set(z["spillover_candidate"]) <= {0, 1}
    assert (z.loc[z["burned"] == 1, "burned_event_ids"].str.len() > 0).all()


def test_committed_register_is_valid_and_covers_required_events():
    reg = w.read_register(ROOT / "data" / "event_register.csv")
    ids = set(reg["event_id"])
    years = reg.loc[reg["event_type"] == "nonrenewal_moratorium", "date_start"].dt.year
    assert set(range(2019, 2025)) <= set(years)
    for required in ("FIRE_2017_LOBO", "FIRE_2020_JONES", "FIRE_2021_RIVER", "FIRE_2018_CAMP",
                     "INS_2023_05_27_STATE_FARM_NEW_BUSINESS", "RULE_2023_09_21_SIS", "RULE_2024_12_13_CAT_MODEL"):
        assert required in ids, required
    assert set(reg["event_type"]) == w.EVENT_TYPES


def test_raw_wildfire_manifest_matches_files():
    assert verify_manifest(ROOT / "data" / "raw" / "wildfire") == []
