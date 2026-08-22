#!/usr/bin/env python3
"""Backtest the fixed ARC range-route grid adapter on ETF history."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from deepstock.grid import GridConfig, run_grid_backtest
from deepstock.arc import assess_walk_forward, fixed_walk_forward_windows, summarize_walk_forward
from deepstock.regime import ARCConfig, classify_market_regime


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", required=True)
    parser.add_argument("--output-dir", default="artifacts/robustness/arc-grid")
    args = parser.parse_args()
    raw = pd.read_csv(args.prices)
    raw["date"] = pd.to_datetime(raw["date"])
    arc = ARCConfig()
    prices = raw.pivot(index="date", columns="symbol", values="adjusted_close").sort_index()
    prices = prices.loc[:, list(arc.risk_assets) + ["SHY"]].dropna()
    signals = classify_market_regime(prices.loc[:, list(arc.risk_assets)], arc)
    result = run_grid_backtest(prices, signals["strategy_route"], GridConfig())
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    result.daily.to_csv(output / "daily_results.csv", index_label="date")
    result.target_weights.to_csv(output / "target_weights.csv", index_label="date")
    result.summary["requested_route_sessions"] = int(signals["strategy_route"].eq("grid_research").sum())
    windows = fixed_walk_forward_windows(prices.index)
    if windows:
        # The grid result is a single causal run; windows are reporting slices,
        # and no test-period value is used to select GridConfig.
        walkforward = summarize_walk_forward(result, windows)
        walkforward.to_csv(output / "walkforward_results.csv", index=False)
        (output / "walkforward_acceptance.json").write_text(json.dumps(assess_walk_forward(walkforward), indent=2, sort_keys=True), encoding="utf-8")
    result.summary["walkforward_windows"] = len(windows)
    (output / "summary.json").write_text(json.dumps(result.summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result.summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
