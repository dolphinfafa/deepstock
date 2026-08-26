#!/usr/bin/env python3
"""Diagnose ARC state persistence and conditional forward market outcomes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from deepstock.regime import ARCConfig, classify_market_regime, regime_statistics


def forward_outcomes(signals: pd.DataFrame, horizons: tuple[int, ...] = (5, 20, 60)) -> pd.DataFrame:
    """Report descriptive forward SPY returns by close-time controlled state."""

    benchmark = signals["benchmark"]
    rows: list[dict[str, object]] = []
    for horizon in horizons:
        forward_return = benchmark.shift(-horizon).div(benchmark).sub(1.0)
        for state, values in forward_return.groupby(signals["regime"], observed=True):
            valid = values.dropna()
            rows.append(
                {
                    "horizon_sessions": horizon,
                    "regime": str(state),
                    "observations": int(len(valid)),
                    "average_forward_return": float(valid.mean()),
                    "median_forward_return": float(valid.median()),
                    "negative_return_rate": float(valid.lt(0).mean()),
                }
            )
    return pd.DataFrame(rows)


def controller_row(name: str, signals: pd.DataFrame, config: ARCConfig) -> dict[str, object]:
    stats = regime_statistics(signals)
    states = signals["regime"].astype(str)
    transitions = states.ne(states.shift())
    bull_range = (
        states.shift().isin(["bull", "range"])
        & states.isin(["bull", "range"])
        & transitions
    ).sum()
    return {
        "controller": name,
        "confirmation_days": config.confirmation_days,
        "min_hold_days": config.min_hold_days,
        "reentry_cooldown_days": config.reentry_cooldown_days,
        "state_switches": stats["state_switches"],
        "switches_per_252_sessions": float(stats["state_switches"] / stats["sessions"] * 252),
        "average_hold_days": stats["average_hold_days"],
        "median_hold_days": stats["median_hold_days"],
        "bull_range_switches": int(bull_range),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", default="artifacts/research/norgate/etf_prices.csv")
    parser.add_argument("--output-dir", default="artifacts/robustness/arc-regime-diagnostics-2026-08-25")
    args = parser.parse_args()

    raw = pd.read_csv(args.prices)
    raw["date"] = pd.to_datetime(raw["date"])
    base = ARCConfig()
    prices = raw.pivot(index="date", columns="symbol", values="adjusted_close").sort_index()
    prices = prices.loc[:, list(base.risk_assets)].dropna()
    candidates = {
        "current_3_confirm_5_hold": base,
        "conservative_5_confirm_10_hold": ARCConfig(confirmation_days=5, min_hold_days=10),
        "anti_churn_5_confirm_10_hold_20_reentry": ARCConfig(
            confirmation_days=5, min_hold_days=10, reentry_cooldown_days=20
        ),
        "fast_defense_slow_recovery": ARCConfig(
            confirmation_days=5,
            min_hold_days=10,
            reentry_cooldown_days=20,
            risk_off_confirmation_days=2,
            risk_off_bypasses_min_hold=True,
            risk_off_bypasses_reentry_cooldown=True,
        ),
    }
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    for name, config in candidates.items():
        signals = classify_market_regime(prices, config)
        signals.to_csv(output / f"{name}_signals.csv", index_label="date")
        forward_outcomes(signals).to_csv(output / f"{name}_forward_outcomes.csv", index=False)
        records.append(controller_row(name, signals, config))

    comparison = pd.DataFrame(records)
    comparison.to_csv(output / "controller_comparison.csv", index=False)
    manifest = {
        "policy": "Predeclared controller diagnostic comparison only; no OOS result may select a production ARC rule.",
        "fixed_candidates": list(candidates),
        "forward_horizons_sessions": [5, 20, 60],
        "execution_status": "research_only_no_orders",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(comparison.to_csv(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
