"""Wildfire exposure by 2020 ZCTA and a dated register of fire, insurer and rule events (#5).

Only the fetch_* functions use the network; build() reads data/raw/wildfire/ and is what tests run.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from ingest.keys import map_zip_to_zcta, normalize_zcta, normalize_zip
from ingest.manifest import write_manifest

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "wildfire"
OUT_ZIP = ROOT / "data" / "wildfire_zip.csv"
OUT_REGISTER = ROOT / "data" / "event_register.csv"
UNMATCHED = ROOT / "data" / "interim" / "unmatched" / "wildfire_zip.csv"

REGION_COUNTIES = {"06057": "Nevada", "06061": "Placer", "06091": "Sierra", "06115": "Yuba"}
# Tiers from docs/schema.md section 2.
CORE = ("95945", "95949", "95959", "95946", "95975", "95986")
FRINGE = ("95960", "95977", "95602")
EXCLUDED_EAST = ("96160", "96161", "96162", "95724", "95728", "96111")

EVENT_TYPES = frozenset({"fire", "insurer_withdrawal", "nonrenewal_moratorium", "rule_change", "fair_plan_change"})
DATE_PRECISION = frozenset({"day", "week", "month", "quarter", "year"})
REGISTER_COLUMNS = [
    "event_id",
    "event_type",
    "date_start",
    "date_end",
    "date_uncertainty",
    "geography",
    "zctas_affected",
    "source_url",
    "notes",
]
CURATED_COLUMNS = [c if c != "zctas_affected" else "zips_listed" for c in REGISTER_COLUMNS]

S2_URL = "https://www.insurance.ca.gov/01-consumers/200-wrr/upload/Residential-Property-Coverage-Amounts-Wildfire-Risk-and-Losses.xlsx"
S14_URL = "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_county20_natl.txt"
DINS_URL = "https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/POSTFIRE_MASTER_DATA_SHARE/FeatureServer/0"
DINS_PAGE = "https://data.ca.gov/dataset/cal-fire-damage-inspection-dins-data"
TIGER_ZCTA = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/PUMA_TAD_TAZ_UGA_ZCTA/MapServer/7"
CALFIRE_URL = "https://incidents.fire.ca.gov/imapdata/mapdataall.csv"

S2_FORMS = ("HO", "RT", "CO", "MH", "DO", "DT")  # FP is undefined in the S2 methodology (docs/schema.md section 6)
S2_VINTAGES = {2018: 1, 2023: 0}  # year -> pre-treatment label
DAMAGED = ("Destroyed", "Major")
LICENSE_CDI = "CDI: public domain unless otherwise indicated (insurance.ca.gov privacy policy, Ownership)"
LICENSE_CENSUS = "US Census Bureau: public domain"
LICENSE_CALFIRE = "CAL FIRE: Creative Commons Attribution (data.ca.gov listing)"


# ---------------------------------------------------------------- register


def parse_register(df: pd.DataFrame) -> pd.DataFrame:
    """Validate an event register and return it with date_start/date_end as datetime64 (NaT when open)."""
    if list(df.columns) != REGISTER_COLUMNS:
        raise ValueError(f"register columns {list(df.columns)} != {REGISTER_COLUMNS}")
    out = df.copy()
    for col in ("event_id", "event_type", "date_start", "date_uncertainty", "geography", "source_url"):
        blank = out[col].isna() | (out[col].astype(str).str.strip() == "")
        if blank.any():
            raise ValueError(f"blank {col} in rows {out.index[blank].tolist()}")
    if out["event_id"].duplicated().any():
        raise ValueError(f"duplicate event_id: {out.loc[out['event_id'].duplicated(), 'event_id'].tolist()}")
    bad = ~out["event_type"].isin(EVENT_TYPES)
    if bad.any():
        raise ValueError(f"unknown event_type: {out.loc[bad, 'event_type'].tolist()}")
    bad = ~out["date_uncertainty"].isin(DATE_PRECISION)
    if bad.any():
        raise ValueError(f"unknown date_uncertainty: {out.loc[bad, 'date_uncertainty'].tolist()}")
    bad = ~out["source_url"].astype(str).str.match(r"^https?://")
    if bad.any():
        raise ValueError(f"source_url is not a URL for: {out.loc[bad, 'event_id'].tolist()}")
    out["date_start"] = pd.to_datetime(out["date_start"], format="%Y-%m-%d", errors="raise")
    end = out["date_end"].where(out["date_end"].notna() & (out["date_end"].astype(str).str.strip() != ""))
    out["date_end"] = pd.to_datetime(end, format="%Y-%m-%d", errors="raise")
    early = out["date_end"].notna() & (out["date_end"] < out["date_start"])
    if early.any():
        raise ValueError(f"date_end before date_start for: {out.loc[early, 'event_id'].tolist()}")
    for ids in out["zctas_affected"].dropna().astype(str):
        for z in filter(None, ids.split(";")):
            if normalize_zcta(z) != z:
                raise ValueError(f"zctas_affected must hold 5-digit codes separated by ';': {ids!r}")
    return out


def read_register(path: Path) -> pd.DataFrame:
    return parse_register(pd.read_csv(path, dtype=str, keep_default_na=False))


# ---------------------------------------------------------------- CDI S2 pivot cache


def read_pivot_cache(xlsx: Path | bytes) -> pd.DataFrame:
    """Return the records of the first pivot cache in an xlsx; S2 stores its data only there (docs/sources.md S2)."""
    zf = zipfile.ZipFile(io.BytesIO(xlsx) if isinstance(xlsx, bytes) else xlsx)
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    definition = ET.fromstring(zf.read("xl/pivotCache/pivotCacheDefinition1.xml"))
    names, shared = [], []
    for field in definition.find("m:cacheFields", ns):
        names.append(field.get("name").strip())
        items = field.find("m:sharedItems", ns)
        shared.append([_cell(i) for i in items] if items is not None and len(items) else None)
    rows = []
    tag_r = f"{{{ns['m']}}}r"
    with zf.open("xl/pivotCache/pivotCacheRecords1.xml") as f:
        for _, el in ET.iterparse(f):
            if el.tag != tag_r:
                continue
            row = []
            for i, c in enumerate(el):
                if c.tag.endswith("}x"):
                    row.append(shared[i][int(c.get("v"))])
                else:
                    row.append(_cell(c))
            rows.append(row)
            el.clear()
    return pd.DataFrame(rows, columns=names)


def _cell(el):
    kind = el.tag.rsplit("}", 1)[-1]
    if kind == "m":
        return None
    v = el.get("v")
    return float(v) if kind == "n" else v


def s2_extract(cache: pd.DataFrame, zips: set[str], counties: set[str]) -> pd.DataFrame:
    """Keep the Fire Risk Scores slice for region ZIPs, with calendar years as 4 digits."""
    df = cache[cache["Source"] == "Fire Risk Scores"].copy()
    df["zip"] = df["ZIP Code"].map(normalize_zip)
    df = df[df["zip"].isin(zips) | df["County"].isin(counties)]
    out = pd.DataFrame(
        {
            "year": (2000 + df["Calendar Year"].astype(int)).values,
            "policy_form": df["Policy Type"].values,
            "zip": df["zip"].values,
            "county": df["County"].values,
            "avg_fire_risk": df["Avg Fire Risk"].values,
            "count_n": df["Count N"].values,
            "count_l": df["Count L"].values,
            "count_m": df["Count M"].values,
            "count_h": df["Count H"].values,
            "count_e": df["Count E"].values,
            "earned_exposure": df["Earned Exp"].values,
        }
    )
    return out.sort_values(["zip", "year", "policy_form"]).reset_index(drop=True)


def s2_risk_by_zip_year(extract: pd.DataFrame) -> pd.DataFrame:
    """ZIP-year risk mix: count-weighted mean score and high+extreme share over S2_FORMS."""
    df = extract[extract["policy_form"].isin(S2_FORMS)].copy()
    counts = ["count_n", "count_l", "count_m", "count_h", "count_e"]
    df[counts] = df[counts].fillna(0.0)
    df["scored"] = df[counts].sum(axis=1)
    df = df[df["avg_fire_risk"].notna() & (df["scored"] > 0)]
    df["risk_x_w"] = df["avg_fire_risk"] * df["scored"]
    df["he"] = df["count_h"] + df["count_e"]
    g = df.groupby(["zip", "year"], as_index=False)[["risk_x_w", "scored", "he"]].sum()
    g["avg_fire_risk"] = g["risk_x_w"] / g["scored"]
    g["high_extreme_share"] = g["he"] / g["scored"]
    return g[["zip", "year", "avg_fire_risk", "high_extreme_share", "scored"]]


# ---------------------------------------------------------------- build


def tiers(universe: pd.DataFrame) -> pd.Series:
    def tier(z):
        if z in CORE:
            return "core"
        if z in FRINGE:
            return "fringe"
        if z in EXCLUDED_EAST:
            return "excluded_east"
        return "region"

    return universe["zcta"].map(tier)


def fire_events(dins: pd.DataFrame, incidents: pd.DataFrame) -> pd.DataFrame:
    """One register row per regional fire that damaged at least one structure, dated from the CAL FIRE incident list."""
    dmg = dins[dins["damage"].str.startswith(DAMAGED)]
    rows = []
    for (name, start), grp in dmg.groupby(["incident_name", "incident_start"]):
        start_d = _parse_date(start, f"DINS incident {name}")
        match = _match_incident(incidents, name, start_d)
        zctas = sorted(z for z in grp["zcta"].dropna().unique() if z)
        destroyed = int(grp["damage"].str.startswith("Destroyed").sum())
        major = int(grp["damage"].str.startswith("Major").sum())
        counties = sorted(grp["county"].dropna().unique())
        note = f"DINS: {destroyed} destroyed, {major} major damage"
        if match is not None:
            ds, de, url = match["date_start"], match["date_end"], match["url"]
            note += f"; {match['acres']} acres per CAL FIRE incident list"
            if de and de < ds:
                de = ""
                note += "; extinguished date precedes start in source, dropped"
            elif not de:
                note += "; extinguished date not published"
        else:
            ds, de, url = start, "", DINS_PAGE
            note += "; not in CAL FIRE incident list, start date from DINS, end date unknown"
        no_zcta = int(grp["zcta"].isna().sum() + (grp["zcta"] == "").sum())
        if no_zcta:
            note += f"; {no_zcta} damaged records without coordinates"
        rows.append(
            {
                "event_id": f"FIRE_{ds[:4]}_{_slug(name)}",
                "event_type": "fire",
                "date_start": ds,
                "date_end": de,
                "date_uncertainty": "day",
                "geography": "; ".join(f"{c} County" for c in counties),
                "zctas_affected": ";".join(zctas),
                "source_url": url,
                "notes": note,
            }
        )
    return pd.DataFrame(rows, columns=REGISTER_COLUMNS)


def _slug(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")


def _base_name(name: str) -> str:
    name = re.sub(r"\(.*?\)", "", str(name)).lower()
    return re.sub(r"\bfire\b|[^a-z0-9 ]", "", name).strip()


def _parse_date(value: str, record: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise ValueError(f"{record}: bad or blank start date {value!r}") from e


def _match_incident(incidents: pd.DataFrame, name: str, start: date):
    base = _base_name(name)
    cand = incidents[incidents["name"].map(_base_name) == base]
    if cand.empty:
        return None
    gap = cand.apply(lambda r: abs((_parse_date(r["date_start"], f"CAL FIRE incident {r['name']}") - start).days), axis=1)
    if gap.min() > 3:
        return None
    return cand.loc[gap.idxmin()]


def curated_events(curated: pd.DataFrame, universe: set[str]) -> pd.DataFrame:
    """Map each curated event's listed USPS ZIPs to region ZCTAs by identity; unmatched ZIPs go to notes."""
    out = curated.copy()
    affected, notes = [], []
    for zips, note in zip(out["zips_listed"], out["notes"]):
        listed = [normalize_zip(z) for z in str(zips).split(";") if z.strip()]
        hit = sorted(z for z in listed if z in universe)
        miss = sorted(z for z in listed if z not in universe)
        affected.append(";".join(hit))
        if miss:
            note = f"{note}; listed ZIPs outside the region ZCTA set or without a 2020 ZCTA: {' '.join(miss)}"
        notes.append(note)
    out["zctas_affected"] = affected
    out["notes"] = notes
    return out[REGISTER_COLUMNS]


def build(raw: Path = RAW, out_zip: Path = OUT_ZIP, out_register: Path = OUT_REGISTER, unmatched_path: Path = UNMATCHED):
    region = pd.read_csv(raw / "zcta_county_region.csv", dtype={"zcta": str, "county_fips": str})
    attrs = pd.read_csv(raw / "zcta_attributes.csv", dtype={"zcta": str})
    adjacency = pd.read_csv(raw / "zcta_adjacency.csv", dtype=str)
    s2 = pd.read_csv(raw / "cdi_s2_fire_risk.csv", dtype={"zip": str, "policy_form": str, "county": str})
    dins = pd.read_csv(raw / "dins_structures.csv", dtype={"zcta": str, "zip_reported": str, "incident_start": str})
    incidents = pd.read_csv(raw / "calfire_incidents.csv", dtype=str, keep_default_na=False)
    curated = pd.read_csv(raw / "events_curated.csv", dtype=str, keep_default_na=False)
    if list(curated.columns) != CURATED_COLUMNS:
        raise ValueError(f"events_curated.csv columns {list(curated.columns)} != {CURATED_COLUMNS}")

    main = region.sort_values("county_land_share", ascending=False).drop_duplicates("zcta")
    universe = main[["zcta", "county_fips"]].rename(columns={"county_fips": "county_fips_main"})
    universe = universe.sort_values("zcta").reset_index(drop=True)
    zset = set(universe["zcta"])
    universe.insert(1, "geo_tier", tiers(universe))
    universe = universe.merge(attrs[["zcta", "hu2020"]], on="zcta", how="left")

    register = pd.concat([curated_events(curated, zset), fire_events(dins, incidents)], ignore_index=True)
    register = parse_register(register).sort_values(["date_start", "event_id"]).reset_index(drop=True)

    risk = s2_risk_by_zip_year(s2)
    matched, unmatched = map_zip_to_zcta(risk, zctas=zset)
    unmatched_path.parent.mkdir(parents=True, exist_ok=True)
    unmatched.to_csv(unmatched_path, index=False)
    print(f"wildfire: {len(unmatched)} S2 ZIP-year rows unmatched to a region ZCTA "
          f"(ZIPs: {' '.join(sorted(unmatched['zip'].unique()))}; scored policies {unmatched['scored'].sum():.0f})")
    out = universe.copy()
    for year, pre in S2_VINTAGES.items():
        r = matched[matched["year"] == year].set_index("zcta")
        for measure, col, how in (
            ("cdi_avg_fire_risk", "avg_fire_risk", "mean of Avg Fire Risk (0-4) weighted by scored policy count N+L+M+H+E"),
            ("cdi_high_extreme_share", "high_extreme_share", "(Count H + Count E) / (N+L+M+H+E)"),
        ):
            name = f"{measure}_{year}"
            out[name] = out["zcta"].map(r[col]).round(4)
            out[f"{name}_source"] = "CDI SB 824 Wildfire Risk Information data (docs/sources.md S2), Fire Risk Scores slice"
            out[f"{name}_vintage"] = f"calendar year {year}, 2024 report"
            out[f"{name}_aggregation_method"] = (
                f"ZIP-year {how}, summed over policy forms {' '.join(S2_FORMS)} (FP excluded); USPS ZIP to 2020 ZCTA by identity"
            )
            out[f"{name}_pretreatment"] = pre
    risk_cols = [f"cdi_avg_fire_risk_{y}" for y in S2_VINTAGES]
    out["cdi_risk_missing"] = out[risk_cols].isna().any(axis=1).astype(int)

    fires = register[register["event_type"] == "fire"]
    burned_ids = _ids_by_zcta(fires)
    dmg = dins[dins["damage"].str.startswith(DAMAGED) & dins["zcta"].notna()]
    out["burned"] = out["zcta"].isin(burned_ids).astype(int)
    out["burned_event_ids"] = out["zcta"].map(lambda z: ";".join(burned_ids.get(z, [])))
    destroyed = dmg[dmg["damage"].str.startswith("Destroyed")].groupby("zcta").size()
    major = dmg[dmg["damage"].str.startswith("Major")].groupby("zcta").size()
    out["structures_destroyed"] = out["zcta"].map(destroyed).fillna(0).astype(int)
    out["structures_major_damage"] = out["zcta"].map(major).fillna(0).astype(int)
    first = fires.assign(z=fires["zctas_affected"].str.split(";")).explode("z").groupby("z")["date_start"].min()
    out["first_burn_date"] = out["zcta"].map(first).dt.strftime("%Y-%m-%d").fillna("")

    # Undirected: a burned ZCTA outside the region has no adjacency rows of its own.
    edges = pd.concat([adjacency, adjacency.rename(columns={"zcta": "neighbor", "neighbor": "zcta"})])
    neighbors = edges.groupby("zcta")["neighbor"].apply(set).to_dict()
    spill = {}
    for z, events in burned_ids.items():
        for n in neighbors.get(z, ()):
            for e in events:
                if e not in burned_ids.get(n, []):
                    spill.setdefault(n, set()).add(e)
    out["spillover_candidate"] = out["zcta"].isin(spill).astype(int)
    out["spillover_event_ids"] = out["zcta"].map(lambda z: ";".join(sorted(spill.get(z, ()))))
    mor = _ids_by_zcta(register[register["event_type"] == "nonrenewal_moratorium"])
    out["moratorium_event_ids"] = out["zcta"].map(lambda z: ";".join(mor.get(z, [])))

    if out["zcta"].duplicated().any():
        raise ValueError("wildfire_zip has duplicate zcta rows")
    out.to_csv(out_zip, index=False)
    reg_out = register.copy()
    reg_out["date_start"] = reg_out["date_start"].dt.strftime("%Y-%m-%d")
    reg_out["date_end"] = reg_out["date_end"].dt.strftime("%Y-%m-%d").fillna("")
    reg_out.to_csv(out_register, index=False)
    return out, reg_out


def _ids_by_zcta(events: pd.DataFrame) -> dict[str, list[str]]:
    ids: dict[str, list[str]] = {}
    for eid, zs in zip(events["event_id"], events["zctas_affected"]):
        for z in filter(None, str(zs).split(";")):
            ids.setdefault(z, []).append(eid)
    return ids


# ---------------------------------------------------------------- fetch (network)


def _get(url: str, **kw):
    import requests

    for attempt in range(4):
        try:
            r = requests.get(url, timeout=180, headers={"User-Agent": "nevada-county-housing-model"}, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def _post(url: str, data: dict):
    import requests

    for attempt in range(4):
        try:
            r = requests.post(url, data=data, timeout=180, headers={"User-Agent": "nevada-county-housing-model"})
            r.raise_for_status()
            return r
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def fetch_zcta_county(raw: Path = RAW) -> pd.DataFrame:
    text = _get(S14_URL).text
    df = pd.read_csv(io.StringIO(text), sep="|", dtype=str)
    df = df[df["GEOID_COUNTY_20"].isin(REGION_COUNTIES) & df["GEOID_ZCTA5_20"].notna()]
    df = df[df["AREALAND_PART"].astype(float) > 0]
    out = pd.DataFrame(
        {
            "zcta": df["GEOID_ZCTA5_20"],
            "county_fips": df["GEOID_COUNTY_20"],
            "county_land_share": (df["AREALAND_PART"].astype(float) / df["AREALAND_ZCTA5_20"].astype(float)).round(4),
        }
    ).sort_values(["zcta", "county_fips"])
    path = raw / "zcta_county_region.csv"
    out.to_csv(path, index=False)
    write_manifest(path, S14_URL, f"{LICENSE_CENSUS}; rows for counties {' '.join(REGION_COUNTIES)} only")
    return out


def _tiger_query(params: dict, post: bool = False) -> dict:
    params = {"f": "json", **params}
    r = _post(TIGER_ZCTA + "/query", params) if post else _get(TIGER_ZCTA + "/query", params=params)
    d = r.json()
    if "error" in d:
        raise RuntimeError(d["error"])
    return d


def zcta_at(lon: float, lat: float) -> str | None:
    d = _tiger_query(
        {
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "ZCTA5",
            "returnGeometry": "false",
        }
    )
    feats = d.get("features", [])
    return feats[0]["attributes"]["ZCTA5"] if feats else None


def fetch_zcta_attributes_and_adjacency(zctas: list[str], raw: Path = RAW):
    """Housing units and land area per ZCTA, and first-order (shared boundary) neighbors, computed by TIGERweb."""
    attrs, edges = [], []
    for z in sorted(zctas):
        d = _tiger_query({"where": f"ZCTA5='{z}'", "outFields": "ZCTA5,HU100,POP100,AREALAND", "returnGeometry": "true"})
        feat = d["features"][0]
        a = feat["attributes"]
        attrs.append({"zcta": z, "hu2020": int(a["HU100"]), "pop2020": int(a["POP100"]), "arealand_m2": int(a["AREALAND"])})
        geom = {"rings": feat["geometry"]["rings"], "spatialReference": d["spatialReference"]}
        n = _tiger_query(
            {
                "geometry": json.dumps(geom),
                "geometryType": "esriGeometryPolygon",
                "inSR": json.dumps(d["spatialReference"]),
                "spatialRel": "esriSpatialRelTouches",
                "outFields": "ZCTA5",
                "returnGeometry": "false",
            },
            post=True,
        )
        edges += [{"zcta": z, "neighbor": f["attributes"]["ZCTA5"]} for f in n["features"] if f["attributes"]["ZCTA5"] != z]
    pa, pe = raw / "zcta_attributes.csv", raw / "zcta_adjacency.csv"
    pd.DataFrame(attrs).to_csv(pa, index=False)
    pd.DataFrame(edges).sort_values(["zcta", "neighbor"]).to_csv(pe, index=False)
    url = TIGER_ZCTA + " (Census 2020 ZCTA layer)"
    write_manifest(pa, url, f"{LICENSE_CENSUS}; HU100, POP100, AREALAND per ZCTA")
    write_manifest(pe, url, f"{LICENSE_CENSUS}; spatialRel=esriSpatialRelTouches per ZCTA polygon")


def fetch_dins(raw: Path = RAW) -> pd.DataFrame:
    """DINS structure records in the region, minus No Damage; the 2020 ZCTA comes from each record's coordinates."""
    counties = ",".join(f"'{c}'" for c in REGION_COUNTIES.values())
    fields = "INCIDENTNAME,INCIDENTNUM,INCIDENTSTARTDATE,COUNTY,ZIPCODE,DAMAGE,STRUCTURECATEGORY,LATITUDE,LONGITUDE"
    feats, offset = [], 0
    while True:
        d = _get(
            DINS_URL + "/query",
            params={
                "where": f"COUNTY IN ({counties}) AND DAMAGE <> 'No Damage'",
                "outFields": fields,
                "returnGeometry": "false",
                "orderByFields": "OBJECTID",
                "resultOffset": offset,
                "resultRecordCount": 1000,
                "f": "json",
            },
        ).json()
        if "error" in d:
            raise RuntimeError(d["error"])
        feats += [f["attributes"] for f in d["features"]]
        if not d.get("exceededTransferLimit"):
            break
        offset += len(d["features"])
    df = pd.DataFrame(feats)
    lookup = {}
    for lat, lon in df[["LATITUDE", "LONGITUDE"]].dropna().drop_duplicates().itertuples(index=False):
        if lat and lon:
            lookup[(lat, lon)] = zcta_at(lon, lat)
    out = pd.DataFrame(
        {
            "incident_name": df["INCIDENTNAME"].str.strip(),
            "incident_num": df["INCIDENTNUM"],
            # DINS stores the start date as midnight UTC of the local date.
            "incident_start": df["INCIDENTSTARTDATE"].map(
                lambda ms: datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat() if pd.notna(ms) else ""
            ),
            "county": df["COUNTY"],
            "zip_reported": df["ZIPCODE"].map(lambda z: f"{int(z):05d}" if pd.notna(z) and int(z) > 0 else ""),
            "damage": df["DAMAGE"],
            "structure_category": df["STRUCTURECATEGORY"],
            "zcta": [lookup.get((a, b)) for a, b in zip(df["LATITUDE"], df["LONGITUDE"])],
            # 4 decimals is about 10 m: enough to audit the ZCTA, coarse enough not to pin an address.
            "lat": df["LATITUDE"].round(4),
            "lon": df["LONGITUDE"].round(4),
        }
    ).sort_values(["incident_start", "incident_name", "zcta", "damage", "lat", "lon"])
    path = raw / "dins_structures.csv"
    out.to_csv(path, index=False)
    write_manifest(path, DINS_URL, f"{LICENSE_CALFIRE}; region counties, DAMAGE <> 'No Damage', addresses dropped")
    return out


def fetch_calfire_incidents(raw: Path = RAW) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(_get(CALFIRE_URL).text), dtype=str, keep_default_na=False)
    county = df["incident_county"].str.contains("|".join(REGION_COUNTIES.values()), regex=True)
    big = pd.to_numeric(df["incident_acres_burned"], errors="coerce").fillna(0) >= 50000
    df = df[county | big]
    out = pd.DataFrame(
        {
            "name": df["incident_name"].str.strip(),
            # The dateonly_* columns are UTC dates; an evening start would land on the next day.
            "date_start": df["incident_date_created"].map(_pacific_date),
            "date_end": df["incident_date_extinguished"].map(_pacific_date),
            "county": df["incident_county"],
            "acres": df["incident_acres_burned"],
            "lat": df["incident_latitude"],
            "lon": df["incident_longitude"],
            "url": df["incident_url"],
        }
    ).sort_values(["date_start", "name"])
    path = raw / "calfire_incidents.csv"
    out.to_csv(path, index=False)
    write_manifest(path, CALFIRE_URL, f"{LICENSE_CALFIRE}; rows in region counties or >= 50,000 acres")
    return out


def _pacific_date(ts: str) -> str:
    from zoneinfo import ZoneInfo

    if not ts:
        return ""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()


def fetch_cdi_s2(zips: set[str], raw: Path = RAW) -> pd.DataFrame:
    import hashlib

    content = _get(S2_URL).content
    digest = hashlib.sha256(content).hexdigest()
    extract = s2_extract(read_pivot_cache(content), zips | {"95712", "95924"}, set(REGION_COUNTIES.values()))
    path = raw / "cdi_s2_fire_risk.csv"
    extract.to_csv(path, index=False)
    write_manifest(path, S2_URL, f"{LICENSE_CDI}; Fire Risk Scores slice for region ZIPs, extracted from xlsx sha256 {digest}")
    return extract


def fetch(raw: Path = RAW) -> None:
    raw.mkdir(parents=True, exist_ok=True)
    region = fetch_zcta_county(raw)
    zctas = sorted(region["zcta"].unique())
    fetch_cdi_s2(set(zctas), raw)
    fetch_calfire_incidents(raw)
    fetch_dins(raw)
    fetch_zcta_attributes_and_adjacency(zctas, raw)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("step", choices=["fetch", "build", "all"])
    a = p.parse_args(argv)
    if a.step in ("fetch", "all"):
        fetch()
    if a.step in ("build", "all"):
        z, r = build()
        print(f"wildfire: {len(z)} ZCTAs, {int(z['burned'].sum())} burned, "
              f"{int(z['spillover_candidate'].sum())} spillover candidates; {len(r)} register events")


if __name__ == "__main__":
    main()
