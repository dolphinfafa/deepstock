#!/usr/bin/env python3
"""Compare the frozen defensive ETF filter with a fixed three-state regime overlay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from deepstock.backtest import StrategyConfig, run_segmented_backtest


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
    parser.add_argument("--split-date", default="2021-08-23")
    parser.add_argument("--output-dir", default="artifacts/robustness/defensive-etf-regime")
    args = parser.parse_args()

    baseline = StrategyConfig(
        momentum_days=252,
        moving_average_days=200,
        top_k_assets=2,
        market_filter_days=200,
        exposure_above_filter=0.80,
        exposure_below_filter=0.40,
    )
    regime = StrategyConfig(
        momentum_days=252,
        moving_average_days=200,
        top_k_assets=2,
        regime_switching=True,
        regime_volatility_days=20,
        regime_baseline_volatility_days=252,
        regime_alert_exposure=0.40,
        regime_crisis_exposure=0.20,
    )
    prices = load_prices(Path(args.prices), baseline.symbols)
    results = []
    for name, config in (("baseline_80_40", baseline), ("three_state_regime", regime)):
        segmented = run_segmented_backtest(prices, args.split_date, config)
        results.append(
            {
                "model": name,
                "in_sample_total_return": segmented.in_sample.summary["total_return"],
                "in_sample_annualized_return": segmented.in_sample.summary["annualized_return"],
                "in_sample_sharpe_ratio": segmented.in_sample.summary["sharpe_ratio"],
                "in_sample_maximum_drawdown": segmented.in_sample.summary["maximum_drawdown"],
                "out_of_sample_total_return": segmented.out_of_sample.summary["total_return"],
                "out_of_sample_annualized_return": segmented.out_of_sample.summary["annualized_return"],
                "out_of_sample_sharpe_ratio": segmented.out_of_sample.summary["sharpe_ratio"],
                "out_of_sample_maximum_drawdown": segmented.out_of_sample.summary["maximum_drawdown"],
                "out_of_sample_total_turnover": segmented.out_of_sample.summary["total_turnover"],
                "out_of_sample_transaction_cost": segmented.out_of_sample.summary["total_transaction_cost"],
                "out_of_sample_benchmark_total_return": segmented.out_of_sample.summary["benchmark_total_return"],
            }
        )

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    table = pd.DataFrame(results)
    table.to_csv(output / "comparison.csv", index=False)
    manifest = {
        "split_date": args.split_date,
        "selection_policy": "No parameter selection; compare the frozen baseline with the fixed three-state rule.",
        "regime_policy": "Normal 80%; alert 40% when SPY <= 200-day average or 20-day annualized volatility >= 1.5x 252-day; crisis 20% when both trend and 2.0x volatility conditions hold.",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(table.to_csv(index=False))
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
