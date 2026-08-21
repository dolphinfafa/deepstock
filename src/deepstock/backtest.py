"""Vectorized, long-only backtest for the defensive ETF allocation strategy."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd


TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class StrategyConfig:
    """Parameters for a deliberately low-turnover, paper-only research strategy."""

    risk_assets: tuple[str, ...] = ("SPY", "QQQ", "IWM", "TLT", "IEF", "GLD")
    safe_asset: str = "SHY"
    benchmark: str = "SPY"
    momentum_days: int = 252
    moving_average_days: int = 200
    volatility_days: int = 63
    total_exposure: float = 0.80
    max_position_weight: float = 0.20
    transaction_cost_bps: float = 5.0
    top_k_assets: int | None = None
    market_filter_days: int | None = None
    exposure_above_filter: float | None = None
    exposure_below_filter: float | None = None

    def __post_init__(self) -> None:
        if not self.risk_assets:
            raise ValueError("At least one risk asset is required.")
        if self.safe_asset in self.risk_assets:
            raise ValueError("The safe asset cannot also be a risk asset.")
        if not self.benchmark:
            raise ValueError("A benchmark symbol is required.")
        if min(self.momentum_days, self.moving_average_days, self.volatility_days) < 2:
            raise ValueError("Lookback windows must be at least two trading days.")
        if not 0 < self.total_exposure <= 1:
            raise ValueError("Total exposure must be in (0, 1].")
        if not 0 < self.max_position_weight <= self.total_exposure:
            raise ValueError("Max position weight must be in (0, total exposure].")
        if self.transaction_cost_bps < 0:
            raise ValueError("Transaction costs cannot be negative.")
        if self.top_k_assets is not None and not 1 <= self.top_k_assets <= len(self.risk_assets):
            raise ValueError("top_k_assets must be between one and the risk-asset count.")
        if self.market_filter_days is not None and self.market_filter_days < 2:
            raise ValueError("market_filter_days must be at least two.")
        exposures = (self.exposure_above_filter, self.exposure_below_filter)
        if any(value is not None and not 0 < value <= 1 for value in exposures):
            raise ValueError("Filtered exposures must be in (0, 1].")
        if self.market_filter_days is not None and any(value is None for value in exposures):
            raise ValueError("Both filtered exposure values are required with a market filter.")

    @property
    def symbols(self) -> tuple[str, ...]:
        symbols = (*self.risk_assets, self.safe_asset)
        return symbols if self.benchmark in symbols else (*symbols, self.benchmark)


@dataclass
class BacktestResult:
    daily: pd.DataFrame
    target_weights: pd.DataFrame
    executed_weights: pd.DataFrame
    summary: dict[str, Any]


@dataclass
class SegmentedBacktestResult:
    """A contiguous in-sample/out-of-sample view of one backtest run."""

    full: BacktestResult
    in_sample: BacktestResult
    out_of_sample: BacktestResult
    split_date: pd.Timestamp


def validate_prices(prices: pd.DataFrame, config: StrategyConfig) -> pd.DataFrame:
    """Validate a daily adjusted-close matrix before any calculation."""

    if not isinstance(prices.index, pd.DatetimeIndex):
        raise ValueError("Prices must use a DatetimeIndex.")
    if prices.index.has_duplicates or not prices.index.is_monotonic_increasing:
        raise ValueError("Price dates must be unique and sorted ascending.")

    missing = set(config.symbols).difference(prices.columns)
    if missing:
        raise ValueError(f"Missing adjusted-close columns: {sorted(missing)}")

    selected = prices.loc[:, list(config.symbols)].copy().astype(float)
    if selected.empty:
        raise ValueError("Prices cannot be empty.")
    if selected.isna().any().any():
        raise ValueError("Prices contain missing values; repair the data before backtesting.")
    if (selected <= 0).any().any():
        raise ValueError("Adjusted-close values must be positive.")
    return selected


def _month_end_dates(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    dates = pd.Series(index, index=index)
    return pd.DatetimeIndex(dates.groupby(index.to_period("M")).max().to_numpy())


def _target_for_date(
    prices: pd.DataFrame,
    returns: pd.DataFrame,
    date: pd.Timestamp,
    config: StrategyConfig,
) -> pd.Series:
    weights = pd.Series(0.0, index=config.symbols, dtype=float)
    current = prices.loc[date]
    momentum = prices.pct_change(config.momentum_days, fill_method=None).loc[date]
    trend = prices.rolling(config.moving_average_days).mean().loc[date]
    volatility = returns.rolling(config.volatility_days).std(ddof=0).loc[date]

    eligible = [
        symbol
        for symbol in config.risk_assets
        if momentum[symbol] > 0
        and current[symbol] > trend[symbol]
        and volatility[symbol] > 0
    ]
    if eligible:
        if config.top_k_assets is not None:
            eligible = sorted(eligible, key=lambda symbol: momentum[symbol], reverse=True)[
                : config.top_k_assets
            ]
        exposure = config.total_exposure
        if config.market_filter_days is not None:
            market = prices[config.benchmark]
            market_average = market.rolling(config.market_filter_days).mean().loc[date]
            exposure = (
                config.exposure_above_filter
                if current[config.benchmark] > market_average
                else config.exposure_below_filter
            )
        inverse_volatility = 1.0 / volatility.loc[eligible]
        allocation = inverse_volatility / inverse_volatility.sum() * exposure
        weights.loc[eligible] = allocation.clip(upper=config.max_position_weight)

    weights.loc[config.safe_asset] = 1.0 - weights.sum()
    return weights


def _performance_summary(daily: pd.DataFrame, config: StrategyConfig) -> dict[str, Any]:
    net_returns = daily["portfolio_net_return"]
    equity_curve = daily["portfolio_equity"]
    periods = len(daily)
    annualized_return = equity_curve.iloc[-1] ** (TRADING_DAYS_PER_YEAR / periods) - 1
    annualized_volatility = net_returns.std(ddof=0) * np.sqrt(TRADING_DAYS_PER_YEAR)
    sharpe = (
        net_returns.mean() / net_returns.std(ddof=0) * np.sqrt(TRADING_DAYS_PER_YEAR)
        if net_returns.std(ddof=0) > 0
        else np.nan
    )
    drawdown = equity_curve / equity_curve.cummax() - 1.0
    benchmark_equity = daily["benchmark_equity"]

    return {
        "config": asdict(config),
        "start": daily.index[0].date().isoformat(),
        "end": daily.index[-1].date().isoformat(),
        "trading_days": periods,
        "total_return": float(equity_curve.iloc[-1] - 1.0),
        "annualized_return": float(annualized_return),
        "annualized_volatility": float(annualized_volatility),
        "sharpe_ratio": float(sharpe) if not np.isnan(sharpe) else None,
        "maximum_drawdown": float(drawdown.min()),
        "total_turnover": float(daily["turnover"].sum()),
        "total_transaction_cost": float(daily["transaction_cost"].sum()),
        "benchmark_total_return": float(benchmark_equity.iloc[-1] - 1.0),
    }


def _slice_result(result: BacktestResult, dates: pd.DatetimeIndex) -> BacktestResult:
    """Rebase a contiguous period while retaining weights set from prior history."""

    daily = result.daily.loc[dates].copy()
    daily["portfolio_equity"] = (1.0 + daily["portfolio_net_return"]).cumprod()
    benchmark_returns = daily["benchmark_equity"].pct_change(fill_method=None).fillna(0.0)
    # The first benchmark return belongs to the selected period, not the full run.
    benchmark_returns.iloc[0] = result.daily.loc[dates[0], "benchmark_equity"] / (
        result.daily.loc[: dates[0], "benchmark_equity"].iloc[-2]
        if len(result.daily.loc[: dates[0]]) > 1
        else 1.0
    ) - 1.0
    daily["benchmark_equity"] = (1.0 + benchmark_returns).cumprod()
    config = StrategyConfig(**result.summary["config"])
    return BacktestResult(
        daily=daily,
        target_weights=result.target_weights.loc[dates].copy(),
        executed_weights=result.executed_weights.loc[dates].copy(),
        summary=_performance_summary(daily, config),
    )


def run_backtest(prices: pd.DataFrame, config: StrategyConfig | None = None) -> BacktestResult:
    """Run an end-of-day backtest without look-ahead trading.

    Signals use a day's adjusted close. Targets are shifted one trading day before
    returns are applied, so the close that creates a signal never earns that
    same day's return. Transaction costs are charged on each target-weight change.
    """

    config = config or StrategyConfig()
    prices = validate_prices(prices, config)
    returns = prices.pct_change(fill_method=None).fillna(0.0)

    target_weights = pd.DataFrame(np.nan, index=prices.index, columns=config.symbols)
    target_weights.loc[prices.index[0], config.safe_asset] = 1.0
    for date in _month_end_dates(prices.index):
        target_weights.loc[date] = _target_for_date(prices, returns, date, config)
    target_weights = target_weights.ffill().fillna(0.0)

    # A target calculated at today's close can only affect tomorrow's return.
    executed_weights = target_weights.shift(1).fillna(0.0)
    traded_weight = executed_weights.diff().abs().sum(axis=1).fillna(
        executed_weights.iloc[0].abs().sum()
    )
    transaction_cost = traded_weight * config.transaction_cost_bps / 10_000
    portfolio_gross_return = (executed_weights * returns).sum(axis=1)
    portfolio_net_return = portfolio_gross_return - transaction_cost

    daily = pd.DataFrame(
        {
            "portfolio_gross_return": portfolio_gross_return,
            "transaction_cost": transaction_cost,
            "portfolio_net_return": portfolio_net_return,
            "turnover": traded_weight / 2.0,
            "portfolio_equity": (1.0 + portfolio_net_return).cumprod(),
            "benchmark_equity": (1.0 + returns[config.benchmark]).cumprod(),
        }
    )
    return BacktestResult(
        daily=daily,
        target_weights=target_weights,
        executed_weights=executed_weights,
        summary=_performance_summary(daily, config),
    )


def run_segmented_backtest(
    prices: pd.DataFrame,
    split_date: str | pd.Timestamp,
    config: StrategyConfig | None = None,
) -> SegmentedBacktestResult:
    """Run one causal backtest and report contiguous in/out-of-sample periods.

    ``split_date`` is the first out-of-sample trading session. Signals on that
    date may use only prior and same-day prices, exactly as in ``run_backtest``;
    no future observations are used. The out-of-sample equity curve is rebased
    to one, while its initial weights retain the position established from the
    preceding historical signal.
    """

    full = run_backtest(prices, config)
    boundary = pd.Timestamp(split_date)
    if boundary not in full.daily.index:
        raise ValueError("Split date must be a trading date present in the prices.")
    in_dates = full.daily.index[full.daily.index < boundary]
    out_dates = full.daily.index[full.daily.index >= boundary]
    if len(in_dates) < 2 or len(out_dates) < 2:
        raise ValueError("The split must leave at least two trading days in each period.")
    return SegmentedBacktestResult(
        full=full,
        in_sample=_slice_result(full, in_dates),
        out_of_sample=_slice_result(full, out_dates),
        split_date=boundary,
    )
