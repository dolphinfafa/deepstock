"""Durable shared reservations for scheduled and manual historical minute calls."""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from deepstock.data.store import ROOT, write_json


def consume_minute_request():
    from scripts.run_csi300_minute_backfill import reserve_request
    from scripts.sync_csi300_auction_data import _try_lock
    root = ROOT / "artifacts/csi300_sync"
    root.mkdir(parents=True, exist_ok=True)
    ledger_path = root / "minute_quota_ledger.json"
    with (root / "minute_quota.lock").open("a+") as handle:
        if not _try_lock(handle):
            raise RuntimeError("Minute quota ledger is locked")
        ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {}
        reservation = os.getenv("DEEPSTOCK_MINUTE_RESERVATION")
        now = datetime.now(timezone.utc)
        row = next((r for r in ledger.get("attempts", []) if r["reserved_at_utc"] == reservation), None)
        if row is not None:
            age = (now - datetime.fromisoformat(row["reserved_at_utc"])).total_seconds()
            if row.get("provider_call_started") or age < 0 or age > 600:
                raise RuntimeError("Minute reservation expired or already consumed")
        else:
            if not reserve_request(ledger, now):
                raise RuntimeError("Minute request quota exhausted; no automatic retry")
            row = ledger["attempts"][-1]
        row["provider_call_started"] = True
        write_json(ledger_path, ledger)
