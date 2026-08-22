#!/usr/bin/env python3
"""Run a fixed, membership-aware stock Turtle grid on Norgate chunks."""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import pandas as pd

from deepstock.turtle import TurtleConfig, run_turtle_backtest, summarize_turtle_segment


def load_point_in_time_inputs(universe_dir: Path, etf_prices_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    price_files = sorted(universe_dir.glob("prices-*.csv"))
    membership_files = sorted(universe_dir.glob("membership-*.json"))
    if not price_files or not membership_files:
        raise ValueError("Point-in-time universe chunks are missing.")
    prices_long = pd.concat((pd.read_csv(path) for path in price_files), ignore_index=True)
    etf = pd.read_csv(etf_prices_path)
    etf = etf.loc[etf["symbol"].isin(["SPY", "SHY"])]
    prices_long = pd.concat((prices_long, etf), ignore_index=True)
    prices_long["date"] = pd.to_datetime(prices_long["date"])
    prices = prices_long.pivot(index="date", columns="symbol", values="adjusted_close").sort_index()
    prices = prices.dropna(subset=["SPY", "SHY"])
    risk_symbols = sorted(set(prices.columns).difference({"SPY", "SHY"}))
    eligibility = pd.DataFrame(False, index=prices.index, columns=risk_symbols)
    for path in membership_files:
        mapping = json.loads(path.read_text(encoding="utf-8"))
        for symbol, intervals in mapping.items():
            if symbol not in eligibility.columns:
                continue
            for interval in intervals:
                start = pd.Timestamp(interval["start"])
                end = pd.Timestamp(interval["end"])
                eligibility.loc[(eligibility.index >= start) & (eligibility.index <= end), symbol] = True
    return prices, eligibility


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe-dir", required=True)
    parser.add_argument("--etf-prices", required=True)
    parser.add_argument("--split-date", default="2021-08-23")
    parser.add_argument("--output-dir", default="artifacts/robustness/stock-turtle-point-in-time")
    args = parser.parse_args()

    prices, eligibility = load_point_in_time_inputs(Path(args.universe_dir), Path(args.etf_prices))
    risk_symbols = tuple(eligibility.columns)
    split = pd.Timestamp(args.split_date)
    if split not in prices.index:
        raise ValueError("Split date must be present in the combined price index.")
    in_dates = prices.index[prices.index < split]
    out_dates = prices.index[prices.index >= split]
    records: list[dict[str, object]] = []
    for entry, exit_, max_positions in itertools.product((20, 55), (10, 20), (3, 5)):
        if exit_ >= entry:
            continue
        config = TurtleConfig(
            risk_assets=risk_symbols,
            benchmark="SPY",
            safe_asset="SHY",
            entry_days=entry,
            exit_days=exit_,
            max_positions=max_positions,
        )
        result = run_turtle_backtest(prices, config, eligibility=eligibility)
        record = {
            "entry_days": entry,
            "exit_days": exit_,
            "max_positions": max_positions,
        }
        for prefix, dates in (("in_sample", in_dates), ("out_of_sample", out_dates)):
            summary = summarize_turtle_segment(result, dates)
            record.update({f"{prefix}_{key}": value for key, value in summary.items() if key not in {"config", "start", "end"}})
            record[f"{prefix}_start"] = summary["start"]
            record[f"{prefix}_end"] = summary["end"]
        records.append(record)
    table = pd.DataFrame(records).sort_values(
        ["in_sample_sharpe_ratio", "in_sample_maximum_drawdown"], ascending=[False, False]
    )
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    table.to_csv(output / "parameter_results.csv", index=False)
    manifest = {
        "actual_from": prices.index[0].date().isoformat(),
        "actual_to": prices.index[-1].date().isoformat(),
        "symbol_count": len(risk_symbols),
        "split_date": args.split_date,
        "parameter_count": len(records),
        "selection_policy": "Rank only by in-sample Sharpe and drawdown; no out-of-sample selection.",
        "membership_policy": "Historical index intervals control new entries; no current-survivor substitution.",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(table.to_csv(index=False))
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
