"""A conservative, long-only Turtle-style ETF or stock breakout backtest."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from deepstock.backtest import BacktestResult, StrategyConfig, TRADING_DAYS_PER_YEAR, validate_prices


@dataclass(frozen=True)
class TurtleConfig(StrategyConfig):
    """Turtle parameters expressed in trading days and portfolio risk limits."""

    entry_days: int = 55
    exit_days: int = 20
    atr_days: int = 20
    stop_atr: float = 2.0
    risk_per_position: float = 0.01
    max_positions: int = 5

    def __post_init__(self) -> None:
        super().__post_init__()
        if min(self.entry_days, self.exit_days, self.atr_days) < 2:
            raise ValueError("Turtle lookback windows must be at least two days.")
        if self.exit_days >= self.entry_days:
            raise ValueError("Exit window must be shorter than entry window.")
        if self.stop_atr <= 0 or self.risk_per_position <= 0:
            raise ValueError("ATR stop and per-position risk must be positive.")
        if self.max_positions < 1 or self.max_positions > len(self.risk_assets):
            raise ValueError("max_positions must be between one and the risk-asset count.")


def _summary(daily: pd.DataFrame, config: TurtleConfig) -> dict[str, Any]:
    returns = daily["portfolio_net_return"]
    equity = daily["portfolio_equity"]
    volatility = returns.std(ddof=0)
    drawdown = equity / equity.cummax() - 1.0
    return {
        "config": asdict(config),
        "start": daily.index[0].date().isoformat(),
        "end": daily.index[-1].date().isoformat(),
        "trading_days": len(daily),
        "total_return": float(equity.iloc[-1] - 1.0),
        "annualized_return": float(equity.iloc[-1] ** (TRADING_DAYS_PER_YEAR / len(daily)) - 1),
        "annualized_volatility": float(volatility * np.sqrt(TRADING_DAYS_PER_YEAR)),
        "sharpe_ratio": float(returns.mean() / volatility * np.sqrt(TRADING_DAYS_PER_YEAR))
        if volatility > 0 else None,
        "maximum_drawdown": float(drawdown.min()),
        "total_turnover": float(daily["turnover"].sum()),
        "total_transaction_cost": float(daily["transaction_cost"].sum()),
        "benchmark_total_return": float(daily["benchmark_equity"].iloc[-1] - 1.0),
    }


def run_turtle_backtest(prices: pd.DataFrame, config: TurtleConfig | None = None) -> BacktestResult:
    """Run a close-only Turtle breakout model with next-session execution."""

    config = config or TurtleConfig()
    prices = validate_prices(prices, config)
    returns = prices.pct_change(fill_method=None).fillna(0.0)
    risk = prices.loc[:, list(config.risk_assets)]
    entry = risk.rolling(config.entry_days).max().shift(1)
    exit_ = risk.rolling(config.exit_days).min().shift(1)
    atr_pct = returns[risk.columns].abs().rolling(config.atr_days).mean().shift(1)

    targets = pd.DataFrame(0.0, index=prices.index, columns=config.symbols)
    active: set[str] = set()
    for date in prices.index:
        for symbol in tuple(active):
            if pd.notna(exit_.at[date, symbol]) and risk.at[date, symbol] < exit_.at[date, symbol]:
                active.remove(symbol)
        for symbol in config.risk_assets:
            if symbol not in active and pd.notna(entry.at[date, symbol]) and risk.at[date, symbol] > entry.at[date, symbol]:
                active.add(symbol)
        if len(active) > config.max_positions:
            active = set(
                sorted(
                    active,
                    key=lambda symbol: (
                        risk.at[date, symbol] / entry.at[date, symbol]
                        if pd.notna(entry.at[date, symbol]) and entry.at[date, symbol] > 0
                        else 0.0
                    ),
                    reverse=True,
                )[: config.max_positions]
            )
        if active:
            raw = pd.Series(
                {
                    symbol: min(
                        config.max_position_weight,
                        config.risk_per_position / (config.stop_atr * atr_pct.at[date, symbol]),
                    )
                    for symbol in active
                    if pd.notna(atr_pct.at[date, symbol]) and atr_pct.at[date, symbol] > 0
                },
                dtype=float,
            )
            if not raw.empty:
                targets.loc[date, raw.index] = (raw / raw.sum() * config.total_exposure).clip(
                    upper=config.max_position_weight
                )
        targets.loc[date, config.safe_asset] = 1.0 - targets.loc[date].sum()

    executed = targets.shift(1).fillna(0.0)
    traded = executed.diff().abs().sum(axis=1).fillna(executed.iloc[0].abs().sum())
    costs = traded * config.transaction_cost_bps / 10_000
    net = (executed * returns).sum(axis=1) - costs
    daily = pd.DataFrame(
        {
            "portfolio_gross_return": (executed * returns).sum(axis=1),
            "transaction_cost": costs,
            "portfolio_net_return": net,
            "turnover": traded / 2.0,
            "portfolio_equity": (1.0 + net).cumprod(),
            "benchmark_equity": (1.0 + returns[config.benchmark]).cumprod(),
        }
    )
    return BacktestResult(daily, targets, executed, _summary(daily, config))


def summarize_turtle_segment(result: BacktestResult, dates: pd.DatetimeIndex) -> dict[str, Any]:
    """Rebase a contiguous segment of a causal Turtle run for reporting."""

    daily = result.daily.loc[dates].copy()
    daily["portfolio_equity"] = (1.0 + daily["portfolio_net_return"]).cumprod()
    benchmark_returns = daily["benchmark_equity"].pct_change(fill_method=None).fillna(0.0)
    first = dates[0]
    prior = result.daily.loc[:first, "benchmark_equity"]
    benchmark_returns.iloc[0] = result.daily.loc[first, "benchmark_equity"] / (
        prior.iloc[-2] if len(prior) > 1 else 1.0
    ) - 1.0
    daily["benchmark_equity"] = (1.0 + benchmark_returns).cumprod()
    return _summary(daily, TurtleConfig(**result.summary["config"]))
