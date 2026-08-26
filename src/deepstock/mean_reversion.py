"""Fixed-rule, long-only SPY mean-reversion research backtest."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from deepstock.backtest import BacktestResult, TRADING_DAYS_PER_YEAR


@dataclass(frozen=True)
class MeanReversionConfig:
    """Predeclared parameters for the independent SPY/SHY baseline."""

    asset: str = "SPY"
    safe_asset: str = "SHY"
    benchmark: str = "SPY"
    mean_days: int = 20
    trend_days: int = 200
    entry_zscore: float = -2.0
    exit_zscore: float = 0.0
    transaction_cost_bps: float = 5.0

    def __post_init__(self) -> None:
        if not self.asset or not self.safe_asset or not self.benchmark:
            raise ValueError("Asset, safe asset, and benchmark symbols are required.")
        if self.asset == self.safe_asset:
            raise ValueError("The risk asset and safe asset must differ.")
        if min(self.mean_days, self.trend_days) < 2:
            raise ValueError("Mean-reversion lookback windows must be at least two days.")
        if self.entry_zscore >= self.exit_zscore:
            raise ValueError("The entry Z-score must be below the exit Z-score.")
        if self.transaction_cost_bps < 0:
            raise ValueError("Transaction costs cannot be negative.")

    @property
    def symbols(self) -> tuple[str, ...]:
        symbols = (self.asset, self.safe_asset)
        return symbols if self.benchmark in symbols else (*symbols, self.benchmark)


def _validate_prices(prices: pd.DataFrame, config: MeanReversionConfig) -> pd.DataFrame:
    if not isinstance(prices.index, pd.DatetimeIndex):
        raise ValueError("Prices must use a DatetimeIndex.")
    if prices.index.has_duplicates or not prices.index.is_monotonic_increasing:
        raise ValueError("Price dates must be unique and sorted ascending.")
    missing = set(config.symbols).difference(prices.columns)
    if missing:
        raise ValueError(f"Missing adjusted-close columns: {sorted(missing)}")
    selected = prices.loc[:, list(config.symbols)].astype(float).copy()
    if selected.empty:
        raise ValueError("Prices cannot be empty.")
    if selected.isna().any().any() or (selected <= 0).any().any():
        raise ValueError("Prices must be complete and positive.")
    return selected


def _summarize(daily: pd.DataFrame, config: MeanReversionConfig, extras: dict[str, int | float]) -> dict[str, Any]:
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
        "annualized_return": float(equity.iloc[-1] ** (TRADING_DAYS_PER_YEAR / len(daily)) - 1.0),
        "annualized_volatility": float(volatility * np.sqrt(TRADING_DAYS_PER_YEAR)),
        "sharpe_ratio": float(returns.mean() / volatility * np.sqrt(TRADING_DAYS_PER_YEAR)) if volatility > 0 else None,
        "maximum_drawdown": float(drawdown.min()),
        "total_turnover": float(daily["turnover"].sum()),
        "total_transaction_cost": float(daily["transaction_cost"].sum()),
        "benchmark_total_return": float(daily["benchmark_equity"].iloc[-1] - 1.0),
        **extras,
    }


def _average_holding_days(target_weights: pd.DataFrame, asset: str) -> float:
    in_position = target_weights[asset].eq(1.0)
    groups = in_position.ne(in_position.shift(fill_value=False)).cumsum()
    lengths = in_position.groupby(groups).sum()
    holding_periods = lengths[lengths > 0]
    return float(holding_periods.mean()) if not holding_periods.empty else 0.0


def run_mean_reversion_backtest(
    prices: pd.DataFrame, config: MeanReversionConfig | None = None
) -> BacktestResult:
    """Backtest the fixed close-to-next-session SPY/SHY mean-reversion rule."""

    config = config or MeanReversionConfig()
    prices = _validate_prices(prices, config)
    asset = prices[config.asset]
    returns = prices.pct_change(fill_method=None).fillna(0.0)

    # Shifting rolling statistics makes their information set explicit: only
    # observations strictly before today's close define the reference values.
    rolling_mean = asset.rolling(config.mean_days).mean().shift(1)
    rolling_std = asset.rolling(config.mean_days).std(ddof=0).shift(1)
    trend_mean = asset.rolling(config.trend_days).mean().shift(1)
    zscore = (asset - rolling_mean) / rolling_std.replace(0.0, np.nan)

    targets = pd.DataFrame(0.0, index=prices.index, columns=config.symbols)
    holding_asset = False
    entries = 0
    mean_exits = 0
    trend_exits = 0
    for date in prices.index:
        trend_eligible = bool(pd.notna(trend_mean.loc[date]) and asset.loc[date] > trend_mean.loc[date])
        current_zscore = zscore.loc[date]
        if holding_asset:
            if not trend_eligible:
                holding_asset = False
                trend_exits += 1
            elif pd.notna(current_zscore) and current_zscore >= config.exit_zscore:
                holding_asset = False
                mean_exits += 1
        elif trend_eligible and pd.notna(current_zscore) and current_zscore <= config.entry_zscore:
            holding_asset = True
            entries += 1

        targets.loc[date, config.asset if holding_asset else config.safe_asset] = 1.0

    # A closing signal first earns the following session's return.
    executed = targets.shift(1).fillna(0.0)
    traded = executed.diff().abs().sum(axis=1).fillna(executed.iloc[0].abs().sum())
    costs = traded * config.transaction_cost_bps / 10_000
    gross = (executed * returns).sum(axis=1)
    net = gross - costs
    equity = (1.0 + net).cumprod()
    benchmark_equity = (1.0 + returns[config.benchmark]).cumprod()
    daily = pd.DataFrame(
        {
            "portfolio_gross_return": gross,
            "transaction_cost": costs,
            "portfolio_net_return": net,
            "turnover": traded / 2.0,
            "portfolio_equity": equity,
            "benchmark_equity": benchmark_equity,
            "zscore": zscore,
            "trend_eligible": trend_mean.notna() & asset.gt(trend_mean),
        }
    )
    extras = {
        "entry_count": entries,
        "mean_reversion_exit_count": mean_exits,
        "trend_filter_exit_count": trend_exits,
        "average_holding_days": _average_holding_days(targets, config.asset),
    }
    return BacktestResult(daily, targets, executed, _summarize(daily, config, extras))


def summarize_mean_reversion_segment(
    result: BacktestResult, dates: pd.DatetimeIndex, config: MeanReversionConfig | None = None
) -> dict[str, Any]:
    """Rebase a contiguous reporting segment without recalculating signals."""

    if dates.empty or not dates.isin(result.daily.index).all():
        raise ValueError("Segment dates must be non-empty backtest dates.")
    config = config or MeanReversionConfig(**result.summary["config"])
    daily = result.daily.loc[dates].copy()
    daily["portfolio_equity"] = (1.0 + daily["portfolio_net_return"]).cumprod()
    benchmark_returns = result.daily["benchmark_equity"].pct_change(fill_method=None).fillna(0.0).loc[dates]
    daily["benchmark_equity"] = (1.0 + benchmark_returns).cumprod()
    targets = result.target_weights.loc[dates]
    entries = int(targets[config.asset].eq(1.0).mul(~targets[config.asset].eq(1.0).shift(fill_value=False)).sum())
    extras = {
        "entry_count": entries,
        "mean_reversion_exit_count": None,
        "trend_filter_exit_count": None,
        "average_holding_days": _average_holding_days(targets, config.asset),
    }
    return _summarize(daily, config, extras)
