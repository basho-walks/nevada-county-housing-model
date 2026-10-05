import json

import pandas as pd
import pytest

from ingest.keys import (
    DuplicateKeyError,
    canonical_keys,
    map_zip_to_zcta,
    normalize_policy_form,
    normalize_zcta,
    normalize_zip,
)
from ingest.manifest import verify_manifest, write_manifest


@pytest.mark.parametrize(
    "raw, expected",
    [
        (95945, "95945"),
        ("95945", "95945"),
        (" 95945 ", "95945"),
        (95945.0, "95945"),
        ("95945.0", "95945"),
        ("95945-1234", "95945"),
        (2134, "02134"),
        ("2134", "02134"),
        (501, "00501"),
    ],
)
def test_normalize_zip_zero_pads(raw, expected):
    assert normalize_zip(raw) == expected


@pytest.mark.parametrize("raw", [None, float("nan"), "", "9594A", "959451", 95945.5, True, "95945-12"])
def test_normalize_zip_rejects_bad_values(raw):
    with pytest.raises(ValueError):
        normalize_zip(raw)


@pytest.mark.parametrize("raw", ["860Z200US95945", "ZCTA5 95945", "8600000US95945", 95945])
def test_normalize_zcta_strips_census_prefixes(raw):
    assert normalize_zcta(raw) == "95945"


def test_zcta_rejects_zip_plus_four():
    with pytest.raises(ValueError):
        normalize_zcta("95945-1234")


def test_policy_form_excludes_undefined_fp():
    assert normalize_policy_form(" ho ") == "HO"
    with pytest.raises(ValueError):
        normalize_policy_form("FP")


def test_identity_mapping_reports_unmatched_zips():
    df = pd.DataFrame({"zip": [95945, "95712", "95959"], "v": [1, 2, 3]})
    matched, unmatched = map_zip_to_zcta(df, zctas={"95945", "95959"})
    assert matched["zcta"].tolist() == ["95945", "95959"]
    assert matched["zcta_weight"].tolist() == [1.0, 1.0]
    assert set(matched["zcta_method"]) == {"identity"}
    assert unmatched["zip"].tolist() == ["95712"]
    assert list(unmatched.columns) == ["zip", "v"]


def test_crosswalk_mapping_is_many_to_many_and_carries_weights(crosswalk):
    df = pd.DataFrame({"zip": ["95945", "95712", "96162"], "v": [10, 20, 30]})
    matched, unmatched = map_zip_to_zcta(df, crosswalk=crosswalk)
    assert len(matched) == 3
    rows_95945 = matched[matched["zip"] == "95945"]
    assert sorted(rows_95945["zcta"]) == ["95945", "95949"]
    assert rows_95945["zcta_weight"].sum() == pytest.approx(1.0)
    # The value is carried unweighted; callers apply the weight explicitly.
    assert set(rows_95945["v"]) == {10}
    assert unmatched["zip"].tolist() == ["96162"]


def test_crosswalk_with_duplicate_pairs_is_rejected(crosswalk):
    bad = pd.concat([crosswalk, crosswalk.iloc[[0]]])
    with pytest.raises(DuplicateKeyError):
        map_zip_to_zcta(pd.DataFrame({"zip": ["95945"]}), crosswalk=bad)


def test_map_requires_exactly_one_source(crosswalk):
    df = pd.DataFrame({"zip": ["95945"]})
    with pytest.raises(ValueError):
        map_zip_to_zcta(df)
    with pytest.raises(ValueError):
        map_zip_to_zcta(df, crosswalk=crosswalk, zctas={"95945"})


def test_canonical_keys_normalizes():
    df = pd.DataFrame({"zcta": [95945, "860Z200US95959"], "year": [2020, "2021"], "policy_form": ["ho", "ALL"]})
    out = canonical_keys(df)
    assert out["zcta"].tolist() == ["95945", "95959"]
    assert out["year"].tolist() == [2020, 2021]
    assert out["policy_form"].tolist() == ["HO", "ALL"]


def test_canonical_keys_rejects_duplicates_after_normalization():
    df = pd.DataFrame({"zcta": [95945, "95945"], "year": [2020, 2020], "policy_form": ["HO", "ho"]})
    with pytest.raises(DuplicateKeyError):
        canonical_keys(df)


def test_canonical_keys_allows_same_zcta_year_across_forms():
    df = pd.DataFrame({"zcta": ["95945", "95945"], "year": [2020, 2020], "policy_form": ["HO", "DO"]})
    assert len(canonical_keys(df)) == 2


def test_canonical_keys_requires_key_columns():
    with pytest.raises(KeyError):
        canonical_keys(pd.DataFrame({"zcta": ["95945"], "year": [2020]}))


def test_manifest_round_trip(tmp_path):
    snap = tmp_path / "a.csv"
    snap.write_bytes(b"zip,v\n95945,1\n")
    path = write_manifest(snap, "https://example.invalid/a.csv", "public domain", retrieved_at="2026-10-05T00:00:00+00:00")
    entry = json.loads(path.read_text())["a.csv"]
    assert entry["size"] == len(b"zip,v\n95945,1\n")
    assert len(entry["sha256"]) == 64
    assert verify_manifest(tmp_path) == []
    snap.write_bytes(b"changed")
    assert verify_manifest(tmp_path) == ["a.csv"]
