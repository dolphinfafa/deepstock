"""Predeclared Bull-route Turtle candidates for controlled comparison."""

from __future__ import annotations

from dataclasses import dataclass

from deepstock.turtle import TurtleConfig


@dataclass(frozen=True)
class BullCandidate:
    name: str
    config: TurtleConfig
    policy: str


def fixed_bull_candidates(
    risk_assets: tuple[str, ...],
    benchmark: str = "SPY",
    safe_asset: str = "SHY",
) -> tuple[BullCandidate, ...]:
    """Return the fixed Bull research set; this function performs no search."""
    common = {
        "risk_assets": risk_assets,
        "benchmark": benchmark,
        "safe_asset": safe_asset,
        "atr_days": 20,
        "stop_atr": 2.0,
        "risk_per_position": 0.01,
        "liquidity_days": 20,
        "min_avg_turnover": 10_000_000.0,
    }
    return (
        BullCandidate("baseline_20_10_3", TurtleConfig(**common, entry_days=20, exit_days=10, max_positions=3), "baseline breakout"),
        BullCandidate("baseline_55_20_5", TurtleConfig(**common, entry_days=55, exit_days=20, max_positions=5), "baseline breakout"),
        BullCandidate("short_breakout_20_10_5", TurtleConfig(**common, entry_days=20, exit_days=10, max_positions=5), "shorter breakout"),
        BullCandidate("long_breakout_55_20_3", TurtleConfig(**common, entry_days=55, exit_days=20, max_positions=3), "longer breakout"),
        BullCandidate("strict_liquidity_20_10_3", TurtleConfig(**{**common, "min_avg_turnover": 25_000_000.0}, entry_days=20, exit_days=10, max_positions=3), "USD 25m ADV filter"),
        BullCandidate("strict_liquidity_55_20_5", TurtleConfig(**{**common, "min_avg_turnover": 25_000_000.0}, entry_days=55, exit_days=20, max_positions=5), "USD 25m ADV filter"),
    )
