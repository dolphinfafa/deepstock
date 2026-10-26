"""Check or run one fixed USD 200 / 70:30 ETF pilot on paper TWS only.

No order is sent by default. --check sends only non-executing what-if requests.
The main node .env, server authorizations and live gates are never modified.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sys
import time

from deepstock.ibkr_pilot import (
    LEGS, PILOT_ID, durable_json, number, pilot_lock, reference, run_cycle, validate_rows,
)

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "artifacts/ibkr-paper-pilot"
ENV = ROOT / ".env.paper-pilot"


def load_local_config(path: Path = ENV) -> dict[str, str]:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pin-account", action="store_true")
    group.add_argument("--check", action="store_true")
    group.add_argument("--run-paper", action="store_true")
    parser.add_argument("--trade-date", help="UTC/US-session date; required for the one-shot run")
    parser.add_argument("--watch-seconds", type=int, default=120)
    parser.add_argument("--sdk-root", type=Path, help="verified official Python SDK; process-local import only")
    args = parser.parse_args(argv)
    if os.name != "nt":
        raise ValueError("TWS pilot must run locally on the quantitative Windows node")
    if not 1 <= args.watch_seconds <= 900:
        raise ValueError("watch-seconds must be between 1 and 900")
    if args.run_paper and not args.trade_date:
        raise ValueError("one explicit trade date is required; this is not a recurring investment")
    if args.sdk_root:
        sdk = args.sdk_root.resolve()
        from scripts.prepare_ibkr_paper_sdk import verify_sdk
        verify_sdk(sdk)
        if "ibapi" in sys.modules:
            raise ValueError("SDK must be selected before importing any broker client")
        sys.path.insert(0, str(sdk))
    from scripts.ibkr_pilot_tws import PaperClient

    # The lock also protects account pin creation and immutable journal reads.
    with pilot_lock(RUNTIME / "pilot.lock") as acquired:
        if not acquired:
            print(json.dumps(dict(status="SKIPPED_LOCKED", pilot_id=PILOT_ID)))
            return 0
        values = load_local_config()
        salt = values.get("PAPER_PILOT_ACCOUNT_SALT", "")
        expected_hash = values.get("PAPER_PILOT_ACCOUNT_HASH", "")
        if args.pin_account and ENV.exists():
            raise ValueError("paper pin already exists; implicit re-pinning is forbidden")
        if not args.pin_account and (not salt or not expected_hash):
            raise ValueError("run the paper-only account pin first")
        enabled = args.run_paper and values.get("PAPER_PILOT_ENABLED") == "true"
        if args.run_paper and not enabled:
            raise ValueError("isolated PAPER_PILOT_ENABLED is not true")
        path = RUNTIME / "journal.json"
        plan = json.loads(path.read_text(encoding="utf-8"))["plan"] if path.exists() else None
        if plan:
            validate_rows(plan)
        broker = PaperClient(salt or secrets.token_urlsafe(32), expected_hash,
                             enabled=enabled, plan=plan,
                             enable_check=lambda: load_local_config().get("PAPER_PILOT_ENABLED") == "true")
        try:
            broker.start()
            if args.pin_account:
                with ENV.open("x", encoding="utf-8") as handle:
                    handle.write(f"PAPER_PILOT_ACCOUNT_SALT={broker.salt}\n"
                                 f"PAPER_PILOT_ACCOUNT_HASH={broker.account_hash}\n"
                                 "PAPER_PILOT_ENABLED=false\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                print(json.dumps(dict(status="PAPER_ACCOUNT_PINNED", order_submission_enabled=False)))
                return 0
            broker.collect(quotes=args.run_paper)
            if args.check:
                snapshot = broker.snapshot()
                checks = []
                for symbol, conid, exchange, budget in LEGS:
                    price = "1000" if symbol == "VOO" else "500"
                    row = dict(symbol=symbol, conid=conid, primary_exchange=exchange,
                        budget_usd=budget, quantity=str(number(budget) / number(price)),
                        limit_price=price, order_ref=reference(symbol))
                    try:
                        result = broker.preview(row)
                        checks.append(dict(symbol=symbol, accepted=True, **result))
                    except (ValueError, TimeoutError) as error:
                        checks.append(dict(symbol=symbol, accepted=False, reason=str(error)))
                ready = (snapshot["paper_account_verified"] and snapshot["reconciliation_complete"]
                         and snapshot["server_version"] >= 163 and all(row["accepted"] for row in checks))
                result = dict(status="PREFLIGHT_PASSED" if ready else "PREFLIGHT_BLOCKED",
                    pilot_id=PILOT_ID, paper_account_verified=snapshot["paper_account_verified"],
                    server_version=snapshot["server_version"], fractional_previews=checks,
                    usd_virtual_cash_at_least_205=all(number(snapshot[key]) >= 205
                        for key in ("cash_balance_usd", "available_usd")),
                    settled_cash_evidence_available=snapshot["settled_cash_evidence_available"],
                    execution_window_open=snapshot["in_regular_hours"],
                    orders_submitted=0, errors=broker.errors)
                durable_json(RUNTIME / "preflight.json", result)
                print(json.dumps(result, ensure_ascii=False))
                return 0 if ready else 2
            deadline = time.monotonic() + args.watch_seconds
            while True:
                result = run_cycle(broker, path, args.trade_date)
                result.update(time_utc=datetime.now(timezone.utc).isoformat(), errors=broker.errors,
                              mode="paper", notional_cap_usd=200, fees_included_in_cap=False)
                durable_json(RUNTIME / "latest.json", result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
                if result["status"] in {"FILLED", "STOPPED"} or time.monotonic() >= deadline:
                    return 0 if result["status"] == "FILLED" else 2
                time.sleep(3)
                broker.collect()
        except Exception as error:
            # SDK error strings are redacted at the callback boundary.
            result = dict(status="BLOCKED", mode="paper", pilot_id=PILOT_ID,
                reason=str(error), error_type=type(error).__name__, errors=broker.errors,
                time_utc=datetime.now(timezone.utc).isoformat())
            durable_json(RUNTIME / "latest.json", result)
            print(json.dumps(result, ensure_ascii=False))
            return 2
        finally:
            broker.close()


if __name__ == "__main__":
    raise SystemExit(main())
