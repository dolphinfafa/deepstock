"""Create deterministic, order-free paper-trading plans."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from deepstock.backtest import StrategyConfig, run_backtest
from deepstock.defensive import config_hash, frozen_defensive_config
from deepstock.turtle import TurtleConfig, run_turtle_backtest


def create_paper_plan(
    prices: pd.DataFrame,
    config: TurtleConfig,
    *,
    generated_at: datetime | None = None,
    kill_switch: bool = False,
) -> dict[str, Any]:
    """Return a deterministic paper plan without any broker interaction."""

    if prices.empty:
        raise ValueError("Prices cannot be empty.")
    result = run_turtle_backtest(prices, config)
    latest = result.target_weights.iloc[-1]
    generated = generated_at or datetime.now(timezone.utc)
    payload = {
        "mode": "paper",
        "data_date": result.target_weights.index[-1].date().isoformat(),
        "config": asdict(config),
        "target_weights": {symbol: float(weight) for symbol, weight in latest.items()},
    }
    plan_id = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "plan_id": plan_id,
        "generated_at_utc": generated.astimezone(timezone.utc).isoformat(),
        "mode": "paper",
        "status": "blocked" if kill_switch else "ready_for_review",
        "kill_switch": kill_switch,
        **payload,
    }


def create_defensive_etf_plan(
    prices: pd.DataFrame,
    config: StrategyConfig | None = None,
    *,
    generated_at: datetime | None = None,
    kill_switch: bool = False,
) -> dict[str, Any]:
    """Return an order-free plan for the frozen defensive ETF configuration."""

    if prices.empty:
        raise ValueError("Prices cannot be empty.")
    config = config or frozen_defensive_config()
    result = run_backtest(prices, config)
    latest = result.target_weights.iloc[-1]
    generated = generated_at or datetime.now(timezone.utc)
    payload = {
        "strategy": "adaptive_defensive_etf",
        "mode": "paper",
        "data_date": result.target_weights.index[-1].date().isoformat(),
        "config": asdict(config),
        "target_weights": {symbol: float(weight) for symbol, weight in latest.items()},
    }
    plan_id = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "plan_id": plan_id,
        "config_hash": config_hash(config),
        "generated_at_utc": generated.astimezone(timezone.utc).isoformat(),
        "mode": "paper",
        "status": "blocked" if kill_switch else "ready_for_review",
        "kill_switch": kill_switch,
        **payload,
    }
