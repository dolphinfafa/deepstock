"""Bounded, aggregate-only result channel for scheduled child commands."""
from __future__ import annotations

import json
import math
import os

from deepstock.scheduled_mail import STAT_LABELS

MARKER = 'DEEPSTOCK_RESULT_V1 '
MAX_BYTES = 16 * 1024


def aggregate_payload(payload):
    def numeric(value):
        return value is None or type(value) in (bool, int) or (type(value) is float and math.isfinite(value))

    result = {key: value for key, value in payload.items() if key in STAT_LABELS and numeric(value)}
    baseline = payload.get('strategies', {}).get('model_baseline', {})
    result.update({key: value for key, value in baseline.items() if key in STAT_LABELS and numeric(value)})
    for key in ('status', 'as_of_date', 'signal_date', 'trade_date', 'data_date'):
        if payload.get(key) is not None:
            result[key] = str(payload[key])[:64]
    if isinstance(payload.get('dates'), list):
        rows = [row for row in payload['dates'] if isinstance(row, dict)]
        for key, field in (('rows_received', 'received'), ('rows_inserted', 'inserted')):
            result[key] = sum(row.get(field, 0) for row in rows if type(row.get(field, 0)) is int)
    return result


def print_result(payload):
    if os.getenv('DEEPSTOCK_SCHEDULED_SUMMARY') != '1':
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return
    line = MARKER + json.dumps({'version': 1, 'payload': aggregate_payload(payload)}, ensure_ascii=False, separators=(',', ':'))
    if len(line.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Scheduled summary exceeds result limit')
    print(line, flush=True)


def marked_payload(text):
    """None means legacy output; an invalid marked result fails closed."""
    for line in reversed(text.splitlines()):
        if line.startswith(MARKER):
            if len(line.encode('utf-8')) > MAX_BYTES:
                return {}
            try:
                value = json.loads(line[len(MARKER):])
                if value.get('version') == 1 and isinstance(value.get('payload'), dict):
                    return aggregate_payload(value['payload'])
            except (ValueError, AttributeError, TypeError):
                pass
            return {}
    return None
