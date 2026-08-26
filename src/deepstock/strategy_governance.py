"""Frozen-rule governance for order-free strategy shadow observation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


POLICY_ID = "shadow-governance-v1-2026-08-26"
POLICY_EFFECTIVE_DATE = date(2026, 8, 26)
PAPER_REVIEW_STATUS = "eligible_for_paper_review"


@dataclass(frozen=True)
class GovernancePolicy:
    """Predeclared gates; changing one requires a new policy identifier."""

    minimum_walk_forward_windows: int = 6
    maximum_negative_walk_forward_windows: int = 1
    minimum_rolling_oos_sessions: int = 252
    minimum_rolling_oos_sharpe: float = 0.5
    maximum_rolling_oos_drawdown: float = -0.20
    maximum_annualized_turnover: float = 15.0
    minimum_shadow_sessions: int = 40


REQUIRED_SNAPSHOT_FIELDS = frozenset(
    {
        "strategy_id",
        "as_of_date",
        "data_date",
        "parameters_frozen",
        "oos_parameter_selection_prohibited",
        "costs_included",
        "data_fresh",
        "risk_review_passed",
        "walk_forward_windows",
        "negative_walk_forward_windows",
        "rolling_oos_sessions",
        "rolling_oos_sharpe",
        "rolling_oos_max_drawdown",
        "annualized_turnover",
        "shadow_sessions",
    }
)


def load_registry(path: Path) -> dict[str, dict[str, Any]]:
    """Load the version-controlled registry without accepting execution flags."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    strategies = raw.get("strategies")
    if not isinstance(strategies, list):
        raise ValueError("Registry must contain a strategies list.")
    registry: dict[str, dict[str, Any]] = {}
    for strategy in strategies:
        strategy_id = strategy.get("strategy_id") if isinstance(strategy, dict) else None
        if not isinstance(strategy_id, str) or not strategy_id:
            raise ValueError("Every registry strategy needs a nonempty strategy_id.")
        if strategy_id in registry:
            raise ValueError(f"Duplicate registry strategy_id={strategy_id}.")
        registry[strategy_id] = strategy
    return registry


def _validate_snapshot(snapshot: dict[str, Any], registry: dict[str, dict[str, Any]]) -> None:
    missing = REQUIRED_SNAPSHOT_FIELDS.difference(snapshot)
    if missing:
        raise ValueError(f"Snapshot missing fields: {sorted(missing)}")
    strategy_id = snapshot["strategy_id"]
    if strategy_id not in registry:
        raise ValueError(f"Snapshot strategy is not registered: {strategy_id}.")
    try:
        as_of = date.fromisoformat(str(snapshot["as_of_date"]))
        data_date = date.fromisoformat(str(snapshot["data_date"]))
    except ValueError as error:
        raise ValueError("Snapshot dates must use ISO-8601 calendar dates.") from error
    if data_date > as_of:
        raise ValueError("Snapshot data_date cannot be after as_of_date.")
    if as_of < POLICY_EFFECTIVE_DATE:
        raise ValueError(f"Snapshot as_of_date precedes {POLICY_ID}.")
    if snapshot["walk_forward_windows"] < 0 or snapshot["negative_walk_forward_windows"] < 0:
        raise ValueError("Walk-Forward counts cannot be negative.")
    if snapshot["negative_walk_forward_windows"] > snapshot["walk_forward_windows"]:
        raise ValueError("Negative Walk-Forward windows cannot exceed all windows.")
    if snapshot["rolling_oos_sessions"] < 0 or snapshot["shadow_sessions"] < 0:
        raise ValueError("Observation session counts cannot be negative.")
    if snapshot["annualized_turnover"] < 0:
        raise ValueError("Annualized turnover cannot be negative.")
    if snapshot["rolling_oos_max_drawdown"] > 0:
        raise ValueError("Rolling OOS drawdown cannot be positive.")


def evaluate_snapshot(
    snapshot: dict[str, Any], registry: dict[str, dict[str, Any]], policy: GovernancePolicy | None = None
) -> dict[str, Any]:
    """Apply fixed gates and return an advisory decision without execution authority."""

    policy = policy or GovernancePolicy()
    _validate_snapshot(snapshot, registry)
    strategy = registry[snapshot["strategy_id"]]
    reasons: list[str] = []
    if strategy.get("execution_status") != "shadow_observation_no_orders":
        reasons.append("Strategy is not approved for shadow observation under the registry.")
    checks = {
        "parameters_frozen": snapshot["parameters_frozen"],
        "oos_parameter_selection_prohibited": snapshot["oos_parameter_selection_prohibited"],
        "costs_included": snapshot["costs_included"],
        "data_fresh": snapshot["data_fresh"],
        "risk_review_passed": snapshot["risk_review_passed"],
        "walk_forward_windows": snapshot["walk_forward_windows"] >= policy.minimum_walk_forward_windows,
        "negative_walk_forward_windows": (
            snapshot["negative_walk_forward_windows"] <= policy.maximum_negative_walk_forward_windows
        ),
        "rolling_oos_sessions": snapshot["rolling_oos_sessions"] >= policy.minimum_rolling_oos_sessions,
        "rolling_oos_sharpe": snapshot["rolling_oos_sharpe"] >= policy.minimum_rolling_oos_sharpe,
        "rolling_oos_max_drawdown": (
            snapshot["rolling_oos_max_drawdown"] >= policy.maximum_rolling_oos_drawdown
        ),
        "annualized_turnover": snapshot["annualized_turnover"] <= policy.maximum_annualized_turnover,
        "shadow_sessions": snapshot["shadow_sessions"] >= policy.minimum_shadow_sessions,
    }
    for check, passed in checks.items():
        if not passed:
            reasons.append(f"Failed fixed gate: {check}.")

    recommendation = PAPER_REVIEW_STATUS if not reasons else "continue_shadow_observation"
    canonical_snapshot = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    snapshot_id = hashlib.sha256(canonical_snapshot.encode("utf-8")).hexdigest()
    return {
        "decision_id": hashlib.sha256(
            f"{POLICY_ID}|{snapshot['strategy_id']}|{snapshot['as_of_date']}|{snapshot_id}".encode("utf-8")
        ).hexdigest(),
        "policy_id": POLICY_ID,
        "strategy_id": snapshot["strategy_id"],
        "as_of_date": snapshot["as_of_date"],
        "data_date": snapshot["data_date"],
        "snapshot_id": snapshot_id,
        "recommendation": recommendation,
        "checks": checks,
        "reasons": reasons,
        "paper_authorized": False,
        "live_authorized": False,
    }


def record_governance_decision(
    decision: dict[str, Any], records_path: Path, *, recorded_at: datetime | None = None
) -> dict[str, Any]:
    """Append one decision and prevent re-evaluating a historical strategy date."""

    required = {"decision_id", "strategy_id", "as_of_date", "paper_authorized", "live_authorized"}
    missing = required.difference(decision)
    if missing:
        raise ValueError(f"Decision missing fields: {sorted(missing)}")
    if decision["paper_authorized"] or decision["live_authorized"]:
        raise ValueError("Governance records cannot authorize paper or live execution.")
    records_path.parent.mkdir(parents=True, exist_ok=True)
    historical_key = (decision["strategy_id"], decision["as_of_date"])
    if records_path.exists():
        for line in records_path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            existing = json.loads(line)
            if (existing.get("strategy_id"), existing.get("as_of_date")) == historical_key:
                raise ValueError(
                    "Governance decision already exists for "
                    f"strategy_id={historical_key[0]}, as_of_date={historical_key[1]}."
                )
    timestamp = (recorded_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    record = {"recorded_at_utc": timestamp.isoformat(), **decision}
    with records_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")
    return record
