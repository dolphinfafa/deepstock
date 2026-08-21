#!/usr/bin/env python3
"""Run a fixed-split grid for adaptive defensive ETF allocation."""

from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from deepstock.backtest import StrategyConfig, run_backtest


def segment_summary(result, dates: pd.DatetimeIndex) -> dict[str, object]:
    daily = result.daily.loc[dates].copy()
    daily["portfolio_equity"] = (1 + daily["portfolio_net_return"]).cumprod()
    benchmark_returns = daily["benchmark_equity"].pct_change(fill_method=None).fillna(0.0)
    prior = result.daily.loc[: dates[0], "benchmark_equity"]
    benchmark_returns.iloc[0] = result.daily.loc[dates[0], "benchmark_equity"] / (
        prior.iloc[-2] if len(prior) > 1 else 1.0
    ) - 1.0
    daily["benchmark_equity"] = (1 + benchmark_returns).cumprod()
    equity = daily["portfolio_equity"]
    returns = daily["portfolio_net_return"]
    volatility = returns.std(ddof=0)
    drawdown = equity / equity.cummax() - 1
    return {
        "trading_days": len(daily),
        "total_return": float(equity.iloc[-1] - 1),
        "annualized_return": float(equity.iloc[-1] ** (252 / len(daily)) - 1),
        "sharpe_ratio": float(returns.mean() / volatility * (252**0.5)) if volatility > 0 else None,
        "maximum_drawdown": float(drawdown.min()),
        "total_turnover": float(daily["turnover"].sum()),
        "total_transaction_cost": float(daily["transaction_cost"].sum()),
        "benchmark_total_return": float(daily["benchmark_equity"].iloc[-1] - 1),
        "start": dates[0].date().isoformat(),
        "end": dates[-1].date().isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", required=True)
    parser.add_argument("--split-date", required=True)
    parser.add_argument("--output-dir", default="artifacts/robustness/adaptive-defensive-latest")
    args = parser.parse_args()
    raw = pd.read_csv(args.prices)
    raw["date"] = pd.to_datetime(raw["date"])
    baseline = StrategyConfig()
    prices = raw.pivot(index="date", columns="symbol", values="adjusted_close").sort_index()
    prices = prices.loc[:, list(baseline.symbols)].dropna()
    split = pd.Timestamp(args.split_date)
    in_dates = prices.index[prices.index < split]
    out_dates = prices.index[prices.index >= split]
    if split not in prices.index or len(in_dates) < 2 or len(out_dates) < 2:
        raise ValueError("Split must be a trading date with at least two sessions per segment.")

    records: list[dict[str, object]] = []
    for above, below, momentum, trend, top_k in itertools.product(
        (0.8, 1.0), (0.2, 0.4), (126, 252), (150, 200), (2, 3)
    ):
        if below >= above:
            continue
        config = StrategyConfig(
            momentum_days=momentum,
            moving_average_days=trend,
            top_k_assets=top_k,
            market_filter_days=trend,
            exposure_above_filter=above,
            exposure_below_filter=below,
        )
        result = run_backtest(prices, config)
        record: dict[str, object] = asdict(config)
        for prefix, dates in (("in_sample", in_dates), ("out_of_sample", out_dates)):
            summary = segment_summary(result, dates)
            record.update({f"{prefix}_{key}": value for key, value in summary.items()})
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
