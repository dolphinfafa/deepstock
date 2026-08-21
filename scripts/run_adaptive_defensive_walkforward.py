#!/usr/bin/env python3
"""Evaluate a fixed adaptive defensive ETF configuration across rolling windows."""

from __future__ import annotations

import argparse
import json
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
    parser.add_argument("--output-dir", default="artifacts/robustness/adaptive-defensive-walkforward")
    parser.add_argument("--train-days", type=int, default=504)
    parser.add_argument("--test-days", type=int, default=252)
    parser.add_argument("--step-days", type=int, default=252)
    args = parser.parse_args()

    raw = pd.read_csv(args.prices)
    raw["date"] = pd.to_datetime(raw["date"])
    base = StrategyConfig(
        momentum_days=252,
        moving_average_days=200,
        top_k_assets=2,
        market_filter_days=200,
        exposure_above_filter=0.8,
        exposure_below_filter=0.4,
    )
    prices = raw.pivot(index="date", columns="symbol", values="adjusted_close").sort_index()
    prices = prices.loc[:, list(base.symbols)].dropna()
    full = run_backtest(prices, base)
    records: list[dict[str, object]] = []
    start = args.train_days
    window = 0
    while start + args.test_days <= len(prices):
        test_dates = prices.index[start : start + args.test_days]
        summary = segment_summary(full, test_dates)
        records.append(
            {
                "window": window,
                "train_start": prices.index[start - args.train_days].date().isoformat(),
                "train_end": prices.index[start - 1].date().isoformat(),
                "test_start": summary["start"],
                "test_end": summary["end"],
                **{key: value for key, value in summary.items() if key not in {"start", "end"}},
            }
        )
        window += 1
        start += args.step_days

    if not records:
        raise ValueError("The dataset is too short for the requested rolling windows.")
    table = pd.DataFrame(records)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    table.to_csv(output / "walkforward_results.csv", index=False)
    manifest = {
        "actual_from": prices.index[0].date().isoformat(),
        "actual_to": prices.index[-1].date().isoformat(),
        "config": base.__dict__,
        "train_days": args.train_days,
        "test_days": args.test_days,
        "step_days": args.step_days,
        "window_count": len(records),
        "selection_policy": "Configuration was fixed from the earlier in-sample grid; no rolling test result selected parameters.",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(table.to_csv(index=False))
    print(json.dumps(manifest, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
