#!/usr/bin/env python3
"""Run the predeclared SPY/SHY mean-reversion baseline and fixed validation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from deepstock.mean_reversion import (
    MeanReversionConfig,
    run_mean_reversion_backtest,
    summarize_mean_reversion_segment,
)


def _walk_forward(
    result, prices: pd.DataFrame, config: MeanReversionConfig, train_days: int, test_days: int, step_days: int
) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    start = train_days
    window = 0
    while start + test_days <= len(prices):
        dates = prices.index[start : start + test_days]
        summary = summarize_mean_reversion_segment(result, dates, config)
        records.append(
            {
                "window": window,
                "train_start": prices.index[start - train_days].date().isoformat(),
                "train_end": prices.index[start - 1].date().isoformat(),
                "test_start": summary.pop("start"),
                "test_end": summary.pop("end"),
                **summary,
            }
        )
        start += step_days
        window += 1
    if not records:
        raise ValueError("The dataset is too short for the requested Walk-Forward windows.")
    return pd.DataFrame(records)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", default="artifacts/research/norgate/etf_prices.csv")
    parser.add_argument("--output-dir", default="artifacts/robustness/spy-mean-reversion-2026-08-25")
    parser.add_argument("--split-date", default="2021-08-23")
    parser.add_argument("--train-days", type=int, default=504)
    parser.add_argument("--test-days", type=int, default=252)
    parser.add_argument("--step-days", type=int, default=252)
    args = parser.parse_args()

    raw = pd.read_csv(args.prices)
    raw["date"] = pd.to_datetime(raw["date"])
    prices = raw.pivot(index="date", columns="symbol", values="adjusted_close").sort_index()
    config = MeanReversionConfig()
    prices = prices.loc[:, list(config.symbols)].dropna()
    if pd.Timestamp(args.split_date) not in prices.index:
        raise ValueError("The fixed split date must be present in the input data.")

    result = run_mean_reversion_backtest(prices, config)
    in_dates = prices.index[prices.index < pd.Timestamp(args.split_date)]
    out_dates = prices.index[prices.index >= pd.Timestamp(args.split_date)]
    in_sample = summarize_mean_reversion_segment(result, in_dates, config)
    out_of_sample = summarize_mean_reversion_segment(result, out_dates, config)
    walk_forward = _walk_forward(result, prices, config, args.train_days, args.test_days, args.step_days)

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    result.daily.to_csv(output / "daily_results.csv", index_label="date")
    result.target_weights.to_csv(output / "target_weights.csv", index_label="date")
    result.executed_weights.to_csv(output / "executed_weights.csv", index_label="date")
    walk_forward.to_csv(output / "walkforward_results.csv", index=False)
    summary = {"full": result.summary, "in_sample": in_sample, "out_of_sample": out_of_sample}
    manifest = {
        "strategy": "spy_mean_reversion_research_only",
        "actual_from": prices.index[0].date().isoformat(),
        "actual_to": prices.index[-1].date().isoformat(),
        "config": result.summary["config"],
        "split_date": args.split_date,
        "walk_forward": {"train_days": args.train_days, "test_days": args.test_days, "step_days": args.step_days, "window_count": len(walk_forward)},
        "selection_policy": "All parameters and validation windows were fixed before inspecting out-of-sample results.",
        "execution_status": "research_only_no_orders",
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
