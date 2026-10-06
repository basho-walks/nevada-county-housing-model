import numpy as np
import pandas as pd
import pytest

from analysis import donors


@pytest.fixture(scope="module")
def inputs():
    return donors.load_inputs()


@pytest.fixture(scope="module")
def spied_run(inputs):
    """One full diagnostics run with the optimizer wrapped to record what it receives."""
    panel, events = inputs
    calls = []
    real = donors.solve_simplex_weights

    def spy(X, y, *args, **kwargs):
        calls.append((list(X.columns), list(y.index)))
        return real(X, y, *args, **kwargs)

    mp = pytest.MonkeyPatch()
    mp.setattr(donors, "solve_simplex_weights", spy)
    try:
        result = donors.build_diagnostics(panel, events)
    finally:
        mp.undo()
    return result, calls


def test_optimizer_sees_only_pre_period_columns(spied_run):
    result, calls = spied_run
    pre_end = result["windows"].pre_end
    fit_years = set(result["fits"]["A_strict"].fit_years)
    assert calls
    for x_cols, y_idx in calls:
        assert set(x_cols) <= fit_years
        assert set(y_idx) <= fit_years
        assert max(x_cols) <= pre_end


def test_select_weights_rejects_post_period_column():
    X = pd.DataFrame([[1.0, 2.0], [1.5, 2.5]], index=["a", "b"], columns=[2016, 2017])
    y = pd.Series([1.2, 2.2], index=[2016, 2017])
    with pytest.raises(ValueError, match="post-period"):
        donors.select_weights(X, y, pre_end=2016)


def test_weights_ignore_post_period_outcomes(inputs, spied_run):
    panel, _ = inputs
    result, _ = spied_run
    pre_end = result["windows"].pre_end
    shocked = panel.copy()
    post = shocked.year > pre_end
    shocked.loc[post, "zhvi_real_2025"] = shocked.loc[post, "zhvi_real_2025"] * 3.0
    a = result["fits"]["A_strict"]
    rerun = donors.fit_pool(shocked, "A_strict", a.donors, result["treated"], result["windows"])
    pd.testing.assert_series_equal(rerun.weights, a.weights)


def test_donor_table_one_row_per_zcta(inputs, spied_run):
    panel, _ = inputs
    table = spied_run[0]["table"]
    assert table.zcta.is_unique
    expected = set(panel.loc[panel.panel_role != "target", "zcta"])
    assert set(table.zcta) == expected
    assert table.reason.str.len().gt(0).all()
    assert set(table.decision) <= {"include", "exclude"}


def test_bay_area_sources_never_in_a_pool(spied_run):
    result, _ = spied_run
    table = result["table"]
    bay = set(table.loc[table.panel_role == "spillover_source", "zcta"])
    assert bay
    for pf in result["fits"].values():
        assert not bay & set(pf.donors)


def test_donor_without_insurance_data_is_unknown(spied_run):
    table = spied_run[0]["table"]
    no_data = table[table.ins_coverage_w2 < donors.INSURANCE_KNOWN_MIN]
    assert (no_data.insurance_status == "unknown").all()


def test_simplex_solver_recovers_convex_combination():
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(4, 12)), index=list("abcd"), columns=range(2000, 2012))
    true = pd.Series([0.6, 0.4, 0.0, 0.0], index=X.index)
    y = X.T @ true
    w = donors.solve_simplex_weights(X, y)
    assert w.min() >= 0
    assert w.sum() == pytest.approx(1.0)
    np.testing.assert_allclose(w.to_numpy(), true.to_numpy(), atol=1e-4)
