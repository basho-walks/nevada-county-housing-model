from pathlib import Path

import pandas as pd

DICT = Path(__file__).resolve().parents[1] / "docs" / "data_dictionary.csv"
LINEAGE = {"key", "observed", "crosswalked", "derived", "county_control", "modeled", "observed or crosswalked"}


def test_data_dictionary_shape():
    d = pd.read_csv(DICT, dtype=str, keep_default_na=False)
    assert list(d.columns) == ["table", "column", "type", "unit", "source", "geography", "lineage", "missing_flag"]
    assert (d != "").all().all()
    assert not d.duplicated(["table", "column"]).any()
    assert set(d["lineage"]) <= LINEAGE


def test_data_dictionary_has_target_tables_with_full_key():
    d = pd.read_csv(DICT, dtype=str, keep_default_na=False)
    for table in ["insurance_zip_year", "housing_zip_year", "panel"]:
        cols = set(d.loc[d["table"] == table, "column"])
        assert {"zcta", "year", "policy_form"} <= cols, table
    # wildfire_zip is a ZCTA cross-section with vintage columns, not a ZCTA-year table.
    assert "zcta" in set(d.loc[d["table"] == "wildfire_zip", "column"])


def test_county_columns_are_never_zip_observations():
    d = pd.read_csv(DICT, dtype=str, keep_default_na=False)
    county = d[d["column"].str.startswith("county_") & (d["table"] == "panel")]
    value_cols = county[(county["column"] != "county_fips") & ~county["column"].str.endswith("_missing")]
    assert len(value_cols) > 0
    assert (value_cols["lineage"] == "county_control").all()
