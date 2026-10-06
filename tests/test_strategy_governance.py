from __future__ import annotations

import json
import hashlib
from dataclasses import asdict
from datetime import datetime, timezone

import pytest
import pandas as pd

from deepstock.strategy_governance import (
    PAPER_REVIEW_STATUS,
    evaluate_snapshot,
    load_registry,
    record_governance_decision,
)
from scripts.evaluate_strategy_registry import main as evaluate_registry_main
from scripts.build_defensive_governance_snapshot import build_snapshot
from deepstock.defensive import config_hash, frozen_defensive_config


def attach_fixed_evidence(paths):
    config = asdict(frozen_defensive_config())
    for name in ("manifest.json", "plan.json"):
        report = json.loads(paths[name].read_text())
        report.update(config=config, config_hash=config_hash(config))
        if name == "manifest.json":
            report["actual_to"] = json.loads(paths["plan.json"].read_text())["data_date"]
        paths[name].write_text(json.dumps(report), encoding="utf-8")
    (paths["daily.csv"].parent / "summary.json").write_text(json.dumps({
        "config": config, "config_hash": config_hash(config),
        "end": json.loads(paths["plan.json"].read_text())["data_date"],
        "daily_sha256": hashlib.sha256(paths["daily.csv"].read_bytes()).hexdigest(),
    }), encoding="utf-8")


def registry_file(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "strategies": [
                    {"strategy_id": "defensive", "execution_status": "shadow_observation_no_orders"},
                    {"strategy_id": "research", "execution_status": "research_only_no_orders"},
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def passing_snapshot(strategy_id="defensive"):
    return {
        "strategy_id": strategy_id,
        "as_of_date": "2026-10-11",
        "data_date": "2026-10-10",
        "parameters_frozen": True,
        "oos_parameter_selection_prohibited": True,
        "costs_included": True,
        "data_fresh": True,
        "risk_review_passed": True,
        "walk_forward_windows": 6,
        "negative_walk_forward_windows": 1,
        "rolling_oos_sessions": 252,
        "rolling_oos_sharpe": 0.5,
        "rolling_oos_max_drawdown": -0.20,
        "annualized_turnover": 15.0,
        "shadow_sessions": 30,
        "shadow_observation_calendar_days": 42,
    }


def test_passing_shadow_strategy_requires_human_paper_review(tmp_path) -> None:
    decision = evaluate_snapshot(passing_snapshot(), load_registry(registry_file(tmp_path)))

    assert decision["recommendation"] == PAPER_REVIEW_STATUS
    assert decision["paper_authorized"] is False
    assert decision["live_authorized"] is False


def test_short_recent_result_cannot_promote_a_strategy(tmp_path) -> None:
    snapshot = passing_snapshot()
    snapshot["rolling_oos_sessions"] = 20
    snapshot["shadow_sessions"] = 20
    snapshot["shadow_observation_calendar_days"] = 20
    snapshot["rolling_oos_sharpe"] = 2.0

    decision = evaluate_snapshot(snapshot, load_registry(registry_file(tmp_path)))

    assert decision["recommendation"] == "continue_shadow_observation"
    assert "Failed fixed gate: rolling_oos_sessions." in decision["reasons"]
    assert "Failed fixed gate: shadow_sessions." in decision["reasons"]


def test_research_only_strategy_cannot_be_promoted(tmp_path) -> None:
    decision = evaluate_snapshot(passing_snapshot("research"), load_registry(registry_file(tmp_path)))

    assert decision["recommendation"] == "continue_shadow_observation"
    assert "Strategy is not approved for shadow observation under the registry." in decision["reasons"]


def test_record_rejects_duplicate_strategy_date_and_execution_authorization(tmp_path) -> None:
    decision = evaluate_snapshot(passing_snapshot(), load_registry(registry_file(tmp_path)))
    records = tmp_path / "decisions.jsonl"
    record_governance_decision(decision, records, recorded_at=datetime(2026, 10, 11, tzinfo=timezone.utc))

    with pytest.raises(ValueError, match="already exists"):
        record_governance_decision(decision, records)
    decision["paper_authorized"] = True
    with pytest.raises(ValueError, match="cannot authorize"):
        record_governance_decision(decision, tmp_path / "blocked.jsonl")


def test_registry_script_skips_duplicate_without_writing(tmp_path, monkeypatch) -> None:
    registry = registry_file(tmp_path)
    snapshots = tmp_path / "snapshots.json"
    records = tmp_path / "decisions.jsonl"
    snapshots.write_text(json.dumps([passing_snapshot()]), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluate_strategy_registry.py",
            "--snapshots",
            str(snapshots),
            "--registry",
            str(registry),
            "--records",
            str(records),
        ],
    )
    assert evaluate_registry_main() == 0
    monkeypatch.setattr("sys.argv", [*__import__("sys").argv, "--skip-duplicate"])
    assert evaluate_registry_main() == 0
    assert len(records.read_text(encoding="utf-8").splitlines()) == 1


def test_registry_script_accepts_one_snapshot_object(tmp_path, monkeypatch) -> None:
    registry = registry_file(tmp_path)
    snapshots = tmp_path / "snapshot.json"
    records = tmp_path / "decisions.jsonl"
    snapshots.write_text(json.dumps(passing_snapshot()), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluate_strategy_registry.py",
            "--snapshots",
            str(snapshots),
            "--registry",
            str(registry),
            "--records",
            str(records),
        ],
    )

    assert evaluate_registry_main() == 0
    assert len(records.read_text(encoding="utf-8").splitlines()) == 1


def test_defensive_snapshot_uses_fixed_reports_and_defaults_risk_review_to_false(tmp_path) -> None:
    dates = pd.date_range("2025-08-01", periods=252, freq="B")
    prices = pd.DataFrame(
        {
            "date": list(dates) * 2,
            "symbol": ["SPY"] * len(dates) + ["SHY"] * len(dates),
            "adjusted_close": [100 + index for index in range(len(dates))] * 2,
        }
    )
    daily = pd.DataFrame(
        {"date": dates, "portfolio_net_return": [0.001] * len(dates), "turnover": [0.01] * len(dates), "transaction_cost": [0.00001] * len(dates)}
    )
    walkforward = pd.DataFrame({"total_return": [0.1, -0.01, 0.02]})
    manifest = {"selection_policy": "Configuration was fixed; no rolling test result selected parameters."}
    plan = {"strategy": "adaptive_defensive_etf", "data_date": dates[-1].date().isoformat()}
    observations = {"plan_id": "one"}
    paths = {name: tmp_path / name for name in ("prices.csv", "daily.csv", "walkforward.csv", "manifest.json", "plan.json", "observations.jsonl")}
    prices.to_csv(paths["prices.csv"], index=False)
    from deepstock.data import DataStore
    DataStore(tmp_path).import_file(paths["prices.csv"], {"market": "CN", "provider": "synthetic test fixture"})
    daily.to_csv(paths["daily.csv"], index=False)
    walkforward.to_csv(paths["walkforward.csv"], index=False)
    paths["manifest.json"].write_text(json.dumps(manifest), encoding="utf-8")
    paths["plan.json"].write_text(json.dumps(plan), encoding="utf-8")
    paths["observations.jsonl"].write_text(json.dumps(observations) + "\n", encoding="utf-8")
    attach_fixed_evidence(paths)

    snapshot = build_snapshot(
        paths["prices.csv"], paths["daily.csv"], paths["walkforward.csv"], paths["manifest.json"], paths["plan.json"], paths["observations.jsonl"], as_of_date="2026-09-01"
    )

    assert snapshot["data_fresh"] is True
    assert snapshot["parameters_frozen"] is True
    assert snapshot["oos_parameter_selection_prohibited"] is True
    assert snapshot["risk_review_passed"] is False
    assert snapshot["negative_walk_forward_windows"] == 1
    assert snapshot["shadow_sessions"] == 0
    assert snapshot["shadow_observation_calendar_days"] == 0


def test_governance_rejects_a_decision_before_the_policy_effective_date(tmp_path) -> None:
    snapshot = passing_snapshot()
    snapshot["as_of_date"] = "2026-08-30"
    snapshot["data_date"] = "2026-08-29"

    with pytest.raises(ValueError, match="precedes"):
        evaluate_snapshot(snapshot, load_registry(registry_file(tmp_path)))


def test_snapshot_counts_only_observations_under_the_current_policy(tmp_path) -> None:
    # The setup matches the fixed-report schema; only post-policy records count.
    dates = pd.date_range("2025-09-15", periods=252, freq="B")
    prices = pd.DataFrame(
        {"date": list(dates) * 2, "symbol": ["SPY"] * len(dates) + ["SHY"] * len(dates), "adjusted_close": [100 + index for index in range(len(dates))] * 2}
    )
    daily = pd.DataFrame({"date": dates, "portfolio_net_return": [0.001] * len(dates), "turnover": [0.01] * len(dates), "transaction_cost": [0.00001] * len(dates)})
    paths = {name: tmp_path / name for name in ("prices.csv", "daily.csv", "walkforward.csv", "manifest.json", "plan.json", "observations.jsonl")}
    prices.to_csv(paths["prices.csv"], index=False)
    from deepstock.data import DataStore
    DataStore(tmp_path).import_file(paths["prices.csv"], {"market": "CN", "provider": "synthetic test fixture"})
    daily.to_csv(paths["daily.csv"], index=False)
    pd.DataFrame({"total_return": [0.1]}).to_csv(paths["walkforward.csv"], index=False)
    paths["manifest.json"].write_text(json.dumps({"selection_policy": "fixed; no rolling test result selected parameters"}), encoding="utf-8")
    paths["plan.json"].write_text(json.dumps({"strategy": "adaptive_defensive_etf", "data_date": dates[-1].date().isoformat()}), encoding="utf-8")
    paths["observations.jsonl"].write_text(
        "\n".join(json.dumps(entry) for entry in ({"plan_id": "old", "data_date": "2026-08-28"}, {"plan_id": "new", "data_date": "2026-08-31"})) + "\n",
        encoding="utf-8",
    )
    attach_fixed_evidence(paths)

    snapshot = build_snapshot(paths["prices.csv"], paths["daily.csv"], paths["walkforward.csv"], paths["manifest.json"], paths["plan.json"], paths["observations.jsonl"], as_of_date="2026-09-01")

    assert snapshot["shadow_sessions"] == 1
    assert snapshot["shadow_observation_calendar_days"] == 2

    plan = json.loads(paths["plan.json"].read_text())
    plan["config"]["top_k_assets"] = None
    paths["plan.json"].write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="frozen"):
        build_snapshot(*[paths[name] for name in ("prices.csv", "daily.csv", "walkforward.csv", "manifest.json", "plan.json", "observations.jsonl")])
    plan["config"]["top_k_assets"] = 2
    paths["plan.json"].write_text(json.dumps(plan), encoding="utf-8")
    paths["daily.csv"].write_text(paths["daily.csv"].read_text() + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        build_snapshot(*[paths[name] for name in ("prices.csv", "daily.csv", "walkforward.csv", "manifest.json", "plan.json", "observations.jsonl")])
