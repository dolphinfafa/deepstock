"""Append-only records for order-free paper-strategy observation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def record_observation(
    plan: dict[str, Any],
    records_path: Path,
    *,
    recorded_at: datetime | None = None,
) -> dict[str, Any]:
    """Append one plan observation and reject duplicate plan IDs."""

    if plan.get("mode") != "paper":
        raise ValueError("Only paper plans can be observed.")
    plan_id = plan.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id:
        raise ValueError("Plan must contain a nonempty plan_id.")

    records_path.parent.mkdir(parents=True, exist_ok=True)
    if records_path.exists():
        for line in records_path.read_text(encoding="utf-8").splitlines():
            if line and json.loads(line).get("plan_id") == plan_id:
                raise ValueError(f"Observation already exists for plan_id={plan_id}.")

    timestamp = (recorded_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    record = {
        "recorded_at_utc": timestamp.isoformat(),
        "plan_id": plan_id,
        "strategy": plan.get("strategy"),
        "data_date": plan.get("data_date"),
        "status": plan.get("status"),
        "kill_switch": bool(plan.get("kill_switch")),
        "target_weights": plan.get("target_weights"),
    }
    with records_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")
    return record
