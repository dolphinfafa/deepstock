#!/usr/bin/env python3
"""Resume Deepstock's frozen minute study without exceeding its provider quota."""
from __future__ import annotations

import argparse
import json
import subprocess
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.sync_csi300_auction_data import _try_lock, synchronize
from scripts.clean_existing_data import run as clean_inputs


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT
SOURCE_PYTHON = Path(__import__("sys").executable)
REPORT_NAME = "execution_backtest_20260929_frozen"
DAILY_LIMIT = 2
MINIMUM_SPACING = timedelta(seconds=3700)


def reserve_request(ledger: dict, now: datetime) -> bool:
    """Reserve before invoking the provider; failed requests also consume budget."""
    attempts = ledger.setdefault("attempts", [])
    today = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
    if today.isoformat() in ledger.get("provider_exhausted_dates", []):
        return False
    timestamps = [datetime.fromisoformat(row["reserved_at_utc"]) for row in attempts]
    if sum(value.astimezone(ZoneInfo("Asia/Shanghai")).date() == today for value in timestamps) >= DAILY_LIMIT:
        return False
    if timestamps and now - max(timestamps) < MINIMUM_SPACING:
        return False
    attempts.append({"reserved_at_utc": now.astimezone(timezone.utc).isoformat()})
    return True


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_backfill(source: Path, python: Path, destination: Path) -> dict:
    source = source.resolve()
    if source == PROJECT_ROOT:
        clean_inputs(PROJECT_ROOT)
    root = source / "artifacts/auction_history_tushare"
    state = destination / "csi300_sync"
    state.mkdir(parents=True, exist_ok=True)
    ledger_path = state / "minute_quota_ledger.json"
    with (state / "minute_backfill.lock").open("a+", encoding="utf-8") as lock:
        if not _try_lock(lock):
            return {"status": "skipped_locked"}
        now = datetime.now(timezone.utc)
        ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {}
        base = [str(python), "-m", "scripts.backtest_auction_features"]
        fetch_report_path = root / "minute_fetch_report.json"
        previous = json.loads(fetch_report_path.read_text(encoding="utf-8")) if fetch_report_path.exists() else {}
        frozen = root / f"{REPORT_NAME}.json"
        result = {"checked_at_utc": now.isoformat(), "daily_limit": DAILY_LIMIT, "minimum_spacing_seconds": 3700}
        if frozen.exists():
            result["status"] = "complete"
        elif previous.get("remaining_windows") == 0:
            result["status"] = "ready_to_finalize"
        elif not reserve_request(ledger, now):
            result["status"] = "waiting_for_quota"
        else:
            write_json(ledger_path, ledger)
            command = base + [
                "fetch-minute-entries", "--start", "2026-08-31", "--end", "2026-09-29",
                "--minute-source", "tushare", "--date-concurrency", "1",
                "--minimum-date-coverage", "0.90", "--maximum-windows-per-run", "1",
                "--retries", "0", "--request-spacing", "0",
            ]
            # The original adapter makes one stk_mins call for this one window.
            # Never retry or invoke a second window in the same execution.
            reservation = ledger['attempts'][-1]['reserved_at_utc']
            try:
                process = subprocess.run(command, cwd=source, check=False, timeout=180,
                                         env={**os.environ, "DEEPSTOCK_MINUTE_RESERVATION": reservation})
                returncode = process.returncode
            except (subprocess.TimeoutExpired, OSError):
                returncode = 1
            # Preserve the provider's consumed marker; never overwrite it with the pre-call ledger.
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            attempt = next(row for row in ledger['attempts'] if row['reserved_at_utc'] == reservation)
            attempt['returncode'] = returncode
            fetched = json.loads(fetch_report_path.read_text(encoding='utf-8')) if fetch_report_path.exists() else {}
            attempted = fetched.get('attempted_dates', []) if fetched.get('reservation') == reservation else []
            manifest_path = root / 'minute_manifest.json'
            minute_manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
            if not attempted:
                attempted = [datetime.strptime(key.split('_')[0], '%Y%m%d').date().isoformat()
                             for key, value in minute_manifest.items() if value.get('reservation') == reservation]
            result['attempted_dates'] = attempt['attempted_dates'] = attempted
            if attempted:
                key = attempted[0].replace("-", "")
                outcome = minute_manifest.get(f"{key}_{key}", {})
                attempt['window_status'] = outcome.get('status', 'unknown')
                if "频率超限" in outcome.get("error", ""):
                    ledger.setdefault("provider_exhausted_dates", []).append(now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat())
                    result["status"] = "waiting_for_provider_quota"
                elif outcome.get("status") == "error":
                    result["status"] = "fetch_failed"
                elif outcome.get('status') != 'ok':
                    result['status'] = 'input_blocked'
                    result['no_progress'] = True
                elif fetched.get('remaining_windows', 21) >= previous.get('remaining_windows', 21):
                    result['status'] = 'input_blocked'
                    result['no_progress'] = True
            else:
                result['status'] = 'fetch_failed' if returncode else 'input_blocked'
            write_json(ledger_path, ledger)
            if returncode:
                result['status'] = 'fetch_failed'
            result.setdefault("status", "backfilling")
        current = json.loads(fetch_report_path.read_text(encoding='utf-8')) if fetch_report_path.exists() else {}
        if not frozen.exists() and current.get('remaining_windows') == 0 and result['status'] not in {'fetch_failed', 'waiting_for_provider_quota'}:
            # The finalizer independently verifies all 21 windows and fixed coverage.
            finalize = subprocess.run(base + [
                "finalize-minute-backfill", "--start", "2026-08-31", "--end", "2026-09-29",
                "--report-name", REPORT_NAME,
            ], cwd=source, check=False, timeout=1800)
            if finalize.returncode:
                result["status"] = "finalize_failed"
            elif frozen.exists():
                result["status"] = "complete"
        if fetch_report_path.exists():
            fetched = json.loads(fetch_report_path.read_text(encoding="utf-8"))
            result.update({key: fetched.get(key) for key in ("remaining_windows", "remaining_dates", "status_counts")})
            result["completed_windows"] = 21 - int(fetched.get("remaining_windows", 21))
        result["frozen_report_ready"] = frozen.exists()
        result["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(state / "minute_backfill_status.json", result)
        if (source / "artifacts").resolve() != destination.resolve():
            synchronize(source / "artifacts", destination)
        else:
            clean_inputs(PROJECT_ROOT)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--source-python", type=Path, default=SOURCE_PYTHON)
    parser.add_argument("--destination-root", type=Path, default=PROJECT_ROOT / "artifacts")
    args = parser.parse_args()
    result = run_backfill(args.source_root, args.source_python, args.destination_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] in {"fetch_failed", "finalize_failed"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
