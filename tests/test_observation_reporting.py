from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from deepstock.defensive import config_hash, frozen_defensive_config
from deepstock.observation_reporting import validate_bundle


def valid_bundle():
    config = asdict(frozen_defensive_config())
    digest = config_hash(config)
    return {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "plan": {"strategy": "adaptive_defensive_etf", "config": config, "config_hash": digest, "plan_id": "latest", "data_date": "2026-09-01", "target_weights": {"SHY": 1}},
        "summary": {"config": config, "config_hash": digest, "daily_sha256": "a" * 64, "start": "2005-01-01", "end": "2026-09-01", "total_return": 1.0, "annualized_return": 0.03, "sharpe_ratio": 0.8, "maximum_drawdown": -0.1, "total_transaction_cost": 0.01, "benchmark_total_return": 2.0},
        "walkforward_manifest": {"config": config, "config_hash": digest, "actual_to": "2026-09-01", "window_count": 19},
        "snapshot": {"strategy_id": "adaptive_defensive_etf", "config_hash": digest, "as_of_date": "2026-09-02", "data_date": "2026-09-01", "parameters_frozen": True, "oos_parameter_selection_prohibited": True, "costs_included": True, "data_fresh": True, "risk_review_passed": False, "walk_forward_windows": 19, "negative_walk_forward_windows": 3, "rolling_oos_sessions": 252, "rolling_oos_sharpe": 0.5, "rolling_oos_max_drawdown": -0.1, "annualized_turnover": 8, "shadow_sessions": 2, "shadow_observation_calendar_days": 3},
        "observations": [{"plan_id": "old", "data_date": "2026-08-28"}, {"plan_id": "one", "data_date": "2026-08-31"}, {"plan_id": "latest", "data_date": "2026-09-01"}],
    }


def test_bundle_deduplicates_and_excludes_pre_policy_records():
    bundle = valid_bundle()
    bundle["observations"].append(bundle["observations"][-1])
    validate_bundle(bundle)


@pytest.mark.parametrize("mutation", [
    lambda b: b["summary"]["config"].update(top_k_assets=None),
    lambda b: b["summary"].update(end="2026-08-31"),
    lambda b: b["snapshot"].update(shadow_sessions=3),
    lambda b: b["summary"].update(sharpe_ratio=float("nan")),
    lambda b: b.update(captured_at_utc="2026-09-01T12:00:00"),
    lambda b: b.update(captured_at_utc=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat()),
    lambda b: b["observations"].append({"plan_id": "future", "data_date": "2027-01-01"}),
])
def test_invalid_evidence_is_rejected(mutation):
    bundle = deepcopy(valid_bundle())
    mutation(bundle)
    with pytest.raises(ValueError):
        validate_bundle(bundle)


def test_windows_wrapper_uses_adaptive_and_publishes_without_broker():
    source = (Path(__file__).resolve().parents[1] / "scripts/run_defensive_etf_observation.cmd").read_text(encoding="utf-8")
    assert "run_defensive_etf_backtest.py --profile adaptive" in source
    assert "publish_defensive_observation.py" in source
    assert "ibkr_execution_agent" not in source
