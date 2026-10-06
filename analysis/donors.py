"""Donor-pool, pre-period fit and identification diagnostics for issue #7.

Reads only data/panel_zip_year.csv and data/event_register.csv. Synthetic-control weights see
log ZHVI for years up to PRE_END only; PRE_END is derived from the register, not from outcomes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import statsmodels.api as sm  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PANEL_PATH = ROOT / "data" / "panel_zip_year.csv"
EVENTS_PATH = ROOT / "data" / "event_register.csv"
REPORT_PATH = ROOT / "output" / "donor_diagnostics.md"
PLOT_PATH = ROOT / "output" / "donor_pretrends.png"

# Pre-set thresholds, fixed before any outcome comparison (docs/estimands.md section 5).
HAZARD_MAX = 0.25  # cdi_high_extreme_share_2018; target core ZCTAs are 0.67-0.91
INSURANCE_KNOWN_MIN = 0.5  # share of window cells present to call an insurance change measured
LOW_NONRENEWAL_DELTA = 0.01  # W2 change in nonrenewal rate (share) counted as "low increase"
LOW_PREMIUM_LOG_DELTA = 0.10  # W2 log change in real HO premium counted as "low increase"
HOLDOUT_YEARS = 5
MIN_POOL = 2
CLUSTERS_RELIABLE = 20
Z_MDT = 1.96 + 0.84  # two-sided 5% test with 80% power

STATEWIDE_W2_EVENT = "RULE_2019_01_01_SB824"
STATEWIDE_W3_EVENT = "INS_2023_05_27_STATE_FARM_NEW_BUSINESS"
TARGET_ROLE = "target"
DEMAND_ROLE = "spillover_source"

INSURANCE_COLS = {
    "nonrenewal": "admitted_homeowners__nonrenewal_rate",
    "premium": "admitted_homeowners__avg_premium_per_exposure_real",
    "fair_share": "fair_plan__fair_plan_share",
}

POOL_RULES = {
    "A_strict": ("moratorium", "burned", "hazard", "adjacent", "price_data"),
    "B_hazard_relaxed": ("moratorium", "burned", "adjacent", "price_data"),
    "C_contamination_relaxed": ("hazard", "adjacent", "price_data"),
    "D_adjacency_relaxed": ("moratorium", "burned", "hazard", "price_data"),
}
POOL_NOTES = {
    "A_strict": "main pool: every rule applied",
    "B_hazard_relaxed": "drops the hazard rule",
    "C_contamination_relaxed": "keeps moratorium-listed and burned ZCTAs (contamination contrast)",
    "D_adjacency_relaxed": "keeps adjacency-proxy ZCTAs (spillover contrast)",
}


@dataclass
class Windows:
    pre_end: int
    first_target_event: str
    w2_break: int
    w3_break: int
    insurance_last_year: int
    w1a: dict[str, int] = field(default_factory=dict)
    w1b: dict[str, int] = field(default_factory=dict)

    def w2(self) -> tuple[range, range]:
        return range(self.w2_break - 4, self.w2_break), range(
            self.w2_break, self.insurance_last_year + 1
        )

    def w3(self) -> tuple[range, range]:
        return range(self.w3_break - 4, self.w3_break), range(
            self.w3_break, self.insurance_last_year + 1
        )


@dataclass
class PoolFit:
    name: str
    donors: list[str]
    fit_years: list[int]
    weights: pd.Series | None = None
    pre_rmspe: float = math.nan
    holdout_rmspe: float = math.nan
    holdout_train_rmspe: float = math.nan
    pretrend_mean: dict | None = None
    placebo: pd.DataFrame | None = None
    synth: pd.Series | None = None
    pool_mean: pd.Series | None = None
    feasible: bool = False
    note: str = ""


# --- inputs -------------------------------------------------------------------------------


def load_inputs(
    panel_path: Path = PANEL_PATH, events_path: Path = EVENTS_PATH
) -> tuple[pd.DataFrame, pd.DataFrame]:
    panel = pd.read_csv(panel_path, dtype={"zcta": str, "county_fips": str}, low_memory=False)
    events = pd.read_csv(events_path, dtype=str)
    return panel, events


def event_zctas(events: pd.DataFrame) -> pd.DataFrame:
    """One row per (event, listed ZCTA); statewide events have no ZCTA and are dropped."""
    rows = []
    for ev in events.itertuples(index=False):
        listed = ev.zctas_affected if isinstance(ev.zctas_affected, str) else ""
        for z in filter(None, (s.strip() for s in listed.split(";"))):
            rows.append(
                {
                    "event_id": ev.event_id,
                    "event_type": ev.event_type,
                    "year": pd.Timestamp(ev.date_start).year,
                    "zcta": z.zfill(5),
                }
            )
    return pd.DataFrame(rows, columns=["event_id", "event_type", "year", "zcta"])


def event_windows(panel: pd.DataFrame, events: pd.DataFrame) -> Windows:
    targets = set(panel.loc[panel.panel_role == TARGET_ROLE, "zcta"])
    ez = event_zctas(events)
    on_target = ez[ez.zcta.isin(targets)].sort_values(["year", "event_id"])
    first = on_target.iloc[0]
    by_id = events.set_index("event_id")
    ins_cols = list(INSURANCE_COLS.values())
    ins_years = panel.loc[panel[ins_cols].notna().any(axis=1), "year"]
    fires = ez[ez.event_type == "fire"].groupby("zcta").year.min()
    mors = ez[ez.event_type == "nonrenewal_moratorium"].groupby("zcta").year.min()
    return Windows(
        pre_end=int(first.year) - 1,
        first_target_event=str(first.event_id),
        w2_break=pd.Timestamp(by_id.loc[STATEWIDE_W2_EVENT, "date_start"]).year,
        w3_break=pd.Timestamp(by_id.loc[STATEWIDE_W3_EVENT, "date_start"]).year,
        insurance_last_year=int(ins_years.max()),
        w1a={z: int(y) for z, y in fires.items()},
        w1b={z: int(y) for z, y in mors.items()},
    )


# --- donor table --------------------------------------------------------------------------


def _first(s: pd.Series):
    s = s.dropna()
    return s.iloc[0] if len(s) else np.nan


def insurance_change(panel: pd.DataFrame, win: Windows) -> pd.DataFrame:
    """Measured insurance change per ZCTA over W2 and W3; NaN where the source is suppressed."""
    out = {}
    nr, pr, fs = INSURANCE_COLS["nonrenewal"], INSURANCE_COLS["premium"], INSURANCE_COLS["fair_share"]
    w2_pre, w2_post = win.w2()
    w3_pre, w3_post = win.w3()
    w2_years = set(w2_pre) | set(w2_post)
    for z, g in panel.groupby("zcta"):
        g = g.set_index("year")

        def mean(col, years):
            return g.loc[g.index.isin(list(years)), col].mean()

        cells = g.loc[g.index.isin(list(w2_years)), [nr, pr]]
        coverage = float(cells.notna().to_numpy().mean()) if len(cells) else 0.0
        prem_pre = g.loc[g.index.isin(list(w2_pre)), pr].dropna()
        prem_post = g.loc[g.index.isin(list(w2_post)), pr].dropna()
        prem3_pre = g.loc[g.index.isin(list(w3_pre)), pr].dropna()
        prem3_post = g.loc[g.index.isin(list(w3_post)), pr].dropna()
        out[z] = {
            "ins_coverage_w2": coverage,
            "w2_nonrenewal_change": mean(nr, w2_post) - mean(nr, w2_pre),
            "w2_premium_log_change": (
                np.log(prem_post).mean() - np.log(prem_pre).mean()
                if len(prem_pre) and len(prem_post)
                else np.nan
            ),
            "w3_nonrenewal_change": mean(nr, w3_post) - mean(nr, w3_pre),
            "w3_premium_log_change": (
                np.log(prem3_post).mean() - np.log(prem3_pre).mean()
                if len(prem3_pre) and len(prem3_post)
                else np.nan
            ),
            "fair_share_2022": _first(g.loc[g.index == 2022, fs]),
        }
    df = pd.DataFrame.from_dict(out, orient="index")
    df.index.name = "zcta"

    def status(r):
        if r.ins_coverage_w2 < INSURANCE_KNOWN_MIN:
            return "unknown"
        nr_ok = not (r.w2_nonrenewal_change > LOW_NONRENEWAL_DELTA)
        pr_ok = not (r.w2_premium_log_change > LOW_PREMIUM_LOG_DELTA)
        return "low increase" if nr_ok and pr_ok else "increase"

    df["insurance_status"] = df.apply(status, axis=1)
    return df


def donor_table(panel: pd.DataFrame, events: pd.DataFrame, win: Windows) -> pd.DataFrame:
    """One row per non-target ZCTA with every rule outcome, reasons and pool membership."""
    ez = event_zctas(events)
    targets = sorted(panel.loc[panel.panel_role == TARGET_ROLE, "zcta"].unique())
    core = sorted(panel.loc[panel.geo_tier == "core", "zcta"].unique())
    core_counties = set(panel.loc[panel.zcta.isin(core), "county_fips"].dropna())
    colisted_events = set(ez.loc[ez.zcta.isin(core), "event_id"])
    fit_years = fit_years_for(panel, core, win.pre_end)

    static = panel.groupby("zcta").agg(
        panel_role=("panel_role", "first"),
        geo_tier=("geo_tier", "first"),
        county_fips=("county_fips", "first"),
        hu2020=("hu2020", "first"),
        cdi_high_extreme_share_2018=("cdi_high_extreme_share_2018", _first),
    )
    static = static[static.panel_role != TARGET_ROLE]

    zhvi = panel.loc[panel.year.isin(fit_years)].pivot(
        index="zcta", columns="year", values="zhvi_real"
    )
    complete = zhvi.notna().all(axis=1).reindex(static.index, fill_value=False)

    w2_pre, w2_post = win.w2()
    w3_pre, w3_post = win.w3()
    mor = ez[ez.event_type == "nonrenewal_moratorium"]
    fire = ez[ez.event_type == "fire"]
    ins = insurance_change(panel, win)

    rows = []
    for z, s in static.iterrows():
        z_mor = mor[mor.zcta == z]
        z_fire = fire[fire.zcta == z]
        colisted = sorted(set(ez.loc[ez.zcta == z, "event_id"]) & colisted_events)
        same_county = s.county_fips in core_counties
        risk = s.cdi_high_extreme_share_2018
        fails = {
            "moratorium": len(z_mor) > 0,
            "burned": len(z_fire) > 0,
            "hazard": not (risk < HAZARD_MAX),
            "adjacent": same_county or bool(colisted),
            "price_data": not bool(complete.get(z, False)),
        }
        reasons = []
        if s.panel_role == DEMAND_ROLE:
            reasons.append("Bay Area demand-linkage source: spillover measure, never a control")
        else:
            if fails["moratorium"]:
                reasons.append(
                    "contamination: on moratorium list(s) "
                    + ", ".join(sorted(z_mor.event_id))
                )
            if fails["burned"]:
                reasons.append("contamination: register fire damage " + ", ".join(sorted(z_fire.event_id)))
            if fails["hazard"]:
                reasons.append(
                    "hazard unknown (no 2018 S2 risk)"
                    if pd.isna(risk)
                    else f"hazard: 2018 high/extreme share {risk:.2f} >= {HAZARD_MAX}"
                )
            if fails["adjacent"]:
                parts = []
                if same_county:
                    parts.append("same county as core ZCTAs")
                if colisted:
                    parts.append("co-listed with a core ZCTA on " + ", ".join(colisted))
                reasons.append("spillover: adjacency proxy (" + "; ".join(parts) + ")")
            if fails["price_data"]:
                reasons.append(f"price data: ZHVI incomplete over fit years {fit_years[0]}-{fit_years[-1]}")
        pools = []
        if s.panel_role != DEMAND_ROLE:
            pools = [p for p, rules in POOL_RULES.items() if not any(fails[r] for r in rules)]
        status = ins.loc[z, "insurance_status"] if z in ins.index else "unknown"
        if "A_strict" in pools:
            decision = "include"
            reasons = [
                "passes moratorium, fire, hazard, adjacency and price-data rules; insurance change "
                + ("not measured (unknown), so not certified low" if status == "unknown" else status)
            ]
        else:
            decision = "exclude"
        rows.append(
            {
                "zcta": z,
                "panel_role": s.panel_role,
                "county_fips": s.county_fips,
                "hu2020": s.hu2020,
                "risk_2018": risk,
                "moratoria_w2_post": int(z_mor.year.isin(list(w2_post)).sum()),
                "moratoria_w3_post": int((z_mor.year >= win.w3_break).sum()),
                "moratoria_total": len(z_mor),
                "fires_total": len(z_fire),
                "first_moratorium_year": win.w1b.get(z),
                "first_burn_year": win.w1a.get(z),
                "adjacent_proxy": int(fails["adjacent"]),
                "zhvi_complete_pre": int(not fails["price_data"]),
                "insurance_status": status,
                "ins_coverage_w2": ins.loc[z, "ins_coverage_w2"] if z in ins.index else 0.0,
                "w2_nonrenewal_change": ins.loc[z, "w2_nonrenewal_change"] if z in ins.index else np.nan,
                "w2_premium_log_change": ins.loc[z, "w2_premium_log_change"] if z in ins.index else np.nan,
                "w3_premium_log_change": ins.loc[z, "w3_premium_log_change"] if z in ins.index else np.nan,
                "decision": decision,
                "pools": ",".join(p.split("_")[0] for p in pools),
                "reason": "; ".join(reasons),
            }
        )
    return pd.DataFrame(rows).sort_values(["panel_role", "zcta"]).reset_index(drop=True)


# --- weights (pre-period only) ------------------------------------------------------------


def fit_years_for(panel: pd.DataFrame, treated: list[str], pre_end: int) -> list[int]:
    """Pre-period years in which every treated ZCTA with any pre-period ZHVI has a value."""
    pre = panel[(panel.year <= pre_end) & panel.zcta.isin(treated)]
    wide = pre.pivot(index="year", columns="zcta", values="zhvi_real")
    wide = wide.loc[:, wide.notna().any()]
    return [int(y) for y in wide.index[wide.notna().all(axis=1)]]


def log_zhvi_wide(panel: pd.DataFrame, zctas: list[str], years: list[int]) -> pd.DataFrame:
    """Rows ZCTA, columns year; rows filtered to the requested years before pivoting."""
    sub = panel[panel.zcta.isin(zctas) & panel.year.isin(years)]
    wide = sub.pivot(index="zcta", columns="year", values="zhvi_real")
    return np.log(wide.reindex(index=zctas, columns=years))


def solve_simplex_weights(X: pd.DataFrame, y: pd.Series, iters: int = 20000) -> pd.Series:
    """Weights w >= 0, sum 1, minimizing ||X.T w - y||^2 (rows donors, columns years)."""
    A = X.to_numpy(dtype=float).T
    b = y.reindex(X.columns).to_numpy(dtype=float)
    n = A.shape[1]
    lip = max(np.linalg.eigvalsh(A.T @ A).max(), 1e-12)
    w = np.full(n, 1.0 / n)
    z, t = w.copy(), 1.0
    for _ in range(iters):
        w_new = _project_simplex(z - (A.T @ (A @ z - b)) / lip)
        t_new = (1 + math.sqrt(1 + 4 * t * t)) / 2
        z = w_new + ((t - 1) / t_new) * (w_new - w)
        if np.abs(w_new - w).max() < 1e-10:
            w = w_new
            break
        w, t = w_new, t_new
    return pd.Series(w, index=X.index)


def _project_simplex(v: np.ndarray) -> np.ndarray:
    u = np.sort(v)[::-1]
    css = np.cumsum(u) - 1
    k = np.nonzero(u - css / np.arange(1, len(v) + 1) > 0)[0][-1]
    return np.maximum(v - css[k] / (k + 1), 0)


def select_weights(X: pd.DataFrame, y: pd.Series, pre_end: int) -> pd.Series:
    """Fit on unit-demeaned log ZHVI (trajectories, not levels); refuses any post-period column."""
    post = [c for c in list(X.columns) + list(y.index) if int(c) > pre_end]
    if post:
        raise ValueError(f"weight selection received post-period columns {sorted(set(post))}")
    Xd = X.sub(X.mean(axis=1), axis=0)
    yd = y - y.mean()
    return solve_simplex_weights(Xd, yd)


def _demean(s: pd.Series) -> pd.Series:
    return s - s.mean()


def _rmspe(a: pd.Series, b: pd.Series) -> float:
    return float(np.sqrt(((a - b) ** 2).mean()))


def pretrend_test(diff: pd.Series) -> dict:
    """Linear trend in a treated-minus-comparison series: slope, SEs, p-value, MDT."""
    years = np.asarray(diff.index, dtype=float)
    X = sm.add_constant(years - years.mean())
    ols = sm.OLS(diff.to_numpy(dtype=float), X).fit()
    hac = sm.OLS(diff.to_numpy(dtype=float), X).fit(cov_type="HAC", cov_kwds={"maxlags": 2})
    se = float(max(ols.bse[1], hac.bse[1]))
    return {
        "slope": float(ols.params[1]),
        "se_ols": float(ols.bse[1]),
        "se_hac": float(hac.bse[1]),
        "p_hac": float(hac.pvalues[1]),
        "mdt": Z_MDT * se,
        "max_abs_gap": float((diff - diff.mean()).abs().max()),
        "n_years": len(diff),
    }


def fit_pool(
    panel: pd.DataFrame, name: str, donors: list[str], treated: list[str], win: Windows
) -> PoolFit:
    fit_years = fit_years_for(panel, treated, win.pre_end)
    pf = PoolFit(name=name, donors=donors, fit_years=fit_years)
    if len(donors) < MIN_POOL:
        pf.note = f"infeasible: {len(donors)} donor(s), need {MIN_POOL}"
        return pf
    tw = log_zhvi_wide(panel, treated, fit_years).dropna(how="all")
    y = tw.mean(axis=0)
    X = log_zhvi_wide(panel, donors, fit_years)
    w = select_weights(X, y, win.pre_end)
    synth = X.T @ w
    pf.weights, pf.feasible = w, True
    pf.synth = synth
    pf.pool_mean = X.mean(axis=0)
    pf.pre_rmspe = _rmspe(_demean(y), _demean(synth))
    train = fit_years[:-HOLDOUT_YEARS]
    hold = fit_years[-HOLDOUT_YEARS:]
    w_tr = select_weights(X[train], y[train], win.pre_end)
    s_all = X.T @ w_tr
    shift = (y[train] - s_all[train]).mean()
    pf.holdout_train_rmspe = _rmspe(y[train], s_all[train] + shift)
    pf.holdout_rmspe = _rmspe(y[hold], s_all[hold] + shift)
    pf.pretrend_mean = pretrend_test(y - pf.pool_mean)
    pf.placebo = placebo_in_space(X, train, hold, win.pre_end)
    return pf


def placebo_in_space(X: pd.DataFrame, train: list[int], hold: list[int], pre_end: int) -> pd.DataFrame:
    """Each donor as pseudo-treated against the rest; pre-period hold-out error only."""
    rows = []
    for z in X.index:
        rest = X.drop(index=z)
        if len(rest) < MIN_POOL:
            continue
        y = X.loc[z]
        w = select_weights(rest[train], y[train], pre_end)
        s = rest.T @ w
        shift = (y[train] - s[train]).mean()
        rows.append(
            {
                "zcta": z,
                "train_rmspe": _rmspe(y[train], s[train] + shift),
                "holdout_rmspe": _rmspe(y[hold], s[hold] + shift),
            }
        )
    return pd.DataFrame(rows)


# --- balance and linkage ------------------------------------------------------------------


def unit_covariates(panel: pd.DataFrame, zctas: list[str], fit_years: list[int]) -> pd.DataFrame:
    sub = panel[panel.zcta.isin(zctas)]
    lz = log_zhvi_wide(panel, zctas, fit_years)
    late = [y for y in fit_years if y >= fit_years[-1] - 5]
    acs = sub[sub.acs_window_start == 2017].set_index("zcta")
    static = sub.groupby("zcta").agg(
        hu2020=("hu2020", "first"),
        risk18=("cdi_high_extreme_share_2018", _first),
        avg_risk18=("cdi_avg_fire_risk_2018", _first),
    )
    df = pd.DataFrame(index=zctas)
    df["pre log ZHVI level (mean)"] = lz.mean(axis=1)
    df["pre growth, full (log/yr)"] = (lz[fit_years[-1]] - lz[fit_years[0]]) / (
        fit_years[-1] - fit_years[0]
    )
    df["pre growth, last 5 yrs (log/yr)"] = (lz[late[-1]] - lz[late[0]]) / (late[-1] - late[0])
    df["log housing units 2020"] = np.log(static.hu2020.reindex(zctas))
    df["S2 high/extreme share 2018"] = static.risk18.reindex(zctas)
    df["S2 avg risk score 2018"] = static.avg_risk18.reindex(zctas)
    df["ACS 2017-21 log median HH income (real)*"] = np.log(
        acs.acs5_median_hh_income_real.reindex(zctas)
    )
    df["ACS 2017-21 vacancy rate*"] = acs.acs5_vacancy_rate.reindex(zctas)
    return df


def balance_table(panel: pd.DataFrame, pf: PoolFit, treated: list[str]) -> pd.DataFrame:
    cov = unit_covariates(panel, treated + pf.donors, pf.fit_years)
    t = cov.loc[treated].mean()
    d = cov.loc[pf.donors]
    pooled_sd = cov.std()
    w = pf.weights.reindex(pf.donors)
    synth = d.apply(lambda c: np.nansum(c * w) / w[c.notna()].sum() if w[c.notna()].sum() > 0 else np.nan)
    return pd.DataFrame(
        {
            "treated": t,
            "pool mean": d.mean(),
            "synthetic": synth,
            "std diff (pool)": (t - d.mean()) / pooled_sd,
            "std diff (synthetic)": (t - synth) / pooled_sd,
        }
    )


def demand_linkage(panel: pd.DataFrame, treated: list[str], fits: dict[str, PoolFit], fit_years: list[int]) -> dict:
    """Pre-period co-movement with the Bay Area index; a spillover measure, not a control."""
    bay = sorted(panel.loc[panel.panel_role == DEMAND_ROLE, "zcta"].unique())
    bw = log_zhvi_wide(panel, bay, fit_years)
    bw = bw[bw.notna().all(axis=1)]
    bay_idx = bw.mean(axis=0)
    g_bay = bay_idx.diff().dropna()
    tgt = log_zhvi_wide(panel, treated, fit_years).mean(axis=0)
    out = {
        "bay_zctas": len(bay),
        "bay_complete": len(bw),
        "corr_target": float(tgt.diff().dropna().corr(g_bay)),
        "bay_index": bay_idx,
        "pools": {},
    }
    for name, pf in fits.items():
        if pf.feasible:
            out["pools"][name] = float(pf.synth.diff().dropna().corr(g_bay))
    return out


# --- identification facts -----------------------------------------------------------------


def timing_table(panel: pd.DataFrame, win: Windows) -> pd.DataFrame:
    targets = panel[panel.panel_role == TARGET_ROLE].groupby("zcta").agg(tier=("geo_tier", "first"))
    first_zhvi = panel[panel.zhvi_real.notna()].groupby("zcta").year.min()
    rows = []
    for z, r in targets.iterrows():
        w1a, w1b = win.w1a.get(z), win.w1b.get(z)
        fz = first_zhvi.get(z)
        rows.append(
            {
                "zcta": z,
                "tier": r.tier,
                "W1a first burn": w1a or "none",
                "W1b first moratorium": w1b or "none",
                "W2 break": win.w2_break,
                "W3 break": win.w3_break,
                "price pre-years before W1b": (w1b - fz) if (w1b and pd.notna(fz)) else 0,
                "S2/S3 pre-years before W2": len(
                    panel[
                        (panel.zcta == z)
                        & (panel.year < win.w2_break)
                        & panel[INSURANCE_COLS["nonrenewal"]].notna()
                    ]
                ),
            }
        )
    return pd.DataFrame(rows)


def acs_windows(panel: pd.DataFrame, win: Windows) -> pd.DataFrame:
    w = panel.loc[panel.acs_window.notna(), ["acs_window", "acs_window_start", "year"]].drop_duplicates()
    w = w.sort_values("year").astype({"acs_window_start": int})
    w["years before W2 break"] = (win.w2_break - w.acs_window_start).clip(lower=0)
    w["years at or after W2 break"] = (w.year - win.w2_break + 1).clip(lower=0)
    return w.rename(columns={"year": "window end"}).reset_index(drop=True)


def verdict(facts: dict) -> tuple[bool, list[tuple[str, bool, str]]]:
    checks = [
        (
            "Insurance change measured in donors",
            facts["donors_insurance_known"] > 0,
            f"{facts['donors_insurance_known']} of {facts['pool_a_size']} pool A donors have a measured "
            "insurance change; the rest are unknown, so none is certified low-insurance-increase",
        ),
        (
            "Enough geographic clusters for cluster-robust inference",
            facts["clusters"] >= CLUSTERS_RELIABLE,
            f"{facts['treated_zctas']} treated ZCTAs in {facts['treated_counties']} county, "
            f"{facts['clusters']} ZCTA clusters in the main design, {facts['county_clusters']} counties",
        ),
        (
            "Treatment timing separable from local fire destruction",
            facts["moratorium_without_local_fire"] > 0,
            f"{facts['moratorium_without_local_fire']} core ZCTA(s) have a first moratorium year with no "
            "register fire in that ZCTA or another core ZCTA in the same or prior year",
        ),
        (
            "Clean pre-period with adequate fit",
            facts["holdout_ok"],
            f"pool A hold-out RMSPE {facts['holdout_rmspe']:.4f} vs placebo median "
            f"{facts['placebo_median']:.4f}; pretrend MDT {facts['mdt']:.4f} log points/yr",
        ),
        (
            "Comparable controls (structural balance)",
            facts["balance_ok"],
            f"largest |std diff| of synthetic on structural covariates: {facts['max_std_diff']:.2f} "
            "(threshold 0.25)",
        ),
    ]
    return all(ok for _, ok, _ in checks), checks


# --- report -------------------------------------------------------------------------------


def _fmt(v, nd=3):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def md_table(df: pd.DataFrame, nd=3) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(_fmt(r[c], nd) for c in cols) + " |")
    return "\n".join(lines)


def build_diagnostics(panel: pd.DataFrame, events: pd.DataFrame) -> dict:
    win = event_windows(panel, events)
    table = donor_table(panel, events, win)
    core = sorted(panel.loc[panel.geo_tier == "core", "zcta"].unique())
    core_fit = fit_years_for(panel, core, win.pre_end)
    treated = [
        z for z in core if log_zhvi_wide(panel, [z], core_fit).notna().all(axis=1).iloc[0]
    ]
    fringe = sorted(panel.loc[panel.geo_tier == "fringe", "zcta"].unique())
    treated_fringe = treated + [
        z for z in fringe if log_zhvi_wide(panel, [z], core_fit).notna().any(axis=1).iloc[0]
    ]
    fits: dict[str, PoolFit] = {}
    for name in POOL_RULES:
        letter = name.split("_")[0]
        donors = sorted(
            table.loc[table.pools.str.split(",").apply(lambda p: letter in p), "zcta"]
        )
        fits[name] = fit_pool(panel, name, donors, treated, win)
    a = fits["A_strict"]
    fringe_fit = fit_pool(panel, "A_strict_with_fringe", a.donors, treated_fringe, win)
    balance = balance_table(panel, a, treated) if a.feasible else None
    linkage = demand_linkage(panel, treated, fits, core_fit)

    structural = [
        "log housing units 2020",
        "S2 high/extreme share 2018",
        "S2 avg risk score 2018",
        "ACS 2017-21 log median HH income (real)*",
        "ACS 2017-21 vacancy rate*",
    ]
    pool_a_rows = table[table.zcta.isin(a.donors)]
    counties = set(pool_a_rows.county_fips) | set(
        panel.loc[panel.zcta.isin(treated), "county_fips"]
    )
    ez = event_zctas(events)
    fires = ez[ez.event_type == "fire"]
    sep = 0
    for z in core:
        y = win.w1b.get(z)
        if y is None:
            continue
        local = fires[fires.zcta.isin(core) & fires.year.between(y - 1, y)]
        sep += int(local.empty)
    plc = a.placebo if a.feasible else pd.DataFrame()
    facts = {
        "donors_insurance_known": int((pool_a_rows.insurance_status != "unknown").sum()),
        "pool_a_size": len(a.donors),
        "clusters": len(treated) + len(a.donors),
        "treated_zctas": len(treated),
        "treated_counties": panel.loc[panel.zcta.isin(treated), "county_fips"].nunique(),
        "county_clusters": len(counties),
        "moratorium_without_local_fire": sep,
        "holdout_rmspe": a.holdout_rmspe,
        "placebo_median": float(plc.holdout_rmspe.median()) if len(plc) else math.nan,
        "mdt": a.pretrend_mean["mdt"] if a.pretrend_mean else math.nan,
        "holdout_ok": bool(
            a.feasible and len(plc) and a.holdout_rmspe <= plc.holdout_rmspe.median()
        ),
        "max_std_diff": float(balance.loc[structural, "std diff (synthetic)"].abs().max())
        if balance is not None
        else math.nan,
    }
    facts["balance_ok"] = bool(facts["max_std_diff"] <= 0.25)
    feasible, checks = verdict(facts)
    return {
        "windows": win,
        "table": table,
        "treated": treated,
        "treated_fringe": treated_fringe,
        "fits": fits,
        "fringe_fit": fringe_fit,
        "balance": balance,
        "linkage": linkage,
        "timing": timing_table(panel, win),
        "acs": acs_windows(panel, win),
        "target_insurance": insurance_change(panel[panel.panel_role == TARGET_ROLE], win),
        "target_series": log_zhvi_wide(panel, treated, core_fit).mean(axis=0),
        "facts": facts,
        "feasible": feasible,
        "checks": checks,
    }


def render(d: dict) -> str:
    win: Windows = d["windows"]
    table: pd.DataFrame = d["table"]
    fits: dict[str, PoolFit] = d["fits"]
    a = fits["A_strict"]
    w2_pre, w2_post = win.w2()
    w3_pre, w3_post = win.w3()
    L = []
    add = L.append
    add("# Donor diagnostics")
    add("")
    add(
        "Issue #7. Generated by `uv run python -m analysis.donors`; do not edit by hand. Inputs: "
        "`data/panel_zip_year.csv` and `data/event_register.csv` only. Interpretation and verdict: "
        "`docs/identification.md`. Pretrend chart: `output/donor_pretrends.png`."
    )
    add("")
    add("## 1. Windows and pre-period")
    add("")
    add(
        f"- Pre-period end: **{win.pre_end}**, the year before the first register event listing a "
        f"target ZCTA (`{win.first_target_event}`). Weights use log real ZHVI for fit years "
        f"{a.fit_years[0]}-{a.fit_years[-1]} only ({len(a.fit_years)} years); no later year is passed "
        "to the optimizer (`select_weights` raises on any later column)."
    )
    add(
        f"- W2 statewide wave: break {win.w2_break} (`{STATEWIDE_W2_EVENT}`), pre "
        f"{w2_pre[0]}-{w2_pre[-1]}, post {w2_post[0]}-{w2_post[-1]}."
    )
    add(
        f"- W3 insurer retreat: break {win.w3_break} (`{STATEWIDE_W3_EVENT}`), pre "
        f"{w3_pre[0]}-{w3_pre[-1]}, post {w3_post[0]}-{w3_post[-1]}. ZIP insurance data end in "
        f"{win.insurance_last_year}."
    )
    add(
        f"- Treated (price fit): core ZCTAs with complete fit-year ZHVI: {', '.join(d['treated'])}. "
        "95986 has no ZHVI. Fringe sensitivity adds "
        f"{', '.join(z for z in d['treated_fringe'] if z not in d['treated'])}."
    )
    add(
        f"- Pre-set thresholds: 2018 S2 high/extreme share < {HAZARD_MAX}; insurance change counted as "
        f"measured when at least {INSURANCE_KNOWN_MIN:.0%} of W2 nonrenewal and premium cells are "
        f"present; low increase means W2 nonrenewal change <= {LOW_NONRENEWAL_DELTA} and W2 log premium "
        f"change <= {LOW_PREMIUM_LOG_DELTA}."
    )
    add("")
    add("## 2. Rules")
    add("")
    add("| rule | excludes a ZCTA when | measures |")
    add("|---|---|---|")
    add("| moratorium | listed on any `nonrenewal_moratorium` row of the register | contamination (own insurance change) |")
    add("| burned | listed on any `fire` row of the register (DINS damage) | contamination (destruction) |")
    add(f"| hazard | 2018 S2 high/extreme share missing or >= {HAZARD_MAX} | low exposure |")
    add(
        "| adjacent | same county as a core ZCTA, or co-listed with a core ZCTA on any register event | "
        "spillover (proxy: the allowed inputs carry no ZCTA geometry) |"
    )
    add("| price_data | ZHVI missing in any fit year | comparable market |")
    add("| Bay Area source | `panel_role = spillover_source` | demand linkage; never in a pool |")
    add("")
    add("| pool | rules applied | donors | note |")
    add("|---|---|---|---|")
    for name, rules in POOL_RULES.items():
        add(f"| {name} | {', '.join(rules)} | {len(fits[name].donors)} | {POOL_NOTES[name]} |")
    add("")
    counts = table.groupby(["panel_role", "decision"]).size().rename("ZCTAs").reset_index()
    add(md_table(counts))
    add("")
    add("## 3. Insurance change in candidates")
    add("")
    st = table[table.panel_role != DEMAND_ROLE].insurance_status.value_counts()
    add(
        "Measured insurance change, non-target candidates (excluding Bay Area sources): "
        + ", ".join(f"{k} {v}" for k, v in st.items())
        + ". The panel carries S2/S3/S6 ZIP insurance data for target ZCTAs only (code 5 elsewhere, "
        "`docs/panel_audit.md` section 2), so no donor can be certified low-insurance-increase. "
        "Register moratorium listings are the only measured insurance-side event for donors and are "
        "used as the contamination rule."
    )
    add("")
    add("Target ZCTAs, for scale (W2 and W3 as above; NaN where not published):")
    add("")
    ti = d["target_insurance"].reset_index()[
        [
            "zcta",
            "ins_coverage_w2",
            "w2_nonrenewal_change",
            "w2_premium_log_change",
            "w3_nonrenewal_change",
            "w3_premium_log_change",
            "fair_share_2022",
            "insurance_status",
        ]
    ]
    add(md_table(ti))
    add("")
    add(
        "S3 nonrenewal counts have a definition break in 2020 inside the W2 post-period "
        "(`admitted_homeowners__series_break`); W2 nonrenewal changes include it."
    )
    add("")
    add("## 4. Pre-period fit by pool")
    add("")
    add(
        "Fit on unit-demeaned log real ZHVI (trajectories). Hold-out: weights refit on the first "
        f"{len(a.fit_years) - HOLDOUT_YEARS} fit years, error measured on the last {HOLDOUT_YEARS} "
        "pre-period years after a level shift; still pre-period. Pretrend: OLS slope of treated mean "
        "minus equal-weighted pool mean on year, SE = max(OLS, HAC lag 2), MDT = 2.8 x SE (5% "
        "two-sided, 80% power), in log points per year. Max abs gap is the largest demeaned "
        "treated-minus-pool-mean deviation in any fit year; it catches non-linear pre-period "
        "divergence that a linear slope misses."
    )
    add("")
    rows = []
    for name, pf in list(fits.items()) + [("A_strict_with_fringe", d["fringe_fit"])]:
        if not pf.feasible:
            rows.append({"pool": name, "donors": len(pf.donors), "note": pf.note})
            continue
        pt = pf.pretrend_mean
        plc = pf.placebo
        rank = (
            int((plc.holdout_rmspe < pf.holdout_rmspe).sum()) + 1 if len(plc) else None
        )
        rows.append(
            {
                "pool": name,
                "donors": len(pf.donors),
                "donors w>0.01": int((pf.weights > 0.01).sum()),
                "fit RMSPE": pf.pre_rmspe,
                "hold-out RMSPE": pf.holdout_rmspe,
                "placebo hold-out median": float(plc.holdout_rmspe.median()) if len(plc) else math.nan,
                "rank among placebos": f"{rank} of {len(plc) + 1}" if rank else "",
                "pretrend slope": pt["slope"],
                "SE": max(pt["se_ols"], pt["se_hac"]),
                "p (HAC)": pt["p_hac"],
                "MDT": pt["mdt"],
                "max abs gap": pt["max_abs_gap"],
                "note": "",
            }
        )
    add(md_table(pd.DataFrame(rows).fillna(""), nd=4))
    add("")
    add("Weights above 0.01:")
    add("")
    for name, pf in fits.items():
        if pf.feasible:
            w = pf.weights[pf.weights > 0.01].sort_values(ascending=False)
            add(f"- {name}: " + ", ".join(f"{z} {v:.3f}" for z, v in w.items()))
        else:
            add(f"- {name}: {pf.note}")
    add("")
    add("## 5. Balance, pool A")
    add("")
    if d["balance"] is not None:
        add(
            "Std diff = (treated - comparison) / SD across treated and pool ZCTAs. Rows marked * use "
            "the ACS 2017-2021 window, which overlaps the post-period; they are reported, never used "
            "for weights. 2018 risk and 2020 housing units postdate the pre-period end and are "
            "balance checks only."
        )
        add("")
        add(md_table(d["balance"].reset_index().rename(columns={"index": "covariate"})))
    else:
        add("Pool A is infeasible; no balance table.")
    add("")
    add("## 6. Spillover and Bay Area demand linkage")
    add("")
    lk = d["linkage"]
    add(
        f"Bay Area demand-linkage ZCTAs: {lk['bay_zctas']} (`panel_role = spillover_source`), "
        f"{lk['bay_complete']} with complete fit-year ZHVI. They are listed in the donor table with an "
        "exclusion reason and never enter a pool. Correlation of annual log ZHVI growth with the Bay "
        f"Area index over the fit years: treated {lk['corr_target']:.3f}; "
        + "; ".join(f"{k} synthetic {v:.3f}" for k, v in lk["pools"].items())
        + ". A comparison that co-moves with the Bay Area like the target does not remove a Bay "
        "Area demand shock; one that co-moves less would load it onto the gap."
    )
    add("")
    adj = table[(table.panel_role != DEMAND_ROLE) & (table.adjacent_proxy == 1)]
    add(
        f"Adjacency-proxy ZCTAs: {len(adj)}. Pool D adds those that pass the other rules: "
        f"{', '.join(z for z in fits['D_adjacency_relaxed'].donors if z not in a.donors) or 'none'}."
    )
    add("")
    add("## 7. Staggered timing and ACS windows")
    add("")
    add(md_table(d["timing"]))
    add("")
    add(md_table(d["acs"]))
    add("")
    add("## 8. Feasibility checks")
    add("")
    add("| check | passes | evidence |")
    add("|---|---|---|")
    for name, ok, ev in d["checks"]:
        add(f"| {name} | {'yes' if ok else 'no'} | {ev} |")
    add("")
    add(
        "Verdict: **"
        + (
            "causal identification is feasible under these conditions"
            if d["feasible"]
            else "causal identification is rejected"
        )
        + "**. Reasoning and the descriptive fallback are in `docs/identification.md`."
    )
    add("")
    add("## 9. Donor table")
    add("")
    add(
        "One row per non-target ZCTA in the panel. `pools` lists the pools (A-D) the ZCTA enters. "
        "Insurance columns are W2/W3 changes; empty means not published for that ZCTA."
    )
    add("")
    cols = [
        "zcta",
        "panel_role",
        "county_fips",
        "risk_2018",
        "moratoria_w2_post",
        "moratoria_w3_post",
        "fires_total",
        "adjacent_proxy",
        "zhvi_complete_pre",
        "insurance_status",
        "w2_nonrenewal_change",
        "w2_premium_log_change",
        "decision",
        "pools",
        "reason",
    ]
    add(md_table(table[cols]))
    add("")
    return "\n".join(L)


def plot(d: dict, path: Path = PLOT_PATH) -> None:
    fits = d["fits"]
    a = fits["A_strict"]
    if not a.feasible:
        return
    panel_years = a.fit_years
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))
    ax = axes[0]
    tgt = d["target_series"]
    ax.plot(panel_years, _demean(tgt), color="#2a78d6", lw=2.2, label="Treated core mean")
    ax.plot(panel_years, _demean(a.synth), color="#eb6834", lw=1.8, label="Synthetic (pool A)")
    ax.plot(panel_years, _demean(a.pool_mean), color="#1baf7a", lw=1.4, ls="--", label="Pool A mean")
    ax.plot(
        panel_years,
        _demean(d["linkage"]["bay_index"]),
        color="#eda100",
        lw=1.4,
        ls=":",
        label="Bay Area index (linkage, not a control)",
    )
    ax.set_title(f"Pre-period log real ZHVI, demeaned ({panel_years[0]}-{panel_years[-1]})")
    ax.set_ylabel("log points")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    ax = axes[1]
    styles = zip(fits, ["#eb6834", "#7a5cc4", "#888888", "#1baf7a"], ["-", "-", "-", "--"])
    for name, color, ls in styles:
        pf = fits[name]
        if pf.feasible:
            same = name != "A_strict" and pf.donors == a.donors
            label = name + (" (same donors as A)" if same else "")
            gap = _demean(tgt) - _demean(pf.pool_mean)
            ax.plot(panel_years, gap, color=color, lw=1.5, ls=ls, label=label)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_title("Treated minus pool mean, pre-period only")
    ax.set_ylabel("log points")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130, metadata={"Software": None})
    plt.close(fig)


def main() -> None:
    panel, events = load_inputs()
    d = build_diagnostics(panel, events)
    a = d["fits"]["A_strict"]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(render(d))
    plot(d)
    print(f"wrote {REPORT_PATH.relative_to(ROOT)} and {PLOT_PATH.relative_to(ROOT)}")
    print(
        "verdict:",
        "feasible" if d["feasible"] else "rejected",
        "| pool A donors:",
        len(a.donors),
    )


if __name__ == "__main__":
    main()
