from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deepstock.backtest import (
    StrategyConfig,
    run_backtest,
    run_segmented_backtest,
    validate_prices,
)
from deepstock.turtle import TurtleConfig, run_turtle_backtest, summarize_turtle_segment
from deepstock.paper_plan import create_paper_plan


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


def test_segmented_backtest_rebases_out_of_sample_without_future_data() -> None:
    prices = make_prices(days=520)
    split_date = prices.index[350]
    result = run_segmented_backtest(prices, split_date)

    assert result.in_sample.daily.index[-1] < split_date
    assert result.out_of_sample.daily.index[0] == split_date
    assert result.out_of_sample.summary["start"] == split_date.date().isoformat()
    assert result.out_of_sample.daily["portfolio_equity"].iloc[0] == pytest.approx(
        1 + result.out_of_sample.daily["portfolio_net_return"].iloc[0]
    )
    assert result.out_of_sample.summary["end"] == prices.index[-1].date().isoformat()


def test_segmented_backtest_requires_a_present_split_date() -> None:
    with pytest.raises(ValueError, match="Split date must be a trading date"):
        run_segmented_backtest(make_prices(), "2020-01-01")


def test_turtle_strategy_uses_next_day_and_exits_declining_asset() -> None:
    prices = make_prices(days=520)
    result = run_turtle_backtest(prices, TurtleConfig(entry_days=20, exit_days=10, atr_days=10))
    assert result.executed_weights.iloc[0].sum() == pytest.approx(0.0)
    assert result.summary["total_transaction_cost"] >= 0


def test_turtle_config_rejects_invalid_windows() -> None:
    with pytest.raises(ValueError, match="Exit window"):
        TurtleConfig(entry_days=20, exit_days=20)


def test_turtle_config_limits_stock_positions() -> None:
    prices = make_prices(days=520)
    config = TurtleConfig(max_positions=2)
    result = run_turtle_backtest(prices, config)
    assert (result.target_weights.drop(columns="SHY") > 0).sum(axis=1).max() <= 2


def test_turtle_config_rejects_too_many_positions() -> None:
    with pytest.raises(ValueError, match="max_positions"):
        TurtleConfig(max_positions=7)


def test_benchmark_can_be_separate_from_trade_universe() -> None:
    prices = make_prices(days=520).assign(SPY=lambda frame: frame["SPY"])
    config = TurtleConfig(
        risk_assets=("QQQ", "IWM", "TLT", "IEF", "GLD"),
        benchmark="SPY",
        max_positions=3,
    )
    result = run_turtle_backtest(prices, config)
    assert "SPY" in result.target_weights.columns
    assert result.target_weights["SPY"].sum() == pytest.approx(0.0)


def test_paper_plan_is_idempotent_and_kill_switch_blocks() -> None:
    prices = make_prices(days=520)
    config = TurtleConfig(entry_days=20, exit_days=10, atr_days=10)
    generated = pd.Timestamp("2026-08-21T00:00:00Z").to_pydatetime()
    first = create_paper_plan(prices, config, generated_at=generated)
    second = create_paper_plan(prices, config, generated_at=generated)
    blocked = create_paper_plan(prices, config, generated_at=generated, kill_switch=True)
    assert first["plan_id"] == second["plan_id"]
    assert first["status"] == "ready_for_review"
    assert blocked["status"] == "blocked"
    assert blocked["plan_id"] == first["plan_id"]


def test_turtle_segment_summary_rebases_equity() -> None:
    prices = make_prices(days=520)
    result = run_turtle_backtest(prices, TurtleConfig(entry_days=20, exit_days=10, atr_days=10))
    dates = prices.index[300:]
    summary = summarize_turtle_segment(result, dates)
    assert summary["start"] == dates[0].date().isoformat()
    assert summary["end"] == dates[-1].date().isoformat()
