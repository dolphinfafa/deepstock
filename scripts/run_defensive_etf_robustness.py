#!/usr/bin/env python3
"""Evaluate predeclared defensive-ETF parameters across an in/out sample split."""

from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from deepstock.backtest import StrategyConfig, run_segmented_backtest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", required=True, help="CSV with date,symbol,adjusted_close columns.")
    parser.add_argument("--split-date", required=True, help="First out-of-sample trading date (YYYY-MM-DD).")
    parser.add_argument("--output-dir", default="artifacts/robustness/latest")
    parser.add_argument("--trend-windows", type=int, nargs="+", default=[150, 200, 250])
    parser.add_argument("--momentum-windows", type=int, nargs="+", default=[126, 252])
    parser.add_argument("--volatility-windows", type=int, nargs="+", default=[42, 63, 126])
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


def metric_columns(prefix: str, summary: dict[str, object]) -> dict[str, object]:
    return {
        f"{prefix}_{key}": value
        for key, value in summary.items()
        if key not in {"config", "start", "end"}
    } | {f"{prefix}_start": summary["start"], f"{prefix}_end": summary["end"]}


def main() -> int:
    args = parse_args()
    baseline = StrategyConfig(transaction_cost_bps=args.transaction_cost_bps)
    prices = load_prices(Path(args.prices), baseline)
    records: list[dict[str, object]] = []
    for trend, momentum, volatility in itertools.product(
        args.trend_windows, args.momentum_windows, args.volatility_windows
    ):
        config = StrategyConfig(
            moving_average_days=trend,
            momentum_days=momentum,
            volatility_days=volatility,
            transaction_cost_bps=args.transaction_cost_bps,
        )
        result = run_segmented_backtest(prices, args.split_date, config)
        record: dict[str, object] = asdict(config)
        record |= metric_columns("in_sample", result.in_sample.summary)
        record |= metric_columns("out_of_sample", result.out_of_sample.summary)
        records.append(record)

    table = pd.DataFrame(records).sort_values(
        ["in_sample_sharpe_ratio", "in_sample_maximum_drawdown"],
        ascending=[False, False],
        na_position="last",
    )
    table.insert(0, "in_sample_rank", range(1, len(table) + 1))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    table.to_csv(output_dir / "parameter_results.csv", index=False)
    manifest = {
        "prices": str(Path(args.prices)),
        "actual_from": prices.index[0].date().isoformat(),
        "actual_to": prices.index[-1].date().isoformat(),
        "rows": int(len(prices)),
        "symbols": list(prices.columns),
        "split_date": args.split_date,
        "parameter_count": len(records),
        "selection_policy": "Results are ranked only by in-sample Sharpe ratio, then in-sample maximum drawdown. Out-of-sample metrics must not select parameters.",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(table.to_csv(index=False))
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
