import numpy as np
import pandas as pd
import pytest

from deepstock.arc import assess_walk_forward, fixed_walk_forward_windows, run_arc_portfolio
from deepstock.bull import fixed_bull_candidates
from deepstock.risk import RiskConfig, apply_risk_constraints


def test_fixed_walk_forward_uses_declared_lengths_and_step() -> None:
    index = pd.bdate_range("2020-01-01", periods=1260)
    windows = fixed_walk_forward_windows(index)
    assert len(windows) == 3
    assert len(windows[0][0]) == 504
    assert len(windows[0][1]) == 252
    assert windows[1][0][0] == index[252]


def test_walk_forward_acceptance_reports_fixed_failures() -> None:
    table = pd.DataFrame({"window": [1], "start": ["2020-01-01"], "end": ["2020-12-31"], "total_return": [-0.25], "maximum_drawdown": [-0.10], "turnover": [10.0]})
    report = assess_walk_forward(table)
    assert report["accepted"] is False
    assert report["failures"][0]["check"] == "severe_negative_return"


def test_bull_candidates_are_fixed_and_distinct() -> None:
    candidates = fixed_bull_candidates(("AAA", "BBB", "CCC", "DDD", "EEE"))
    assert len(candidates) == 6
    assert len({candidate.name for candidate in candidates}) == 6
    assert {candidate.config.entry_days for candidate in candidates} == {20, 55}
    assert all(candidate.config.transaction_cost_bps == 5.0 for candidate in candidates)


def test_risk_layer_caps_symbol_and_routes_invalid_data_to_safe() -> None:
    index = pd.bdate_range("2024-01-01", periods=3)
    targets = pd.DataFrame({"AAA": [0.9, 0.9, 0.9], "BBB": [0.2, 0.2, 0.2], "SHY": [0, 0, 0]}, index=index)
    returns = pd.DataFrame({"AAA": [0, 0, 0], "BBB": [0, 0, 0], "SHY": [0, 0, 0]}, index=index)
    clipped = apply_risk_constraints(targets, returns, RiskConfig(max_symbol_weight=0.2), safe_asset="SHY")
    assert clipped.loc[index[0], "AAA"] == pytest.approx(0.2)
    assert clipped.sum(axis=1).eq(1.0).all()


def test_risk_layer_routes_invalid_data_to_safe_asset() -> None:
    index = pd.bdate_range("2024-01-01", periods=2)
    targets = pd.DataFrame({"AAA": [0.4, 0.4], "SHY": [0.6, 0.6]}, index=index)
    returns = pd.DataFrame({"AAA": [0.0, 0.0], "SHY": [0.0, 0.0]}, index=index)
    valid = pd.Series([True, False], index=index)
    clipped = apply_risk_constraints(targets, returns, data_valid=valid, safe_asset="SHY")
    assert clipped.loc[index[1], "AAA"] == 0.0
    assert clipped.loc[index[1], "SHY"] == 1.0


def test_arc_accounts_route_switch_cost_separately() -> None:
    index = pd.bdate_range("2024-01-01", periods=8)
    prices = pd.DataFrame({"AAA": np.linspace(100, 102, 8), "SHY": np.linspace(100, 100.1, 8), "SPY": np.linspace(100, 103, 8)}, index=index)
    routes = pd.Series(["grid_research"] * 4 + ["defensive_etf"] * 4, index=index)
    frame = pd.DataFrame({"AAA": 0.4, "SHY": 0.6}, index=index)
    safe = pd.DataFrame({"AAA": 0.0, "SHY": 1.0}, index=index)
    result = run_arc_portfolio(prices, routes, {"grid_research": frame, "defensive_etf": safe})
    assert result.summary["state_switch_transaction_cost"] >= 0
    assert "reentry_cost" in result.daily
    assert {row["execution_route"] for row in result.summary["route_contribution"]} == {"grid_research", "defensive_etf"}
