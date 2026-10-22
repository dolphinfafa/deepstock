"""Check the real S4U context without creating plans, captures or orders."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import sqlite3

from deepstock.data.store import write_json
from deepstock.web.config import settings


class ProbeFailure(ValueError):
    """Only developer-authored diagnostic reasons may be included in results."""


def probe(expected_sid, output):
    from scripts.install_observation_tasks import current_identity
    result = {'started_at_utc': datetime.now(timezone.utc).isoformat(), 'checks': {}}
    checks = result['checks']
    stage = 'identity'
    try:
        if os.name != 'nt' or current_identity()[1] != expected_sid:
            raise ProbeFailure('wrong_windows_user_context')
        checks[stage] = 'passed'
        stage = 'local_files'
        # Readability is verified without recording secrets or licensed contents.
        if not (settings.project_root / '.env').read_bytes() or not settings.node_token:
            raise ProbeFailure('local_node_configuration_unavailable')
        for relative in ('artifacts/paper/defensive-etf/latest.json',
                         'artifacts/research/adx-shadow-v1/latest.json',
                         'artifacts/research/granville-shadow-v1/latest.json'):
            json.loads((settings.project_root / relative).read_text(encoding='utf-8'))
        for relative in ('adx-shadow-v1', 'granville-shadow-v1'):
            path = settings.project_root / 'artifacts/research' / relative / 'state.sqlite3'
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
                if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ProbeFailure('local_ledger_integrity_check_failed')
        output.parent.mkdir(parents=True, exist_ok=True)
        test_file = output.with_suffix('.write-test')
        test_file.write_text('probe', encoding='utf-8')
        test_file.unlink()
        checks[stage] = 'passed'
        stage = 'norgate'
        import norgatedata as n
        import pandas as pd
        from deepstock.data.membership_contract import load_effective_membership_contract
        from scripts.run_adx_shadow import completed_session
        contract, _ = load_effective_membership_contract()
        if importlib.metadata.version('norgatedata') != contract['package_version']:
            raise ProbeFailure('reviewed_norgate_sdk_version_required')
        day = str(completed_session(datetime.now(timezone.utc)).date())
        symbols = n.watchlist_symbols('S&P 500 Current & Past')
        if not symbols or len(symbols) != len(set(symbols)):
            raise ProbeFailure('norgate_historical_watchlist_unavailable')
        for symbol in ('SPY', 'QQQ', 'IWM', 'TLT', 'IEF', 'GLD', 'SHY'):
            frame = pd.DataFrame(n.price_timeseries(symbol,
                stock_price_adjustment_setting=n.StockPriceAdjustmentType.TOTALRETURN,
                padding_setting=n.PaddingType.NONE, start_date=day, end_date=day))
            if frame.empty or 'Date' not in frame or str(pd.Timestamp(frame.Date.iloc[-1]).date()) != day:
                raise ProbeFailure('norgate_latest_completed_session_quotes_unavailable')
        checks[stage] = 'passed'
        result['data_date'] = day
        result['historical_symbols'] = len(symbols)
        stage = 'network'
        import httpx
        response = httpx.get(settings.public_base_url.rstrip('/') + '/api/health', timeout=20)
        response.raise_for_status()
        if response.json().get('status') != 'ok':
            raise ProbeFailure('server_health_check_failed')
        checks[stage] = 'passed'
        result['status'] = 'passed'
    except Exception as error:
        # Errors can contain user paths, license details, URLs or credentials.
        checks[stage] = 'failed'
        result.update(status='failed', failed_check=stage, error_type=type(error).__name__)
        if isinstance(error, ProbeFailure):
            result['reason'] = str(error)
    result['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    write_json(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-sid', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = probe(args.expected_sid, args.output)
    print(json.dumps(result))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
