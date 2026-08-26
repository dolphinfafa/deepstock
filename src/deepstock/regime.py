"""Deterministic market-regime controller for the Deepstock ARC research system."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import pandas as pd


class MarketRegime(StrEnum):
    CRISIS = "crisis"
    DEFENSIVE = "defensive"
    RANGE = "range"
    BULL = "bull"


class StrategyRoute(StrEnum):
    DEFENSIVE_ETF = "defensive_etf"
    GRID_RESEARCH = "grid_research"
    STOCK_TURTLE_RESEARCH = "stock_turtle_research"


ARC_EXECUTION_STATUS = "research_only_no_orders"


@dataclass(frozen=True)
class ARCConfig:
    """Fixed, explainable ARC regime thresholds used for research."""

    benchmark: str = "SPY"
    risk_assets: tuple[str, ...] = ("SPY", "QQQ", "IWM", "TLT", "IEF", "GLD")
    short_trend_days: int = 50
    long_trend_days: int = 200
    volatility_days: int = 20
    baseline_volatility_days: int = 252
    defensive_volatility_ratio: float = 1.5
    crisis_volatility_ratio: float = 2.0
    bull_breadth: float = 0.5
    confirmation_days: int = 3
    min_hold_days: int = 5
    reentry_cooldown_days: int = 0
    risk_off_confirmation_days: int | None = None
    risk_off_bypasses_min_hold: bool = False
    risk_off_bypasses_reentry_cooldown: bool = False

    def __post_init__(self) -> None:
        if self.benchmark not in self.risk_assets:
            raise ValueError("The benchmark must be in the risk asset universe.")
        if min(self.short_trend_days, self.long_trend_days, self.volatility_days) < 2:
            raise ValueError("Regime lookback windows must be at least two days.")
        if self.short_trend_days >= self.long_trend_days:
            raise ValueError("Short trend window must be shorter than the long window.")
        if self.volatility_days >= self.baseline_volatility_days:
            raise ValueError("Short volatility window must be shorter than its baseline.")
        if not 1.0 < self.defensive_volatility_ratio < self.crisis_volatility_ratio:
            raise ValueError("Volatility ratios must satisfy 1 < defensive < crisis.")
        if not 0 < self.bull_breadth <= 1:
            raise ValueError("Bull breadth must be in (0, 1].")
        if self.confirmation_days < 1 or self.min_hold_days < 1:
            raise ValueError("Confirmation and minimum hold days must be positive.")
        if self.reentry_cooldown_days < 0:
            raise ValueError("State reentry cooldown cannot be negative.")
        if self.risk_off_confirmation_days is not None and self.risk_off_confirmation_days < 1:
            raise ValueError("Risk-off confirmation days must be positive when set.")


def apply_regime_hysteresis(raw_regime: pd.Series, config: ARCConfig) -> pd.Series:
    """Apply fixed confirmation, minimum hold, and re-entry cooldown controls."""

    controlled = pd.Series(index=raw_regime.index, dtype="object")
    current = MarketRegime.RANGE.value
    pending: str | None = None
    pending_count = 0
    held = 0
    last_departed: dict[str, int] = {}
    for session, (date, candidate) in enumerate(raw_regime.items()):
        candidate = str(candidate)
        risk_off = candidate in {MarketRegime.CRISIS.value, MarketRegime.DEFENSIVE.value}
        confirmation_days = (
            config.risk_off_confirmation_days
            if risk_off and config.risk_off_confirmation_days is not None
            else config.confirmation_days
        )
        can_bypass_cooldown = risk_off and config.risk_off_bypasses_reentry_cooldown
        if candidate == current:
            pending = None
            pending_count = 0
        elif (
            candidate in last_departed
            and session - last_departed[candidate] < config.reentry_cooldown_days
            and not can_bypass_cooldown
        ):
            # Require a new uninterrupted confirmation after the cooldown.
            pending = None
            pending_count = 0
        else:
            if candidate == pending:
                pending_count += 1
            else:
                pending = candidate
                pending_count = 1
            can_leave_current = held >= config.min_hold_days or (
                risk_off and config.risk_off_bypasses_min_hold
            )
            if pending_count >= confirmation_days and can_leave_current:
                last_departed[current] = session
                current = candidate
                pending = None
                pending_count = 0
                held = 0
        controlled.at[date] = current
        held += 1
    return controlled


def classify_market_regime(
    prices: pd.DataFrame, config: ARCConfig | None = None
) -> pd.DataFrame:
    """Classify each session without using future prices.

    The returned signal is formed at the close of each date. A trading engine
    must shift the resulting route by one session before applying it to returns.
    """

    config = config or ARCConfig()
    if not isinstance(prices.index, pd.DatetimeIndex):
        raise ValueError("Prices must use a DatetimeIndex.")
    if prices.index.has_duplicates or not prices.index.is_monotonic_increasing:
        raise ValueError("Price dates must be unique and sorted ascending.")
    missing = set(config.risk_assets).difference(prices.columns)
    if missing:
        raise ValueError(f"Missing regime price columns: {sorted(missing)}")
    selected = prices.loc[:, list(config.risk_assets)].astype(float)
    if selected.empty or selected.isna().any().any() or (selected <= 0).any().any():
        raise ValueError("Regime prices must be non-empty, positive, and complete.")

    benchmark = selected[config.benchmark]
    returns = benchmark.pct_change(fill_method=None)
    short_ma = benchmark.rolling(config.short_trend_days).mean()
    long_ma = benchmark.rolling(config.long_trend_days).mean()
    short_vol = returns.rolling(config.volatility_days).std(ddof=0) * np.sqrt(252)
    baseline_vol = returns.rolling(config.baseline_volatility_days).std(ddof=0) * np.sqrt(252)
    breadth = (selected.gt(selected.rolling(config.long_trend_days).mean())).mean(axis=1)
    ratio = short_vol / baseline_vol.replace(0, np.nan)

    regime = pd.Series(MarketRegime.RANGE.value, index=selected.index, dtype="object")
    valid_vol = ratio.notna()
    crisis = valid_vol & benchmark.le(long_ma) & ratio.ge(config.crisis_volatility_ratio)
    defensive = valid_vol & (benchmark.le(long_ma) | ratio.ge(config.defensive_volatility_ratio))
    bull = valid_vol & benchmark.gt(long_ma) & benchmark.gt(short_ma) & breadth.ge(config.bull_breadth)
    regime.loc[defensive] = MarketRegime.DEFENSIVE.value
    regime.loc[crisis] = MarketRegime.CRISIS.value
    regime.loc[bull] = MarketRegime.BULL.value

    # Fixed hysteresis prevents one-day noise from repeatedly rerouting the
    # portfolio. The raw signal remains available for auditability.
    raw_regime = regime.copy()
    regime = apply_regime_hysteresis(raw_regime, config)

    routes = regime.map(
        {
            MarketRegime.CRISIS.value: StrategyRoute.DEFENSIVE_ETF.value,
            MarketRegime.DEFENSIVE.value: StrategyRoute.DEFENSIVE_ETF.value,
            MarketRegime.RANGE.value: StrategyRoute.GRID_RESEARCH.value,
            MarketRegime.BULL.value: StrategyRoute.STOCK_TURTLE_RESEARCH.value,
        }
    )
    return pd.DataFrame(
        {
            "regime": regime,
            "raw_regime": raw_regime,
            "strategy_route": routes,
            "benchmark": benchmark,
            "short_trend": short_ma,
            "long_trend": long_ma,
            "short_volatility": short_vol,
            "baseline_volatility": baseline_vol,
            "volatility_ratio": ratio,
            "breadth": breadth,
        },
        index=selected.index,
    )


def regime_statistics(signals: pd.DataFrame) -> dict[str, object]:
    """Return fixed, descriptive state duration and transition statistics."""
    if "regime" not in signals or signals.empty:
        raise ValueError("Signals must contain a non-empty regime column.")
    states = signals["regime"].astype(str)
    changes = states.ne(states.shift())
    run_ids = changes.cumsum()
    durations = states.groupby(run_ids, sort=False).size()
    transitions = pd.DataFrame({"from": states.shift(), "to": states})
    transitions = transitions.loc[changes & states.shift().notna()]
    return {
        "sessions": int(len(states)),
        "state_switches": int(changes.sum() - 1),
        "average_hold_days": float(durations.mean()),
        "median_hold_days": float(durations.median()),
        "state_durations": {
            str(state): {"sessions": int((states == state).sum()), "average_duration": float(durations[states.groupby(run_ids).first().eq(state)].mean())}
            for state in states.unique()
        },
        "transitions": transitions.value_counts().rename("count").reset_index().to_dict("records"),
    }
