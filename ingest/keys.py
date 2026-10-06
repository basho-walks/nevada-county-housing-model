"""ZIP/ZCTA normalization and the canonical panel key (zcta, year, policy_form).

A USPS ZIP is not a ZCTA: mapping is explicit and every unmatched ZIP is returned, never dropped.
"""

from __future__ import annotations

import re

import pandas as pd

KEY = ("zcta", "year", "policy_form")

# CDI SB 824 form codes (docs/sources.md S2). FP is excluded until CDI defines it; ALL marks non-policy tables.
CDI_FORMS = frozenset({"HO", "RT", "CO", "MH", "DO", "DT", "ALL"})
# Insurance product names from issue #3 (ingest/insurance.py), kept distinct from the form codes.
PRODUCT_FORMS = frozenset({"admitted_homeowners", "dwelling_fire", "fair_plan", "supplemental"})
POLICY_FORMS = CDI_FORMS | PRODUCT_FORMS

YEAR_MIN, YEAR_MAX = 1990, 2100

_PREFIXES = ("860Z200US", "8600000US", "ZCTA5 ")
_DIGITS = re.compile(r"^\d{1,5}$")


class DuplicateKeyError(ValueError):
    pass


def _code5(value, kind: str) -> str:
    if value is None or isinstance(value, bool) or (isinstance(value, float) and pd.isna(value)):
        raise ValueError(f"missing or invalid {kind}: {value!r}")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"invalid {kind}: {value!r}")
        value = int(value)
    s = str(value).strip()
    for p in _PREFIXES:
        s = s.removeprefix(p)
    if kind == "ZIP" and re.fullmatch(r"\d{5}-\d{4}", s):
        s = s[:5]
    s = s.removesuffix(".0")  # spreadsheet readers turn ZIP columns into floats
    if not _DIGITS.match(s):
        raise ValueError(f"invalid {kind}: {value!r}")
    return s.zfill(5)


def normalize_zip(value) -> str:
    """Return a 5-digit USPS ZIP string; accepts ints, floats, ZIP+4 and padded strings."""
    return _code5(value, "ZIP")


def normalize_zcta(value) -> str:
    """Return a 5-digit ZCTA string; strips Census GEO_ID and NAMELSAD prefixes."""
    return _code5(value, "ZCTA")


def normalize_policy_form(value) -> str:
    s = str(value).strip()
    if s.upper() in CDI_FORMS:
        return s.upper()
    if s.lower() in PRODUCT_FORMS:
        return s.lower()
    raise ValueError(f"unknown policy_form: {value!r}")


def normalize_year(value) -> int:
    y = int(value)
    if y != float(value) or not YEAR_MIN <= y <= YEAR_MAX:
        raise ValueError(f"invalid year: {value!r}")
    return y


def map_zip_to_zcta(
    df: pd.DataFrame,
    *,
    crosswalk: pd.DataFrame | None = None,
    zctas: set[str] | None = None,
    zip_col: str = "zip",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Attach zcta, xw_weight and zcta_method to ZIP rows; return (matched, unmatched).

    With `crosswalk` (columns zip, zcta, weight) one ZIP can map to several ZCTAs: rows are repeated
    and the weight is carried, not applied. Without it, a ZIP maps only to the same-coded ZCTA in `zctas`.
    """
    if (crosswalk is None) == (zctas is None):
        raise ValueError("pass exactly one of crosswalk or zctas")
    out = df.copy()
    out[zip_col] = out[zip_col].map(normalize_zip)
    if crosswalk is not None:
        xw = crosswalk[["zip", "zcta", "weight"]].copy()
        xw["zip"] = xw["zip"].map(normalize_zip)
        xw["zcta"] = xw["zcta"].map(normalize_zcta)
        if xw.duplicated(["zip", "zcta"]).any():
            raise DuplicateKeyError("crosswalk has duplicate (zip, zcta) rows")
        xw = xw.rename(columns={"zip": zip_col, "weight": "xw_weight"})
        merged = out.merge(xw, on=zip_col, how="left")
        merged["zcta_method"] = "crosswalk"
    else:
        known = {normalize_zcta(z) for z in zctas}
        merged = out.copy()
        merged["zcta"] = merged[zip_col].where(merged[zip_col].isin(known))
        merged["xw_weight"] = merged["zcta"].notna().astype(float)
        merged["zcta_method"] = "identity"
    hit = merged["zcta"].notna()
    unmatched = merged.loc[~hit, list(out.columns)].reset_index(drop=True)
    return merged.loc[hit].reset_index(drop=True), unmatched


def assert_unique_keys(df: pd.DataFrame, key: tuple[str, ...] = KEY) -> None:
    dup = df.duplicated(list(key), keep=False)
    if dup.any():
        sample = df.loc[dup, list(key)].drop_duplicates().head(5).to_dict("records")
        raise DuplicateKeyError(f"{int(dup.sum())} rows share a key; first: {sample}")


def canonical_keys(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with normalized (zcta, year, policy_form); raise on duplicate keys."""
    missing = [c for c in KEY if c not in df.columns]
    if missing:
        raise KeyError(f"missing key columns: {missing}")
    out = df.copy()
    out["zcta"] = out["zcta"].map(normalize_zcta)
    out["year"] = out["year"].map(normalize_year)
    out["policy_form"] = out["policy_form"].map(normalize_policy_form)
    assert_unique_keys(out)
    return out
