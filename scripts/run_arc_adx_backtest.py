#!/usr/bin/env python3
"""Compare the current ARC controller with one fixed ADX/DMI candidate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from deepstock.arc import (
    assess_walk_forward,
    fixed_walk_forward_windows,
    run_arc_portfolio,
    summarize_walk_forward,
)
from deepstock.backtest import StrategyConfig, run_backtest
from deepstock.bull import fixed_bull_candidates
from deepstock.grid import GridConfig, run_grid_backtest
from deepstock.regime import (
    ADXARCConfig,
    ARCConfig,
    ARC_EXECUTION_STATUS,
    StrategyRoute,
    classify_adx_market_regime,
    classify_market_regime,
    regime_statistics,
)
from deepstock.turtle import run_turtle_backtest
from run_point_in_time_turtle import load_point_in_time_inputs


def load_spy_ohlc(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path)
    required = {
        "date",
        "symbol",
        "adjusted_high",
        "adjusted_low",
        "adjusted_close",
    }
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError(f"OHLC CSV missing columns: {sorted(missing)}")
    raw = raw.loc[raw["symbol"].eq("SPY")].copy()
    if raw.empty or raw["date"].duplicated().any():
        raise ValueError("OHLC CSV must contain unique SPY daily bars.")
    raw["date"] = pd.to_datetime(raw["date"])
    return (
        raw.set_index("date")
        .sort_index()
        .rename(
            columns={
                "adjusted_high": "high",
                "adjusted_low": "low",
                "adjusted_close": "close",
            }
        )[["high", "low", "close"]]
    )


def transition_summary(signals: pd.DataFrame) -> dict[str, object]:
    statistics = regime_statistics(signals)
    states = signals["regime"].astype(str)
    changed = states.ne(states.shift())
    bull_range = (
        changed
        & states.shift().isin(["bull", "range"])
        & states.isin(["bull", "range"])
    )
    statistics["bull_range_switches"] = int(bull_range.sum())
    return statistics


def forward_outcomes(
    signals: pd.DataFrame, benchmark: pd.Series, group_column: str
) -> pd.DataFrame:
    """Describe fixed forward returns without using them to select a rule."""

    frame = signals.loc[:, [group_column]].copy()
    for horizon in (1, 5, 20, 60):
        frame[f"forward_{horizon}_session_return"] = (
            benchmark.shift(-horizon).div(benchmark).sub(1).reindex(frame.index)
        )
    grouped = frame.groupby(group_column, observed=True)
    result = grouped.size().rename("sessions").to_frame()
    for horizon in (1, 5, 20, 60):
        column = f"forward_{horizon}_session_return"
        result[f"average_{column}"] = grouped[column].mean()
    return result.reset_index()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe-dir", required=True)
    parser.add_argument("--etf-prices", required=True)
    parser.add_argument("--spy-ohlc", required=True)
    parser.add_argument("--bull-candidate", default="strict_liquidity_55_20_5")
    parser.add_argument("--output-dir", default="artifacts/robustness/arc-adx-2026-09-30")
    args = parser.parse_args()

    current_config = ARCConfig(confirmation_days=3, min_hold_days=5)
    adx_config = ADXARCConfig(
        adx_days=14,
        range_threshold=20,
        trend_threshold=25,
        strong_trend_threshold=30,
        super_trend_threshold=40,
        confirmation_days=3,
        min_hold_days=5,
    )
    prices, eligibility, turnover, _ = load_point_in_time_inputs(
        Path(args.universe_dir), Path(args.etf_prices)
    )
    ohlc = load_spy_ohlc(Path(args.spy_ohlc))
    etf_symbols = (*current_config.risk_assets, "SHY")
    etf = prices.loc[:, list(etf_symbols)].dropna()

    current_signals = classify_market_regime(
        etf.loc[:, list(current_config.risk_assets)], current_config
    )
    adx_signals = classify_adx_market_regime(ohlc, adx_config)
    valid_adx_dates = adx_signals.index[adx_signals["adx"].notna()]
    evaluation_dates = prices.index.intersection(etf.index).intersection(valid_adx_dates)
    if len(evaluation_dates) < 756:
        raise ValueError("ADX comparison requires at least 756 common sessions.")

    candidates = {
        candidate.name: candidate
        for candidate in fixed_bull_candidates(tuple(eligibility.columns))
    }
    if args.bull_candidate not in candidates:
        raise ValueError(f"Unknown fixed Bull candidate: {args.bull_candidate}")
    bull = run_turtle_backtest(
        prices,
        candidates[args.bull_candidate].config,
        eligibility=eligibility,
        turnover=turnover,
    ).target_weights
    defensive = run_backtest(etf, StrategyConfig()).target_weights

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    comparison_rows: list[dict[str, object]] = []
    controller_inputs = {
        "current_arc_3_confirm_5_hold": current_signals,
        "adx_14_3_confirm_5_hold": adx_signals,
    }
    for name, signals in controller_inputs.items():
        routes = signals["strategy_route"].reindex(prices.index).fillna(
            StrategyRoute.DEFENSIVE_ETF.value
        )
        grid = run_grid_backtest(
            etf.loc[:, ["SPY", "SHY"]],
            routes.reindex(etf.index).fillna(StrategyRoute.DEFENSIVE_ETF.value),
            GridConfig(),
        ).target_weights
        route_targets = {
            StrategyRoute.DEFENSIVE_ETF.value: defensive.reindex(prices.index).fillna(0.0),
            StrategyRoute.GRID_RESEARCH.value: grid.reindex(prices.index).fillna(0.0),
            StrategyRoute.STOCK_TURTLE_RESEARCH.value: bull,
        }
        result = run_arc_portfolio(
            prices.loc[evaluation_dates],
            routes.loc[evaluation_dates],
            {route: targets.loc[evaluation_dates] for route, targets in route_targets.items()},
            rebalance_band=0.10,
            route_cooldown_days=10,
        )
        windows = fixed_walk_forward_windows(evaluation_dates)
        walk_forward = summarize_walk_forward(result, windows)
        acceptance = assess_walk_forward(walk_forward)
        state_statistics = transition_summary(signals.reindex(evaluation_dates))
        evaluated_signals = signals.reindex(evaluation_dates)
        benchmark = etf["SPY"]

        controller_dir = output / name
        controller_dir.mkdir(parents=True, exist_ok=True)
        result.daily.to_csv(controller_dir / "daily_results.csv", index_label="date")
        evaluated_signals.to_csv(
            controller_dir / "daily_regime_signals.csv", index_label="date"
        )
        forward_outcomes(evaluated_signals, benchmark, "regime").to_csv(
            controller_dir / "regime_forward_outcomes.csv", index=False
        )
        if "adx_zone" in evaluated_signals:
            forward_outcomes(evaluated_signals, benchmark, "adx_zone").to_csv(
                controller_dir / "adx_zone_forward_outcomes.csv", index=False
            )
        walk_forward.to_csv(controller_dir / "walkforward_results.csv", index=False)
        (controller_dir / "summary.json").write_text(
            json.dumps(result.summary, indent=2, sort_keys=True), encoding="utf-8"
        )
        (controller_dir / "regime_statistics.json").write_text(
            json.dumps(state_statistics, indent=2, sort_keys=True), encoding="utf-8"
        )
        (controller_dir / "walkforward_acceptance.json").write_text(
            json.dumps(acceptance, indent=2, sort_keys=True), encoding="utf-8"
        )
        comparison_rows.append(
            {
                "controller": name,
                **{
                    key: result.summary[key]
                    for key in (
                        "total_return",
                        "annualized_return",
                        "sharpe_ratio",
                        "maximum_drawdown",
                        "total_turnover",
                        "total_transaction_cost",
                        "benchmark_total_return",
                    )
                },
                "controller_state_switches": state_statistics["state_switches"],
                "execution_route_switches": result.summary["state_switches"],
                "average_state_duration": state_statistics["average_hold_days"],
                "bull_range_switches": state_statistics["bull_range_switches"],
                "walkforward_windows": len(walk_forward),
                "negative_walkforward_windows": int((walk_forward["total_return"] < 0).sum()),
                "walkforward_threshold_checks_passed": acceptance["accepted"],
                "walkforward_minimum_window_gate": len(walk_forward) >= 6,
                "walkforward_accepted": bool(acceptance["accepted"] and len(walk_forward) >= 6),
            }
        )

    comparison = pd.DataFrame(comparison_rows)
    comparison.to_csv(output / "controller_comparison.csv", index=False)
    manifest = {
        "research_status": ARC_EXECUTION_STATUS,
        "paper_authorized": False,
        "candidate_selection": "predeclared; no OOS parameter selection",
        "comparison_policy": "Same dates, route modules, Bull candidate, risk layer, 5 bps costs, 10% rebalance band, and 10-session route cooldown.",
        "adx_policy": {
            "calculation": "Wilder ADX/DMI from split-adjusted SPY high, low, close",
            "days": 14,
            "zones": {"range": "<20", "chaos": "20-<25", "weak": "25-<30", "strong": "30-<40", "super": ">=40"},
            "routing": "ADX >=25 with +DI>-DI -> bull; ADX >=25 with -DI>=+DI -> defensive; bearish ADX >=40 -> crisis; otherwise range.",
            "confirmation_days": 3,
            "minimum_hold_days": 5,
        },
        "actual_from": evaluation_dates[0].date().isoformat(),
        "actual_to": evaluation_dates[-1].date().isoformat(),
        "sessions": len(evaluation_dates),
        "minimum_walkforward_windows": 6,
        "coverage_warning": "Massive OHLC subscription supplied only a recent approximately five-year sample; this is a limited diagnostic, not a controller selection result.",
        "bull_candidate": args.bull_candidate,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(comparison.to_csv(index=False))
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
