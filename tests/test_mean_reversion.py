from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deepstock.mean_reversion import MeanReversionConfig, run_mean_reversion_backtest


def make_prices(days: int = 260) -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-02", periods=days)
    spy = np.linspace(100.0, 160.0, days)
    spy[230] = 142.0
    spy[231] = 95.0
    return pd.DataFrame({"SPY": spy, "SHY": np.full(days, 100.0)}, index=dates)


def test_enters_on_extreme_drop_and_executes_next_session() -> None:
    prices = make_prices()
    result = run_mean_reversion_backtest(prices)
    entry_dates = result.target_weights.index[result.target_weights["SPY"].eq(1.0)]

    assert len(entry_dates) == 1
    entry_date = entry_dates[0]
    assert result.executed_weights.loc[entry_date, "SPY"] == 0.0
    assert result.executed_weights.loc[entry_date + pd.offsets.BDay(), "SPY"] == 1.0
    assert result.target_weights.loc[entry_date + pd.offsets.BDay(), "SHY"] == 1.0
    assert result.summary["trend_filter_exit_count"] == 1


def test_future_prices_do_not_change_prior_targets() -> None:
    prices = make_prices()
    changed = prices.copy()
    changed.loc[changed.index[232] :, "SPY"] = 300.0
    original = run_mean_reversion_backtest(prices)
    altered = run_mean_reversion_backtest(changed)

    pd.testing.assert_frame_equal(
        original.target_weights.loc[: prices.index[231]],
        altered.target_weights.loc[: prices.index[231]],
    )


def test_weights_are_fully_allocated_and_costs_are_charged() -> None:
    result = run_mean_reversion_backtest(make_prices())

    assert result.target_weights.sum(axis=1).eq(1.0).all()
    assert result.summary["total_transaction_cost"] > 0
    assert (result.daily["portfolio_net_return"] <= result.daily["portfolio_gross_return"]).all()


def test_rejects_invalid_configuration_and_missing_prices() -> None:
    with pytest.raises(ValueError, match="entry Z-score"):
        MeanReversionConfig(entry_zscore=0.0, exit_zscore=0.0)
    with pytest.raises(ValueError, match="Missing adjusted-close columns"):
        run_mean_reversion_backtest(make_prices().drop(columns="SHY"))
