"""Windows-local Granville close plans and next-open analytical shadow fills."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path

import exchange_calendars as xcals
import numpy as np
import pandas as pd

from deepstock.data.capture import capture_response
from deepstock.data.membership import native_membership_view
from deepstock.data.membership_contract import load_effective_membership_contract
from deepstock.data.store import ROOT, DataStore, read_clean_csv, write_json
from deepstock.research_control import require_active_research
from deepstock.strategies.cn.auction.forward import _command_lock
from deepstock.strategies.us.granville_shadow import (
    POLICY, SettlementBlocked, advance, bundle, decision, fingerprint,
    initial_accounts, open_state, required_symbols, require_timing,
)
from deepstock.web.config import settings
from scripts.download_norgate_stock_ohlc_inventory import stock_frame
from scripts.rerun_clean_research import capture_code_provenance


def completed_session(now):
    cal = xcals.get_calendar('XNYS', start=POLICY['source_start'], end=str(now.date()))
    return next(s for s in cal.sessions[::-1] if cal.session_close(s) < pd.Timestamp(now))


def capture(folder, day, needed, code):
    import norgatedata as n
    contract, _ = load_effective_membership_contract()
    if importlib.metadata.version('norgatedata') != contract['package_version']:
        raise ValueError('Reviewed Norgate package required')
    symbols = sorted(n.watchlist_symbols('S&P 500 Current & Past'))
    if not symbols or len(symbols) != len(set(symbols)):
        raise ValueError('Complete unique current/past universe required')
    dates = xcals.get_calendar('XNYS', start=POLICY['source_start'], end=day).sessions.tz_localize(None)
    versions, records, failures, eligible = [], [], [], set()
    # Prospective eligibility is recorded as actually observed before next open.
    # Historical members are retained in the evidence list, not survivor-replaced.
    for i, symbol in enumerate(symbols):
        try:
            first, last = n.first_quoted_date(symbol), n.last_quoted_date(symbol)
            meta = {'symbol': symbol, 'assetid': n.assetid(symbol), 'first': str(first), 'last': str(last) if last else None}
            versions.append(capture_response('Norgate', 'granville-shadow-life', meta, 'US', restricted=True))
            if first is None:
                raise ValueError('Quote lifetime unknown')
            if pd.Timestamp(first) > pd.Timestamp(day) or (last is not None and pd.Timestamp(last) < pd.Timestamp(day)):
                records.append({**meta, 'status': 'outside_verified_current_quote_life'})
                continue
            native = pd.DataFrame(n.index_constituent_timeseries(symbol, 'S&P 500',
                padding_setting=n.PaddingType.ALLMARKETDAYS, start_date=day, end_date=day))
            versions.append(capture_response('Norgate', 'granville-shadow-member-' + symbol, native, 'US', restricted=True))
            view, _, quality = native_membership_view(native, pd.DatetimeIndex([pd.Timestamp(day)]), symbol, first, last, day)
            if quality['missing_required_sessions'] or len(view) != 1:
                raise ValueError('Current effective membership unavailable')
            member = bool(view.weight.iloc[0])
            records.append({**meta, 'member': member})
            if member:
                eligible.add(symbol)
        except Exception as error:
            failures.append({'symbol': symbol, 'reason': str(error)[:180]})
        if (i + 1) % 100 == 0:
            print(json.dumps({'membership_processed': i + 1, 'coverage_failures': len(failures)}), flush=True)
    frames = {}
    for i, symbol in enumerate(sorted(eligible | set(needed) | {'SPY'})):
        try:
            native = []
            lineage = []
            for adjustment in ('NONE', 'TOTALRETURN'):
                frame = pd.DataFrame(n.price_timeseries(symbol,
                    stock_price_adjustment_setting=getattr(n.StockPriceAdjustmentType, adjustment),
                    padding_setting=n.PaddingType.NONE, start_date=POLICY['source_start'], end_date=day))
                version = capture_response('Norgate', 'granville-shadow-' + adjustment + '-' + symbol, frame, 'US', restricted=True)
                versions.append(version); lineage.append(version); native.append(frame)
            frame = stock_frame(*native, symbol)
            observed = pd.DatetimeIndex(pd.to_datetime(frame.date))
            if len(observed.difference(dates)) or pd.Timestamp(day) not in observed:
                raise ValueError('Current quote missing or outside exchange calendar')
            if symbol == 'SPY' and len(dates.difference(observed)):
                raise ValueError('SPY comparison calendar incomplete')
            path = folder / 'prices' / (symbol + '.csv.gz')
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(path, index=False, compression={'method': 'gzip', 'mtime': 0})
            version = DataStore().import_file(path, {'market': 'US', 'provider': 'Norgate', 'restricted': True,
                'padding': 'NONE', 'kind': 'daily_ohlc', 'origin': 'derived_provider_export',
                'upstream_versions': lineage, 'timezone': 'America/New_York'})['id']
            clean = read_clean_csv(path, version=version)
            clean.date = pd.to_datetime(clean.date)
            frames[symbol] = clean.set_index('date')
            versions.append(version)
        except Exception as error:
            failures.append({'symbol': symbol, 'reason': str(error)[:180]})
        if (i + 1) % 50 == 0:
            print(json.dumps({'quotes_processed': i + 1, 'coverage_failures': len(failures)}), flush=True)
    if sorted(n.watchlist_symbols('S&P 500 Current & Past')) != symbols:
        failures.append({'symbol': None, 'reason': 'Universe changed during capture'})
    if not eligible:
        failures.append({'symbol': None, 'reason': 'No verified current constituent pool'})
    manifest = {'data_date': day, 'code': code, 'policy': POLICY, 'symbols': symbols, 'records': records,
                'membership_contract': contract, 'eligible': sorted(eligible), 'needed': sorted(needed),
                'versions': versions, 'failures': failures}
    write_json(folder / 'capture.json', manifest)
    return dates, frames, eligible, fingerprint(manifest), len(failures)


def publish(value):
    import httpx
    response = httpx.post(settings.public_base_url + '/api/agent/research/granville-shadow',
        headers={'Authorization': 'Bearer ' + settings.node_token}, json=value, timeout=30)
    response.raise_for_status()


def run(args, root):
    if args.publish_only:
        publish(json.loads((root / 'latest.json').read_text(encoding='utf-8')))
        return 0
    require_active_research('granville_stock_portfolio', ROOT, POLICY['scope'])
    code = capture_code_provenance()
    if code['tracked_dirty']:
        raise ValueError('Clean committed source required')
    now = datetime.now(timezone.utc)
    day = str(completed_session(now).date())
    db = open_state(root / 'state.sqlite3')
    try:
        prior = db.execute('SELECT payload FROM plans WHERE date=?', (day,)).fetchone()
        if prior and not args.preflight:
            value = bundle(db, day, json.loads(prior[0])['input_hash'], 'already_recorded')
        else:
            input_hash = fingerprint({'day': day, 'code': code})
            folder = root / 'captures' / now.strftime('%Y%m%dT%H%M%S%fZ')
            failures = 0
            late = False
            try:
                require_timing(day, now)
            except ValueError:
                late = True
            status = 'missed_decision_cutoff' if late else 'input_blocked'
            # An empty late account has no settlement work; do not collect solely
            # to manufacture a late plan. Explicit preflight only tests inputs.
            if args.preflight or not late or db.execute('SELECT 1 FROM plans LIMIT 1').fetchone():
                try:
                    dates, bars, eligible, input_hash, failures = capture(folder, day, required_symbols(db), code)
                    if capture_code_provenance()['source_sha256'] != code['source_sha256']:
                        raise ValueError('Source changed during capture')
                    ready = datetime.now(timezone.utc)
                    try:
                        require_timing(day, ready)
                    except ValueError:
                        late = True
                    if args.preflight:
                        if not failures:
                            decision(initial_accounts(), dates, bars, eligible)
                        status = 'input_blocked' if failures else 'preflight_ready'
                    else:
                        status = advance(db, dates, bars, eligible, input_hash, ready.isoformat(), allow_plan=not late and not failures)
                        if failures:
                            status = 'input_blocked'
                except (ValueError, KeyError, RuntimeError) as error:
                    status = 'settlement_blocked' if isinstance(error, SettlementBlocked) else 'input_blocked'
                    failures = max(failures, 1)
                    write_json(folder / 'blocked.json', {'reason': str(error)[:250], 'code': code})
            value = bundle(db, day, input_hash, status, failures)
        write_json(root / 'latest.json', value)
        publish(value)
        print(json.dumps({'status': value['status'], 'data_date': day, 'shadow_sessions': value['shadow_sessions'],
                          'coverage_failures': value['coverage_failures']}), flush=True)
        return 0
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish-only', action='store_true')
    parser.add_argument('--preflight', action='store_true', help='Check native current inputs; never create plans/fills')
    args = parser.parse_args()
    if args.publish_only and args.preflight:
        parser.error('Publication-only cannot acquire inputs')
    root = settings.project_root / 'artifacts/research/granville-shadow-v1'
    with _command_lock(root, 'granville-shadow') as acquired:
        if not acquired:
            raise RuntimeError('Granville shadow already running')
        return run(args, root)


if __name__ == '__main__':
    raise SystemExit(main())
