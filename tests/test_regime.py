from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deepstock.regime import (
    ARCConfig,
    MarketRegime,
    StrategyRoute,
    apply_regime_hysteresis,
    classify_market_regime,
)
from deepstock.grid import GridConfig, run_grid_backtest


def make_regime_prices(days: int = 600) -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-02", periods=days)
    trend = np.linspace(100, 180, days)
    return pd.DataFrame(
        {
            "SHY": np.linspace(100, 103, days),
            "SPY": trend,
            "QQQ": trend * 1.01,
            "IWM": trend * 0.99,
            "TLT": np.linspace(100, 110, days),
            "IEF": np.linspace(100, 105, days),
            "GLD": np.linspace(100, 120, days),
        },
        index=dates,
    )


def test_regime_classifier_routes_bull_market_to_stock_turtle() -> None:
    signals = classify_market_regime(make_regime_prices())
    assert signals.iloc[-1]["regime"] == MarketRegime.BULL
    assert signals.iloc[-1]["strategy_route"] == StrategyRoute.STOCK_TURTLE_RESEARCH


def test_regime_config_rejects_invalid_ratios() -> None:
    with pytest.raises(ValueError, match="Volatility ratios"):
        ARCConfig(defensive_volatility_ratio=2.0, crisis_volatility_ratio=1.5)


def test_regime_hysteresis_blocks_short_term_return_to_a_departed_state() -> None:
    dates = pd.bdate_range("2024-01-02", periods=5)
    raw = pd.Series(["range", "bull", "range", "bull", "range"], index=dates)
    controlled = apply_regime_hysteresis(
        raw,
        ARCConfig(confirmation_days=1, min_hold_days=1, reentry_cooldown_days=3),
    )

    assert controlled.tolist() == ["range", "bull", "bull", "bull", "range"]


def test_risk_off_control_bypasses_slow_recovery_guards() -> None:
    dates = pd.bdate_range("2024-01-02", periods=4)
    raw = pd.Series(["range", "bull", "defensive", "defensive"], index=dates)
    controlled = apply_regime_hysteresis(
        raw,
        ARCConfig(
            confirmation_days=5,
            min_hold_days=10,
            reentry_cooldown_days=20,
            risk_off_confirmation_days=2,
            risk_off_bypasses_min_hold=True,
            risk_off_bypasses_reentry_cooldown=True,
        ),
    )

    assert controlled.tolist() == ["range", "range", "range", "defensive"]


def test_regime_classifier_requires_complete_universe() -> None:
    with pytest.raises(ValueError, match="Missing regime price columns"):
        classify_market_regime(make_regime_prices().drop(columns="GLD"))


def test_grid_returns_to_safe_asset_outside_range_route() -> None:
    prices = make_regime_prices()
    routes = pd.Series(StrategyRoute.STOCK_TURTLE_RESEARCH.value, index=prices.index)
    result = run_grid_backtest(prices[["SPY", "SHY"]], routes, GridConfig())
    assert result.target_weights["SPY"].sum() == pytest.approx(0.0)
    assert result.target_weights["SHY"].eq(1.0).all()


def test_grid_exits_after_fixed_abnormal_move() -> None:
    prices = make_regime_prices()
    prices.loc[prices.index[-1], "SPY"] *= 0.85
    routes = pd.Series(StrategyRoute.GRID_RESEARCH.value, index=prices.index)
    result = run_grid_backtest(prices[["SPY", "SHY"]], routes, GridConfig(abnormal_move_exit=0.10))
    assert result.summary["abnormal_move_exits"] == 1
    assert result.target_weights.loc[prices.index[-1], "SPY"] == pytest.approx(0.0)
