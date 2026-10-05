"""Single source of truth for the unchanged frozen defensive ETF policy."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from deepstock.backtest import StrategyConfig


def frozen_defensive_config() -> StrategyConfig:
    return StrategyConfig(
        momentum_days=252, moving_average_days=200, top_k_assets=2,
        market_filter_days=200, exposure_above_filter=0.8, exposure_below_filter=0.4,
    )


def config_hash(config: StrategyConfig | dict[str, Any]) -> str:
    values = asdict(config) if isinstance(config, StrategyConfig) else config
    return hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def require_frozen_config(values: dict[str, Any]) -> str:
    expected = config_hash(frozen_defensive_config())
    if config_hash(values) != expected:
        raise ValueError("Defensive evidence does not match the frozen Top-2/market-filter configuration")
    return expected
