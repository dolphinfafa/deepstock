#!/usr/bin/env python3
"""Run the conservative Turtle-style ETF or stock breakout backtest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from deepstock.turtle import TurtleConfig, run_turtle_backtest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", required=True)
    parser.add_argument("--output-dir", default="artifacts/backtests/turtle-latest")
    parser.add_argument("--entry-days", type=int, default=55)
    parser.add_argument("--exit-days", type=int, default=20)
    parser.add_argument(
        "--symbols", nargs="+", default=["SPY", "QQQ", "IWM", "TLT", "IEF", "GLD"],
        help="Risk symbols in the input CSV.",
    )
    parser.add_argument("--benchmark", default=None)
    parser.add_argument("--safe-asset", default="SHY")
    parser.add_argument("--max-positions", type=int, default=6)
    args = parser.parse_args()
    raw = pd.read_csv(args.prices)
    raw["date"] = pd.to_datetime(raw["date"])
    benchmark = args.benchmark or args.symbols[0]
    config = TurtleConfig(
        risk_assets=tuple(args.symbols),
        benchmark=benchmark,
        safe_asset=args.safe_asset,
        entry_days=args.entry_days,
        exit_days=args.exit_days,
        max_positions=args.max_positions,
    )
    prices = raw.pivot(index="date", columns="symbol", values="adjusted_close").sort_index()
    result = run_turtle_backtest(prices, config)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    result.daily.to_csv(output / "daily_results.csv", index_label="date")
    result.target_weights.to_csv(output / "target_weights.csv", index_label="date")
    result.executed_weights.to_csv(output / "executed_weights.csv", index_label="date")
    (output / "summary.json").write_text(json.dumps(result.summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result.summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
