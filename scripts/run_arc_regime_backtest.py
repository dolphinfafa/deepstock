#!/usr/bin/env python3
"""Backtest Deepstock ARC regime signals and their next-session benchmark behavior."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from deepstock.arc import route_conditioned_performance
from deepstock.regime import ARCConfig, classify_market_regime, regime_statistics, ARC_EXECUTION_STATUS


def load_prices(path: Path, symbols: tuple[str, ...]) -> pd.DataFrame:
    raw = pd.read_csv(path)
    required = {"date", "symbol", "adjusted_close"}
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError(f"CSV missing columns: {sorted(missing)}")
    raw["date"] = pd.to_datetime(raw["date"])
    prices = raw.pivot(index="date", columns="symbol", values="adjusted_close").sort_index()
    return prices.loc[:, list(symbols)].dropna()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", required=True)
    parser.add_argument("--output-dir", default="artifacts/robustness/arc-regime")
    args = parser.parse_args()

    config = ARCConfig()
    prices = load_prices(Path(args.prices), config.risk_assets)
    signals = classify_market_regime(prices, config)
    next_returns = prices[config.benchmark].pct_change(fill_method=None).shift(-1)
    signals["next_benchmark_return"] = next_returns
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    grouped = signals.dropna(subset=["next_benchmark_return"]).groupby(
        ["regime", "strategy_route"], observed=True
    )
    summary = grouped["next_benchmark_return"].agg(
        sessions="size", average_next_day_return="mean", cumulative_next_day_return=lambda x: (1 + x).prod() - 1
    ).reset_index()
    conditioned = route_conditioned_performance(signals, next_returns)
    conditioned.to_csv(output / "route_conditioned_performance.csv", index=False)
    statistics = regime_statistics(signals)
    signals.to_csv(output / "daily_regime_signals.csv", index_label="date")
    summary.to_csv(output / "regime_summary.csv", index=False)
    (output / "regime_statistics.json").write_text(json.dumps(statistics, indent=2, sort_keys=True), encoding="utf-8")
    manifest = {
        "system_name": "Deepstock ARC",
        "system_expansion": "Adaptive Regime Controller",
        "signal_policy": "Close-based regime signal; strategy route must execute from the next session.",
        "strategy_routes": {
            "crisis": "defensive_etf",
            "defensive": "defensive_etf",
            "range": "grid_research",
            "bull": "stock_turtle_research",
        },
        "execution_status": ARC_EXECUTION_STATUS,
        "paper_authorized": False,
        "confirmation_days": config.confirmation_days,
        "minimum_hold_days": config.min_hold_days,
        "oos_parameter_selection": "prohibited",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(summary.to_csv(index=False))
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
