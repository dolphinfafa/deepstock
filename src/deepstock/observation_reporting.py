"""Validate the small, price-free observation bundle published by the Windows node."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import math
from typing import Any
from zoneinfo import ZoneInfo

from deepstock.defensive import require_frozen_config
from deepstock.strategy_governance import POLICY_EFFECTIVE_DATE


def validate_bundle(bundle: dict[str, Any]) -> None:
    captured = datetime.fromisoformat(bundle["captured_at_utc"])
    if captured.tzinfo is None or captured.utcoffset() is None:
        raise ValueError("Capture timestamp must include a timezone")
    if captured > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("Capture timestamp is future-dated")
    plan, summary, manifest, snapshot = (bundle[key] for key in ("plan", "summary", "walkforward_manifest", "snapshot"))
    if plan.get("strategy") != "adaptive_defensive_etf":
        raise ValueError("Plan belongs to another strategy")
    expected = require_frozen_config(plan.get("config", {}))
    for report in (summary, manifest):
        require_frozen_config(report.get("config", {}))
        if report.get("config_hash") != expected:
            raise ValueError("Research configuration checksum is missing or differs")
    if snapshot.get("strategy_id") != "adaptive_defensive_etf" or snapshot.get("config_hash") != expected:
        raise ValueError("Snapshot configuration checksum differs")
    if len(str(summary.get("daily_sha256", ""))) != 64:
        raise ValueError("Daily evidence checksum is missing")
    if manifest.get("window_count") != snapshot.get("walk_forward_windows"):
        raise ValueError("Walk-Forward window counts differ")
    for report, keys in ((summary, ("total_return", "annualized_return", "sharpe_ratio", "maximum_drawdown", "total_transaction_cost", "benchmark_total_return")),
                         (snapshot, ("rolling_oos_sharpe", "rolling_oos_max_drawdown", "annualized_turnover"))):
        if any(not isinstance(report.get(key), (int, float)) or not math.isfinite(report[key]) for key in keys):
            raise ValueError("Research metrics must be finite numbers")
    data_date = date.fromisoformat(plan["data_date"])
    as_of = date.fromisoformat(snapshot["as_of_date"])
    if data_date > as_of or as_of > datetime.now(ZoneInfo("Asia/Shanghai")).date():
        raise ValueError("Observation dates are future-dated")
    if summary.get("end") != plan["data_date"] or manifest.get("actual_to") != plan["data_date"] or snapshot.get("data_date") != plan["data_date"]:
        raise ValueError("Observation report dates differ")
    records = {}
    for row in bundle["observations"]:
        if not row.get("plan_id") or not row.get("data_date"):
            raise ValueError("Observation record is missing an identity or date")
        value = date.fromisoformat(row["data_date"])
        if value > data_date:
            raise ValueError("Observation record exceeds the evidence date")
        if row["plan_id"] in records and records[row["plan_id"]] != value:
            raise ValueError("Duplicate observation identity has conflicting dates")
        records[row["plan_id"]] = value
    effective = [value for value in records.values() if POLICY_EFFECTIVE_DATE <= value <= data_date]
    days = (as_of - min(effective)).days + 1 if effective else 0
    if snapshot.get("shadow_sessions") != len(effective) or snapshot.get("shadow_observation_calendar_days") != days:
        raise ValueError("Published observation counts do not match the records")
