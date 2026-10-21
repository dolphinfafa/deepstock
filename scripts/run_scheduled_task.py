"""Run an existing scheduled command once and retain only its aggregate result."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from deepstock.scheduled_mail import TASKS, STAT_LABELS, TaskResult, enqueue, publish_pending
from deepstock.web.config import settings
from deepstock.scheduled_summary import marked_payload

MODULES = {
    'app-ingest': ('scripts.ingest_deepstock_state', []),
    'database-backup': ('scripts.backup_deepstock_database', []),
    'auction-minute-backfill': ('scripts.run_csi300_minute_backfill', []),
    **{f'auction-{key}': ('scripts.short_term_forward', [key])
       for key in ('collect', 'signal', 'fill', 'settle', 'report')},
    'auction-settle-counterfactual': ('scripts.short_term_forward', ['settle-counterfactual']),
    'adx-shadow': ('scripts.run_adx_shadow', []),
    'granville-shadow': ('scripts.run_granville_shadow', []),
}


def command(task_id, extra, root):
    if task_id == 'defensive-observation':
        if extra or os.name != 'nt':
            raise ValueError('Defensive scheduler runs only on its Windows node')
        return ['cmd.exe', '/d', '/c', str(root / 'scripts/run_defensive_etf_observation_core.cmd')]
    module, args = MODULES[task_id]
    # Installer supplies the original arguments; no shell invocation/interpolation.
    return [sys.executable, '-m', module, *args, *extra]


def last_object(text):
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != '{':
            continue
        try:
            value, end = decoder.raw_decode(text[index:])
        except ValueError:
            continue
        if isinstance(value, dict) and not text[index + end:].strip():
            return value
    return {}


def receipt(task_id, started, finished, returncode, payload, *, node='server'):
    reason = payload.get('status', 'completed')
    status = 'completed'
    if returncode:
        status, reason = 'failed', 'process_failed'
    elif reason in ('fetch_failed', 'finalize_failed'):
        status = 'failed'
    elif reason in ('failed', 'error'):
        status, reason = 'failed', 'process_failed'
    elif reason in ('waiting_for_quota', 'waiting_for_provider_quota', 'missed_decision_cutoff', 'input_blocked', 'settlement_blocked'):
        status = 'blocked'
    elif reason in ('skipped_locked', 'skipped_non_trading_day', 'already_recorded', 'nothing_pending'):
        status = 'skipped'
    elif not payload:
        status, reason = 'blocked', 'result_unavailable'
    else:
        reason = 'completed'
    stats = {k: v for k, v in payload.items() if k in STAT_LABELS and (v is None or type(v) in (int, float, bool))}
    if task_id == 'auction-report':
        baseline = payload.get('strategies', {}).get('model_baseline', {})
        stats.update({k: v for k, v in baseline.items() if k in STAT_LABELS})
    if task_id == 'database-backup' and 'integrity_check' in payload:
        stats['integrity_ok'] = payload.get('integrity_check') == 'ok'
    if task_id == 'auction-collect':
        if isinstance(payload.get('dates'), list):
            stats.update({key: sum(v.get(field, 0) for v in payload['dates'] if isinstance(v, dict)
                                  and type(v.get(field, 0)) is int)
                          for key, field in (('rows_received', 'received'), ('rows_inserted', 'inserted'))})
    data_date = next((payload.get(k) for k in ('as_of_date', 'signal_date', 'trade_date', 'data_date') if payload.get(k)), None)
    return TaskResult(event_id=uuid.uuid4().hex, task_id=task_id, node=node, started_at=started,
                      finished_at=finished, exit_code=returncode, status=status, reason=reason,
                      data_date=data_date, stats=stats)


def run(task_id, extra, root):
    args = command(task_id, extra, root)
    started = datetime.now(timezone.utc)
    tail = bytearray()
    try:
        with subprocess.Popen(args, cwd=root, stdout=subprocess.PIPE,
                              env={**os.environ, 'DEEPSTOCK_SCHEDULED_SUMMARY': '1'}) as process:
            while block := process.stdout.read1(8192):
                sys.stdout.buffer.write(block)
                sys.stdout.buffer.flush()
                tail.extend(block)
                del tail[:-128 * 1024]
            returncode = process.wait()
    except OSError:
        returncode = 1
    finished = datetime.now(timezone.utc)
    output = tail.decode('utf-8', errors='replace')
    payload = marked_payload(output)
    if payload is None:
        payload = last_object(output)
    node = 'quant' if task_id in ('defensive-observation','adx-shadow','granville-shadow') else 'server'
    try:
        if task_id == 'defensive-observation' and returncode == 0:
            # The core command has completed all publication steps successfully.
            payload = json.loads((root / 'artifacts/paper/defensive-etf/latest.json').read_text(encoding='utf-8'))
        event = receipt(task_id, started, finished, returncode, payload, node=node)
    except Exception:
        # A malformed/missing summary still produces a truthful task-outcome receipt.
        event = receipt(task_id, started, finished, returncode, {}, node=node)
    try:
        enqueue(root, event)
        if event.node == 'quant':
            publish_pending(root, settings)
    except Exception as error:
        # Mail/reporting faults must not change the original strategy command's exit.
        # A persisted node receipt survives for later publication; never rerun its task.
        print(json.dumps({'scheduled_receipt': 'deferred', 'error_type': type(error).__name__}))
    return returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task_id', choices=TASKS)
    args, extra = parser.parse_known_args()
    return run(args.task_id, extra, settings.project_root)


if __name__ == '__main__':
    raise SystemExit(main())
