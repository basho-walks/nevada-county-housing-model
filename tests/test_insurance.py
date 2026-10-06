import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ingest import insurance as ins
from ingest.keys import DuplicateKeyError, canonical_keys

FIX = Path(__file__).parent / "fixtures" / "insurance"
S2 = FIX / "s2_sb824_pivot_2020_2021.xlsx"
S3_SPLIT = FIX / "s3_nonrenewal_split_2015_2021.xlsx"
S3_TOTAL = FIX / "s3_nonrenewal_total_2020_2023.xlsx"
S6 = FIX / "s6_fair_plan_share_2022.pdf"


def write_xlsx(path: Path, rows: list[list]) -> Path:
    """Minimal one-sheet xlsx with inline strings."""
    def cell(ref, v):
        if isinstance(v, (int, float)):
            return f'<c r="{ref}"><v>{v}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{v}</t></is></c>'

    body = "".join(
        f'<row r="{i}">' + "".join(cell(f"{chr(65 + j)}{i}", v) for j, v in enumerate(r)) + "</row>"
        for i, r in enumerate(rows, 1)
    )
    sheet = f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{body}</sheetData></worksheet>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return path


@pytest.fixture(scope="module")
def parsed():
    return {
        "s2": ins.parse_s2_sb824_pivot(S2),
        "s3": [ins.parse_s3_nonrenewal_split(S3_SPLIT), ins.parse_s3_nonrenewal_total(S3_TOTAL)],
        "s6": ins.parse_s6_fair_plan_share(S6),
    }


@pytest.fixture(scope="module")
def panel(parsed):
    return ins.build_panel(parsed["s2"], parsed["s3"], parsed["s6"])


def row(df, zcta, year, form):
    hit = df[(df["zcta"] == zcta) & (df["year"] == year) & (df["policy_form"] == form)]
    assert len(hit) == 1
    return hit.iloc[0]


# ---- layouts and parsers


@pytest.mark.parametrize(
    "path, layout",
    [(S2, "s2_sb824_pivot"), (S3_SPLIT, "s3_nonrenewal_split"), (S3_TOTAL, "s3_nonrenewal_total"), (S6, "s6_fair_plan_share")],
)
def test_detect_layout(path, layout):
    assert ins.detect_layout(path) == layout


def test_unknown_layout_is_rejected(tmp_path):
    p = write_xlsx(tmp_path / "x.xlsx", [["County", "ZIP Code", "Year", "Policies"], ["Nevada", "95945", "2020", "1"]])
    with pytest.raises(ins.LayoutError):
        ins.detect_layout(p)


def test_parse_s3_split_layout():
    df = ins.parse_s3_nonrenewal_split(S3_SPLIT)
    r = df[(df["zip"] == "95945") & (df["year"] == 2019)].iloc[0]
    assert (r["policies_new"], r["policies_renewed"]) == (2230, 6123)
    assert (r["nonrenewed_insured"], r["nonrenewed_insurer"], r["nonrenewed_total"]) == (869, 1130, 1999)
    assert "96161" not in set(df["zip"])  # Truckee is outside the target ZIPs


def test_parse_s3_total_layout_leaves_split_missing_not_zero():
    df = ins.parse_s3_nonrenewal_total(S3_TOTAL)
    r = df[(df["zip"] == "95945") & (df["year"] == 2023)].iloc[0]
    assert (r["policies_new"], r["policies_renewed"], r["nonrenewed_total"]) == (373, 5009, 1266)
    assert np.isnan(r["nonrenewed_insurer"]) and np.isnan(r["nonrenewed_insured"])
    assert set(df["county"].dropna()) == {"Nevada"}  # space padding stripped


def test_parse_s3_keeps_blank_county_placeholder_zip_when_asked():
    df = ins.parse_s3_nonrenewal_total(S3_TOTAL, zips=None)
    assert df.loc[df["zip"] == "90000", "county"].isna().all()


def test_parse_s2_keeps_all_companies_and_known_forms_only():
    recs = list(ins.pivot_records(S2))
    assert {r["Source"] for r in recs} > {"All Companies"}
    assert "FP" in {r["Policy Type"] for r in recs}
    df = ins.parse_s2_sb824_pivot(S2)
    assert set(df["s2_code"]) == {"HO", "DO", "DT"}
    r = df[(df["zip"] == "95945") & (df["year"] == 2021) & (df["s2_code"] == "HO")].iloc[0]
    assert (r["earned_premium"], r["earned_exposure"]) == (8595635, 5593)
    assert r["avg_coverage_a"] == pytest.approx(444166.77)


def test_parse_s6_layout():
    df = ins.parse_s6_fair_plan_share(S6, zips=None)
    assert set(df["year"]) == {2022}
    r = df[df["zip"] == "95949"].iloc[0]  # this row is split over two content streams in the fixture
    assert (r["voluntary_dwelling_units"], r["fair_plan_dwelling_units"]) == (4739, 3184)
    zero = df[df["zip"] == "90013"].iloc[0]
    assert zero["fair_plan_dwelling_units"] == 0  # "-" is a published zero


def test_encrypted_pdf_is_rejected(tmp_path):
    p = tmp_path / "e.pdf"
    p.write_bytes(S6.read_bytes().replace(b"/Root 1 0 R", b"/Root 1 0 R/Encrypt 9 0 R"))
    with pytest.raises(ins.LayoutError):
        ins.pdf_lines(p)


# ---- panel


def test_panel_key_and_forms(panel):
    out, _ = panel
    assert not out.duplicated(list(ins.KEY)).any()
    assert set(out["policy_form"]) <= set(ins.POLICY_FORMS)
    assert list(out.columns) == ins.COLUMNS


def test_panel_passes_canonical_keys(panel):
    out, _ = panel
    assert canonical_keys(out)["policy_form"].tolist() == out["policy_form"].tolist()


def test_dwelling_fire_combines_owner_and_tenant(panel):
    out, _ = panel
    r = row(out, "95945", 2020, "dwelling_fire")
    assert r["exposures"] == 1385 + 1806
    assert r["earned_premium"] == 3225962 + 1715192
    assert r["avg_premium_per_exposure"] == pytest.approx((3225962 + 1715192) / (1385 + 1806))
    assert r["owner_occupied_share"] == pytest.approx(1385 / (1385 + 1806))
    assert r["avg_coverage_a"] == pytest.approx((444435.86 * 1385 + 316762.88 * 1806) / (1385 + 1806))


def test_homeowners_row_carries_premium_and_counts_separately(panel):
    out, _ = panel
    r = row(out, "95945", 2021, "admitted_homeowners")
    assert r["exposure_unit"] == "earned_house_years"
    assert r["premium_basis"] == "earned"
    assert np.isnan(r["written_premium"])  # S2 publishes earned premium only
    assert r["premium_per_1000_coverage_a"] == pytest.approx(8595635 / 5593 / 444166.77 * 1000)
    assert r["form_share_of_admitted"] == pytest.approx(5593 / (5593 + 1559 + 1744))
    assert r["coverage_basis"] == "start_of_year" and r["coverage_def_change"]
    # 2020-2021 come from the 2020-2023 S3 vintage, not the superseded 2015-2021 file.
    assert (r["policies_new"], r["policies_renewed"], r["s3_vintage"]) == (605, 6033, "2020-2023")
    assert r["nonrenewal_rate"] == pytest.approx(862 / (6033 + 862))
    assert np.isnan(r["nonrenewed_insurer"])
    assert "S2" in r["sources"] and "S3" in r["sources"]


def test_s3_only_years_have_no_premium(panel):
    out, _ = panel
    r = row(out, "95945", 2019, "admitted_homeowners")
    assert np.isnan(r["earned_premium"]) and np.isnan(r["exposures"])
    assert r["combined_premium_unavailable"]
    assert row(out, "95945", 2020, "admitted_homeowners")["series_break"]


def test_fair_plan_share_uses_same_source_denominator(panel):
    out, _ = panel
    r = row(out, "95949", 2022, "fair_plan")
    assert r["fair_plan_share"] == pytest.approx(3184 / (3184 + 4739))
    assert r["exposure_unit"] == "dwelling_units"
    assert "S6 voluntary+FAIR Plan" in r["denominator_source"]
    assert r["combined_premium_unavailable"]
    assert out.loc[out["policy_form"] != "fair_plan", "fair_plan_share"].isna().all()


def test_absent_s6_zip_is_flagged_suppressed(parsed):
    s6 = parsed["s6"][parsed["s6"]["zip"] != "95945"]
    out, _ = ins.build_panel(parsed["s2"], parsed["s3"], s6)
    r = row(out, "95945", 2022, "fair_plan")
    assert r["suppressed"]
    assert np.isnan(r["fair_plan_share"]) and np.isnan(r["exposures"])


def test_zip_without_zcta_goes_to_unmatched(panel):
    out, unmatched = panel
    assert "95712" not in set(out["zip_source"])
    assert set(unmatched["zip"]) == {"95712"}


def test_mix_shift_flag(panel):
    out, _ = panel
    # 95949 DO exposure grows from 2,082 to 2,457 while DT falls; the form shares move less than the threshold.
    assert not row(out, "95949", 2021, "admitted_homeowners")["mix_shift"]
    s2 = ins.parse_s2_sb824_pivot(S2)
    s2.loc[(s2["zip"] == "95949") & (s2["year"] == 2021) & (s2["s2_code"] == "DO"), "earned_exposure"] = 9000
    out2, _ = ins.build_panel(s2, [], None)
    assert row(out2, "95949", 2021, "dwelling_fire")["mix_shift"]


def test_unpublished_columns_are_nan_not_zero(panel):
    out, _ = panel
    fp = out[out["policy_form"] == "fair_plan"]
    for col in ("earned_premium", "written_premium", "avg_premium_per_exposure", "nonrenewal_rate", "policies_new"):
        assert fp[col].isna().all(), col


# ---- denominators


def test_safe_rate_is_nan_without_valid_denominator():
    out = ins.safe_rate(pd.Series([1.0, 1.0, 1.0, np.nan]), pd.Series([np.nan, 0.0, 4.0, 2.0]))
    assert np.isnan(out[0]) and np.isnan(out[1]) and out[2] == 0.25 and np.isnan(out[3])


def test_nonrenewal_rate_nan_when_renewed_missing():
    s3 = pd.DataFrame({"county": ["Nevada"], "zip": ["95945"], "year": [2022], "policies_new": [1.0],
                       "policies_renewed": [np.nan], "nonrenewed_total": [5.0], "nonrenewed_insured": [np.nan],
                       "nonrenewed_insurer": [np.nan], "s3_vintage": ["2020-2023"]})
    out, _ = ins.build_panel(pd.DataFrame(columns=["zip", "year", "s2_code"]), [s3], None)
    r = row(out, "95945", 2022, "admitted_homeowners")
    assert np.isnan(r["nonrenewal_rate"])
    assert "nonrenewal_rate" not in r["denominator_source"]


def test_fair_plan_share_nan_when_voluntary_missing(parsed):
    s6 = parsed["s6"].copy()
    s6.loc[s6["zip"] == "95946", "voluntary_dwelling_units"] = np.nan
    s6.loc[s6["zip"] == "95946", "published_share"] = np.nan
    out, _ = ins.build_panel(parsed["s2"], parsed["s3"], s6)
    assert np.isnan(row(out, "95946", 2022, "fair_plan")["fair_plan_share"])


def test_fair_plan_and_supplemental_are_never_summed(panel):
    out, _ = panel
    with pytest.raises(ValueError):
        ins.total_policies(out, ["fair_plan", "supplemental"])
    with pytest.raises(ins.UnitError):
        ins.total_policies(out, ["admitted_homeowners", "fair_plan"])  # house-years vs dwelling units
    tot = ins.total_policies(out, ["admitted_homeowners", "dwelling_fire"])
    assert tot.loc[("95945", 2020)] == 5484 + 1385 + 1806


# ---- units


def test_premiums_in_thousands_are_rejected(parsed):
    s2 = parsed["s2"].copy()
    s2["earned_premium"] = s2["earned_premium"] / 1000
    with pytest.raises(ins.UnitError):
        ins.build_panel(s2, [], None)


def test_s6_share_must_match_published_percent(parsed):
    s6 = parsed["s6"].copy()
    s6["fair_plan_dwelling_units"] = s6["fair_plan_dwelling_units"] * 1000
    with pytest.raises(ins.UnitError):
        ins.build_panel(parsed["s2"], parsed["s3"], s6)


# ---- duplicates


def test_duplicate_rows_in_s3_file_are_rejected(tmp_path):
    rows = [["County", "ZIP Code", "Year", "New", "Renewed", "Non-Renewed"],
            ["Nevada", 95945, 2022, 1, 2, 3], ["Nevada", "95945", 2022, 4, 5, 6]]
    p = write_xlsx(tmp_path / "dup.xlsx", rows)
    assert ins.detect_layout(p) == "s3_nonrenewal_total"
    with pytest.raises(DuplicateKeyError):
        ins.parse_s3_nonrenewal_total(p)


def test_duplicate_s2_rows_are_rejected(parsed):
    s2 = pd.concat([parsed["s2"], parsed["s2"].iloc[[0]]])
    with pytest.raises(DuplicateKeyError):
        ins.build_panel(s2, [], None)


def test_duplicate_years_across_s3_vintages_are_rejected(parsed):
    split = parsed["s3"][0]  # the same vintage passed twice would supply 2018-2019 twice
    with pytest.raises(DuplicateKeyError):
        ins.build_panel(parsed["s2"], [split, split], None)


# ---- retrieval


def test_fetch_is_offline_when_manifest_verifies(tmp_path, monkeypatch):
    monkeypatch.setattr(ins, "RAW", tmp_path)
    src = ins.Source("S3", "cdi_s3_nonrenewal_zip", "2020-2023", "https://example.invalid/a.xlsx", "0" * 64)
    calls = []

    class Resp:
        content = S3_TOTAL.read_bytes()

        def raise_for_status(self):
            pass

    def fake_get(url, **kw):
        calls.append(url)
        return Resp()

    monkeypatch.setattr("requests.get", fake_get)
    path = ins.fetch(src)
    # The audited hash does not match, so the bytes are kept as a new vintage, never over the audited one.
    assert path.parent.name.startswith("2020-2023-")
    manifest = json.loads((path.parent / "manifest.json").read_text())
    assert manifest["a.xlsx"]["sha256"] == ins.sha256(S3_TOTAL)
    good = ins.Source("S3", "cdi_s3_nonrenewal_zip", "2020-2023", "https://example.invalid/a.xlsx", ins.sha256(S3_TOTAL))
    p2 = ins.fetch(good)
    assert p2.parent.name == "2020-2023"
    assert ins.fetch(good) == p2
    assert len(calls) == 2  # the third call verified the manifest and did not download


def test_restricted_sources_are_never_fetched():
    restricted = [s for s in ins.SOURCES if s.restricted]
    assert {s.sid for s in restricted} == {"S9", "S10"}
    with pytest.raises(PermissionError):
        ins.fetch(restricted[0])
    text = ins.restricted_instructions()
    assert all(s.url in text for s in restricted)
    assert "data/raw/restricted/" in text


def test_restricted_directory_is_gitignored():
    gitignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text().splitlines()
    assert "data/raw/restricted/" in gitignore
