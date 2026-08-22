"""Bounded, long-only mean-reversion grid research adapter."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from deepstock.backtest import BacktestResult, TRADING_DAYS_PER_YEAR
from deepstock.regime import StrategyRoute


@dataclass(frozen=True)
class GridConfig:
    asset: str = "SPY"
    safe_asset: str = "SHY"
    benchmark: str = "SPY"
    anchor_days: int = 50
    volatility_days: int = 20
    grid_spacing_volatility: float = 1.0
    grid_levels: int = 4
    max_exposure: float = 0.40
    transaction_cost_bps: float = 5.0
    abnormal_move_exit: float | None = 0.10

    def __post_init__(self) -> None:
        if min(self.anchor_days, self.volatility_days) < 2:
            raise ValueError("Grid lookback windows must be at least two days.")
        if self.grid_spacing_volatility <= 0 or self.grid_levels < 1:
            raise ValueError("Grid spacing and levels must be positive.")
        if not 0 < self.max_exposure <= 1:
            raise ValueError("Grid exposure must be in (0, 1].")
        if self.transaction_cost_bps < 0:
            raise ValueError("Transaction costs cannot be negative.")
        if self.abnormal_move_exit is not None and self.abnormal_move_exit <= 0:
            raise ValueError("Abnormal move threshold must be positive.")


def run_grid_backtest(
    prices: pd.DataFrame,
    strategy_routes: pd.Series,
    config: GridConfig | None = None,
) -> BacktestResult:
    """Run a bounded grid only while ARC routes to ``grid_research``.

    The signal buys one inventory unit for each full volatility-scaled grid
    level below a prior rolling anchor. It never shorts, uses no leverage, and
    returns to the safe asset whenever ARC leaves the range state.
    """

    config = config or GridConfig()
    required = {config.asset, config.safe_asset, config.benchmark}
    missing = required.difference(prices.columns)
    if missing:
        raise ValueError(f"Missing grid price columns: {sorted(missing)}")
    if not isinstance(prices.index, pd.DatetimeIndex) or prices.index.has_duplicates or not prices.index.is_monotonic_increasing:
        raise ValueError("Grid prices must use a unique, sorted DatetimeIndex.")
    if not strategy_routes.index.equals(prices.index):
        raise ValueError("Strategy routes must cover the exact price index.")
    selected = prices.loc[:, sorted(required)].astype(float)
    if selected.isna().any().any() or (selected <= 0).any().any():
        raise ValueError("Grid prices must be complete and positive.")

    asset = selected[config.asset]
    returns = selected.pct_change(fill_method=None).fillna(0.0)
    anchor = asset.rolling(config.anchor_days).mean().shift(1)
    volatility = returns[config.asset].rolling(config.volatility_days).std(ddof=0).shift(1)
    distance = (anchor - asset) / anchor / volatility.replace(0, np.nan)
    units = np.floor((distance / config.grid_spacing_volatility).clip(lower=0)).clip(upper=config.grid_levels)
    active = strategy_routes.eq(StrategyRoute.GRID_RESEARCH.value)
    abnormal = (
        returns[config.asset].abs().ge(config.abnormal_move_exit)
        if config.abnormal_move_exit is not None
        else pd.Series(False, index=prices.index)
    )
    active &= ~abnormal

    targets = pd.DataFrame(0.0, index=prices.index, columns=[config.asset, config.safe_asset])
    targets.loc[active, config.asset] = (units.loc[active] / config.grid_levels * config.max_exposure).fillna(0.0)
    targets[config.safe_asset] = 1.0 - targets[config.asset]
    executed = targets.shift(1).fillna(0.0)
    traded = executed.diff().abs().sum(axis=1).fillna(executed.iloc[0].abs().sum())
    costs = traded * config.transaction_cost_bps / 10_000
    gross = (executed[config.asset] * returns[config.asset] + executed[config.safe_asset] * returns[config.safe_asset])
    net = gross - costs
    equity = (1 + net).cumprod()
    benchmark_equity = (1 + returns[config.benchmark]).cumprod()
    daily = pd.DataFrame(
        {
            "portfolio_gross_return": gross,
            "transaction_cost": costs,
            "portfolio_net_return": net,
            "turnover": traded / 2,
            "portfolio_equity": equity,
            "benchmark_equity": benchmark_equity,
        }
    )
    volatility_net = net.std(ddof=0)
    drawdown = equity / equity.cummax() - 1
    summary: dict[str, Any] = {
        "config": asdict(config),
        "start": prices.index[0].date().isoformat(),
        "end": prices.index[-1].date().isoformat(),
        "trading_days": len(prices),
        "total_return": float(equity.iloc[-1] - 1),
        "annualized_return": float(equity.iloc[-1] ** (TRADING_DAYS_PER_YEAR / len(prices)) - 1),
        "annualized_volatility": float(volatility_net * np.sqrt(TRADING_DAYS_PER_YEAR)),
        "sharpe_ratio": float(net.mean() / volatility_net * np.sqrt(TRADING_DAYS_PER_YEAR)) if volatility_net > 0 else None,
        "maximum_drawdown": float(drawdown.min()),
        "total_turnover": float(daily["turnover"].sum()),
        "total_transaction_cost": float(daily["transaction_cost"].sum()),
            "benchmark_total_return": float(benchmark_equity.iloc[-1] - 1),
        "route_sessions": int(active.sum()),
        "abnormal_move_exits": int(abnormal.sum()),
        "maximum_inventory": float(targets[config.asset].max()),
    }
    return BacktestResult(daily, targets, executed, summary)
