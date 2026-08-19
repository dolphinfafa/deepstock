from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deepstock.backtest import StrategyConfig, run_backtest, validate_prices


def make_prices(days: int = 520, declining: bool = False) -> pd.DataFrame:
    dates = pd.bdate_range("2022-01-03", periods=days)
    values: dict[str, np.ndarray] = {"SHY": 100 * np.cumprod(np.full(days, 1.00005))}
    risk_assets = ("SPY", "QQQ", "IWM", "TLT", "IEF", "GLD")
    for offset, symbol in enumerate(risk_assets):
        daily_return = 0.0005 + offset * 0.00001 + 0.0001 * np.sin(np.arange(days))
        if declining:
            daily_return = -0.0005 + 0.0001 * np.sin(np.arange(days))
        values[symbol] = 100 * np.cumprod(1 + daily_return)
    return pd.DataFrame(values, index=dates)


def test_declining_assets_fall_back_to_safe_asset() -> None:
    result = run_backtest(make_prices(declining=True))
    final_target = result.target_weights.iloc[-1]
    assert final_target["SHY"] == pytest.approx(1.0)
    assert final_target.drop("SHY").sum() == pytest.approx(0.0)


def test_signal_is_not_used_for_same_day_return() -> None:
    prices = make_prices()
    result = run_backtest(prices)
    changed_dates = result.target_weights.index[
        result.target_weights["SPY"].diff().fillna(0).ne(0)
    ]
    assert len(changed_dates) > 0
    rebalance_date = changed_dates[0]
    previous_weight = result.target_weights.loc[:rebalance_date, "SPY"].iloc[-2]
    assert result.executed_weights.loc[rebalance_date, "SPY"] == pytest.approx(previous_weight)


def test_transaction_cost_reduces_net_return() -> None:
    result = run_backtest(make_prices())
    assert (result.daily["portfolio_net_return"] <= result.daily["portfolio_gross_return"]).all()
    assert result.summary["total_transaction_cost"] > 0


def test_rejects_missing_symbols() -> None:
    prices = make_prices().drop(columns="GLD")
    with pytest.raises(ValueError, match="Missing adjusted-close columns"):
        validate_prices(prices, StrategyConfig())
