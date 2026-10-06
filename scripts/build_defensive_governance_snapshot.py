#!/usr/bin/env python3
"""Build a Defensive ETF governance snapshot from fixed, order-free reports."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from deepstock.data.store import read_clean_csv, read_clean_json, complete_panel, report_json

from deepstock.strategy_governance import POLICY_EFFECTIVE_DATE
from deepstock.defensive import require_frozen_config

def _completed_price_date(prices_path: Path) -> str:
    raw = read_clean_csv(prices_path)
    required = {"date", "symbol", "adjusted_close"}
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError(f"Price input missing columns: {sorted(missing)}")
    raw["date"] = pd.to_datetime(raw["date"])
    prices = complete_panel(raw.pivot(index="date", columns="symbol", values="adjusted_close").sort_index())
    if prices.empty:
        raise ValueError("Price input has no complete sessions.")
    return prices.index[-1].date().isoformat()


def _rolling_metrics(daily_path: Path, sessions: int) -> dict[str, float | int]:
    daily = pd.read_csv(daily_path, parse_dates=["date"])
    required = {"date", "portfolio_net_return", "turnover", "transaction_cost"}
    missing = required.difference(daily.columns)
    if missing:
        raise ValueError(f"Daily report missing columns: {sorted(missing)}")
    rolling = daily.tail(sessions).copy()
    if len(rolling) < sessions:
        raise ValueError(f"Daily report has fewer than {sessions} sessions.")
    returns = rolling["portfolio_net_return"].astype(float)
    equity = (1.0 + returns).cumprod()
    volatility = returns.std(ddof=0)
    return {
        "rolling_oos_sessions": int(len(rolling)),
        "rolling_oos_sharpe": float(returns.mean() / volatility * np.sqrt(252)) if volatility > 0 else 0.0,
        "rolling_oos_max_drawdown": float((equity / equity.cummax() - 1.0).min()),
        "annualized_turnover": float(rolling["turnover"].astype(float).sum() * 252 / len(rolling)),
        "costs_included": bool(rolling["transaction_cost"].notna().all()),
    }


def build_snapshot(
    prices_path: Path,
    daily_path: Path,
    walkforward_path: Path,
    manifest_path: Path,
    plan_path: Path,
    observations_path: Path,
    *,
    risk_review_passed: bool = False,
    as_of_date: str | None = None,
    summary_path: Path | None = None,
) -> dict[str, object]:
    """Create a current snapshot without judging or authorizing the strategy."""

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("strategy") != "adaptive_defensive_etf":
        raise ValueError("Plan must belong to adaptive_defensive_etf.")
    data_date = plan.get("data_date")
    if not isinstance(data_date, str):
        raise ValueError("Plan must contain an ISO data_date.")
    completed_date = _completed_price_date(prices_path)
    metrics = _rolling_metrics(daily_path, 252)
    walkforward = pd.read_csv(walkforward_path)
    if "total_return" not in walkforward.columns:
        raise ValueError("Walk-Forward report must contain total_return.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    summary = json.loads((summary_path or daily_path.parent / "summary.json").read_text(encoding="utf-8"))
    hashes = [require_frozen_config(report.get("config", {})) for report in (plan, manifest, summary)]
    if any(report.get("config_hash") != hashes[0] for report in (manifest, summary)):
        raise ValueError("Configuration checksums are missing or differ")
    if summary.get("daily_sha256") != hashlib.sha256(daily_path.read_bytes()).hexdigest():
        raise ValueError("Daily report checksum does not match its configuration summary")
    daily_dates = pd.read_csv(daily_path, usecols=["date"])["date"]
    if summary.get("end") != data_date or manifest.get("actual_to") != data_date or str(daily_dates.iloc[-1])[:10] != data_date:
        raise ValueError("Plan, daily report, and Walk-Forward evidence dates differ")
    selection_policy = str(manifest.get("selection_policy", ""))
    observations = []
    if observations_path.exists():
        observations = [json.loads(line) for line in observations_path.read_text(encoding="utf-8").splitlines() if line]
    effective_observations = [
        entry
        for entry in observations
        if isinstance(entry.get("data_date"), str)
        and POLICY_EFFECTIVE_DATE <= pd.Timestamp(entry["data_date"]).date() <= date.fromisoformat(data_date)
    ]
    unique_plan_ids = {entry.get("plan_id") for entry in effective_observations if entry.get("plan_id")}
    decision_date = date.fromisoformat(as_of_date or date.today().isoformat())
    effective_dates = sorted(
        {pd.Timestamp(entry["data_date"]).date() for entry in effective_observations if entry.get("plan_id")}
    )
    observation_calendar_days = (
        (decision_date - effective_dates[0]).days + 1 if effective_dates else 0
    )
    return {
        "strategy_id": "adaptive_defensive_etf",
        "config_hash": hashes[0],
        "evidence_revision": "defensive-config-reconciliation-20261005",
        "as_of_date": decision_date.isoformat(),
        "data_date": data_date,
        "parameters_frozen": "fixed" in selection_policy.lower(),
        "oos_parameter_selection_prohibited": "no rolling test result selected parameters" in selection_policy.lower(),
        "costs_included": metrics.pop("costs_included"),
        "data_fresh": data_date == completed_date,
        "risk_review_passed": risk_review_passed,
        "walk_forward_windows": int(len(walkforward)),
        "negative_walk_forward_windows": int((walkforward["total_return"] < 0).sum()),
        "shadow_sessions": len(unique_plan_ids),
        "shadow_observation_calendar_days": observation_calendar_days,
        **metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", required=True)
    parser.add_argument("--daily", required=True)
    parser.add_argument("--walkforward", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--observations", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--risk-review-passed", action="store_true")
    parser.add_argument("--as-of-date", help="ISO date of this decision; defaults to the local run date.")
    args = parser.parse_args()
    snapshot = build_snapshot(
        Path(args.prices),
        Path(args.daily),
        Path(args.walkforward),
        Path(args.manifest),
        Path(args.plan),
        Path(args.observations),
        risk_review_passed=args.risk_review_passed,
        as_of_date=args.as_of_date,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report_json(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(report_json(snapshot, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
