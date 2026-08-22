#!/usr/bin/env python3
"""Run a fixed, membership-aware stock Turtle grid on Norgate chunks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from deepstock.regime import ARCConfig, classify_market_regime
from deepstock.bull import fixed_bull_candidates
from deepstock.turtle import run_turtle_backtest, summarize_turtle_segment


def load_point_in_time_inputs(
    universe_dir: Path, etf_prices_path: Path
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.Series]:
    price_files = sorted(universe_dir.glob("prices-*.csv"))
    membership_files = sorted(universe_dir.glob("membership-*.json"))
    if not price_files or not membership_files:
        raise ValueError("Point-in-time universe chunks are missing.")
    prices_long = pd.concat((pd.read_csv(path) for path in price_files), ignore_index=True)
    if "turnover" not in prices_long.columns:
        raise ValueError("Norgate chunks must contain turnover for the liquidity-controlled run.")
    etf = pd.read_csv(etf_prices_path)
    etf_symbols = [*ARCConfig().risk_assets, "SHY"]
    etf = etf.loc[etf["symbol"].isin(etf_symbols)]
    prices_long = pd.concat((prices_long, etf), ignore_index=True)
    prices_long["date"] = pd.to_datetime(prices_long["date"])
    prices = prices_long.pivot(index="date", columns="symbol", values="adjusted_close").sort_index()
    prices = prices.dropna(subset=["SPY", "SHY"])
    risk_symbols = sorted(set(prices.columns).difference(set(etf_symbols)))
    turnover = prices_long.pivot(index="date", columns="symbol", values="turnover").sort_index()
    turnover = turnover.reindex(index=prices.index, columns=risk_symbols)
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
    regime_prices = prices.loc[:, list(ARCConfig().risk_assets)].dropna()
    regime_signals = classify_market_regime(regime_prices)
    routes = regime_signals["strategy_route"].reindex(prices.index).fillna("defensive_etf")
    return prices, eligibility, turnover, routes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe-dir", required=True)
    parser.add_argument("--etf-prices", required=True)
    parser.add_argument("--split-date", default="2021-08-23")
    parser.add_argument("--output-dir", default="artifacts/robustness/stock-turtle-point-in-time")
    parser.add_argument("--candidate", help="Run one named fixed Bull candidate; omit to run all candidates.")
    parser.add_argument("--save-artifacts", action="store_true")
    args = parser.parse_args()

    prices, eligibility, turnover, routes = load_point_in_time_inputs(
        Path(args.universe_dir), Path(args.etf_prices)
    )
    risk_symbols = tuple(eligibility.columns)
    split = pd.Timestamp(args.split_date)
    if split not in prices.index:
        raise ValueError("Split date must be present in the combined price index.")
    in_dates = prices.index[prices.index < split]
    out_dates = prices.index[prices.index >= split]
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    candidates = fixed_bull_candidates(risk_symbols)
    if args.candidate:
        candidates = tuple(candidate for candidate in candidates if candidate.name == args.candidate)
        if not candidates:
            raise ValueError(f"Unknown candidate: {args.candidate}")
    for candidate in candidates:
        config = candidate.config
        record = {
            "candidate": candidate.name,
            "candidate_policy": candidate.policy,
            "entry_days": config.entry_days,
            "exit_days": config.exit_days,
            "max_positions": config.max_positions,
            "min_avg_turnover": config.min_avg_turnover,
        }
        standalone = run_turtle_backtest(
            prices, config, eligibility=eligibility, turnover=turnover
        )
        routed = run_turtle_backtest(
            prices, config, eligibility=eligibility, turnover=turnover,
            strategy_routes=routes,
        )
        if args.save_artifacts:
            routed.daily.to_csv(output / f"{candidate.name}_arc_routed_daily.csv", index_label="date")
            routed.target_weights.to_csv(output / f"{candidate.name}_arc_routed_targets.csv", index_label="date")
            routed.executed_weights.to_csv(output / f"{candidate.name}_arc_routed_executed.csv", index_label="date")
        for prefix, dates in (("in_sample", in_dates), ("out_of_sample", out_dates)):
            for mode, result in (("standalone", standalone), ("arc_routed", routed)):
                summary = summarize_turtle_segment(result, dates)
                record.update({f"{mode}_{prefix}_{key}": value for key, value in summary.items() if key not in {"config", "start", "end"}})
                record[f"{mode}_{prefix}_start"] = summary["start"]
                record[f"{mode}_{prefix}_end"] = summary["end"]
        records.append(record)
    new_table = pd.DataFrame(records)
    prior_path = output / "parameter_results.csv"
    if args.candidate and prior_path.exists():
        prior = pd.read_csv(prior_path)
        new_table = pd.concat([prior.loc[prior["candidate"] != args.candidate], new_table], ignore_index=True)
    table = new_table.sort_values(
        ["standalone_in_sample_sharpe_ratio", "standalone_in_sample_maximum_drawdown"], ascending=[False, False]
    )
    table.to_csv(output / "parameter_results.csv", index=False)
    manifest = {
        "actual_from": prices.index[0].date().isoformat(),
        "actual_to": prices.index[-1].date().isoformat(),
        "symbol_count": len(risk_symbols),
        "split_date": args.split_date,
        "parameter_count": len(table),
        "completed_candidates": sorted(table["candidate"].dropna().unique().tolist()),
        "selection_policy": "Rank only by standalone in-sample Sharpe and drawdown; no out-of-sample selection.",
        "comparison_policy": "Standalone and ARC-routed Turtle use identical parameters, point-in-time universe, liquidity data, dates, and transaction costs.",
        "membership_policy": "Historical index intervals control new entries; no current-survivor substitution.",
        "liquidity_policy": "Baseline candidates require prior 20-session average turnover >= USD 10,000,000; strict candidates require USD 25,000,000; existing positions are held until their normal exit.",
        "arc_route_policy": "Stock Turtle positions are allowed only during the ARC stock_turtle_research route; other routes force SHY on the next session.",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(table.to_csv(index=False))
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
