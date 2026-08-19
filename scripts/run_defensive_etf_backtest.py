#!/usr/bin/env python3
"""Run the defensive ETF backtest from a local adjusted-close CSV file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from deepstock.backtest import StrategyConfig, run_backtest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", required=True, help="CSV with date,symbol,adjusted_close columns.")
    parser.add_argument("--output-dir", default="artifacts/backtests/latest")
    parser.add_argument("--transaction-cost-bps", type=float, default=5.0)
    return parser.parse_args()


def load_prices(path: Path, config: StrategyConfig) -> pd.DataFrame:
    raw = pd.read_csv(path)
    required = {"date", "symbol", "adjusted_close"}
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError(f"CSV missing columns: {sorted(missing)}")
    raw["date"] = pd.to_datetime(raw["date"], utc=False)
    prices = raw.pivot(index="date", columns="symbol", values="adjusted_close").sort_index()
    return prices.loc[:, list(config.symbols)]


def main() -> int:
    args = parse_args()
    config = StrategyConfig(transaction_cost_bps=args.transaction_cost_bps)
    prices = load_prices(Path(args.prices), config)
    result = run_backtest(prices, config)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result.daily.to_csv(output_dir / "daily_results.csv", index_label="date")
    result.target_weights.to_csv(output_dir / "target_weights.csv", index_label="date")
    result.executed_weights.to_csv(output_dir / "executed_weights.csv", index_label="date")
    (output_dir / "summary.json").write_text(
        json.dumps(result.summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(result.summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
