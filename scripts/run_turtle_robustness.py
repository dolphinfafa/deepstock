#!/usr/bin/env python3
"""Run a fixed-split parameter grid for the stock Turtle strategy."""

from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from deepstock.turtle import TurtleConfig, run_turtle_backtest, summarize_turtle_segment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", required=True)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--benchmark", default="SPY")
    parser.add_argument("--safe-asset", default="SHY")
    parser.add_argument("--split-date", required=True)
    parser.add_argument("--output-dir", default="artifacts/robustness/stock-turtle-latest")
    parser.add_argument("--entry-days", type=int, nargs="+", default=[20, 55])
    parser.add_argument("--exit-days", type=int, nargs="+", default=[10, 20])
    parser.add_argument("--max-positions", type=int, nargs="+", default=[3, 5])
    parser.add_argument("--max-per-sector", type=int, default=None)
    parser.add_argument("--sector-map", default="", help="Comma-separated SYMBOL=SECTOR pairs.")
    args = parser.parse_args()

    raw = pd.read_csv(args.prices)
    raw["date"] = pd.to_datetime(raw["date"])
    prices = raw.pivot(index="date", columns="symbol", values="adjusted_close").sort_index()
    symbols = (*args.symbols, args.safe_asset, args.benchmark)
    sector_map = tuple(
        tuple(item.split("=", 1)) for item in args.sector_map.split(",") if "=" in item
    )
    prices = prices.loc[:, list(dict.fromkeys(symbols))].dropna()
    split = pd.Timestamp(args.split_date)
    if split not in prices.index:
        raise ValueError("Split date must be present after complete-row filtering.")
    in_dates = prices.index[prices.index < split]
    out_dates = prices.index[prices.index >= split]
    if len(in_dates) < 2 or len(out_dates) < 2:
        raise ValueError("Split must leave at least two sessions per segment.")

    records: list[dict[str, object]] = []
    for entry, exit_, max_positions in itertools.product(
        args.entry_days, args.exit_days, args.max_positions
    ):
        if exit_ >= entry:
            continue
        config = TurtleConfig(
            risk_assets=tuple(args.symbols),
            benchmark=args.benchmark,
            safe_asset=args.safe_asset,
            entry_days=entry,
            exit_days=exit_,
            max_positions=max_positions,
            sector_by_symbol=sector_map,
            max_per_sector=args.max_per_sector,
        )
        result = run_turtle_backtest(prices, config)
        record: dict[str, object] = asdict(config)
        for prefix, dates in (("in_sample", in_dates), ("out_of_sample", out_dates)):
            summary = summarize_turtle_segment(result, dates)
            for key, value in summary.items():
                if key not in {"config", "start", "end"}:
                    record[f"{prefix}_{key}"] = value
            record[f"{prefix}_start"] = summary["start"]
            record[f"{prefix}_end"] = summary["end"]
        records.append(record)

    table = pd.DataFrame(records).sort_values(
        ["in_sample_sharpe_ratio", "in_sample_maximum_drawdown"],
        ascending=[False, False],
        na_position="last",
    )
    table.insert(0, "in_sample_rank", range(1, len(table) + 1))
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    table.to_csv(output / "parameter_results.csv", index=False)
    manifest = {
        "actual_from": prices.index[0].date().isoformat(),
        "actual_to": prices.index[-1].date().isoformat(),
        "rows_after_complete_filter": len(prices),
        "symbols": list(args.symbols),
        "benchmark": args.benchmark,
        "safe_asset": args.safe_asset,
        "split_date": args.split_date,
        "parameter_count": len(records),
        "selection_policy": "Rank only by in-sample Sharpe and drawdown; do not select using out-of-sample metrics.",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(table.to_csv(index=False))
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
