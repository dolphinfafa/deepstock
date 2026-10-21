"""Regression coverage for real receipt, fallback and queue failure modes."""
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest

from deepstock.scheduled_mail import TaskResult, outbox
from deepstock.scheduled_summary import MARKER, MAX_BYTES, marked_payload, print_result
from deepstock.strategies.cn.auction.announcements import AnnouncementUnavailable, OfficialAnnouncements, VerificationDeferred
from deepstock.strategies.cn.auction.announcement_store import retry_pending, store_official_frame
from deepstock.strategies.cn.auction.forward import collect_announcements, open_store
from deepstock.strategies.cn.auction.tushare_history import normalize_tushare_stk_mins_0931
from scripts.backtest_auction_features import _minute_attempt_queue
from scripts.run_scheduled_task import receipt, run

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
DAY = date(2026, 10, 21)


def row(source='cninfo', number=1):
    url = f'https://static.cninfo.com.cn/{number}.pdf' if source == 'cninfo' else f'https://www.sse.com.cn/{number}.pdf'
    return {'source': source, 'source_key': str(number), 'canonical_key': f'cninfo:{number}' if source == 'cninfo' else 'url:' + url,
            'ts_code': '600001.SH', 'ann_date': DAY.isoformat(), 'title': 'notice', 'url': url,
            'observed_at': NOW.isoformat()}


def service(tmp_path, handler, **kwargs):
    return OfficialAnnouncements(httpx.Client(transport=httpx.MockTransport(handler)),
                                 capture=lambda *a, **k: 'evidence', pause=lambda _: None,
                                 root=tmp_path, **kwargs)


def test_large_report_uses_bounded_aggregate_summary_and_preserves_regular_output(monkeypatch, capsys):
    payload = {'as_of_date': '2026-10-21', 'strategies': {'model_baseline': {'completed_cohorts': 100}},
               'positions': ['private'] * 40000}
    print_result(payload)
    full = capsys.readouterr().out
    assert len(full) > 128 * 1024 and json.loads(full) == payload
    monkeypatch.setenv('DEEPSTOCK_SCHEDULED_SUMMARY', '1')
    print_result(payload)
    compact = capsys.readouterr().out
    assert compact.startswith(MARKER) and len(compact.encode()) < MAX_BYTES
    summary = marked_payload(('log' * 100000 + '\n' + compact)[-128 * 1024:])
    result = receipt('auction-report', NOW, NOW, 0, summary)
    assert result.status == 'completed' and result.stats['completed_cohorts'] == 100
    assert 'private' not in compact
    assert marked_payload(MARKER + '{invalid') == {}
    assert marked_payload('legacy\n{}') is None


def test_real_large_child_invoked_once_and_receipt_retains_exit(tmp_path, monkeypatch, capfd):
    (tmp_path / 'large_child.py').write_text(
        'from deepstock.scheduled_summary import print_result\n'
        'print("x" * 300000)\n'
        'print_result({"status":"ok","rows_inserted":2,"private":["secret"]*40000})\n', encoding='utf-8')
    monkeypatch.setitem(__import__('scripts.run_scheduled_task', fromlist=['MODULES']).MODULES,
                        'auction-collect', ('large_child', []))
    assert run('auction-collect', [], tmp_path) == 0
    capfd.readouterr()
    db = outbox(tmp_path)
    records = db.execute('SELECT payload FROM results').fetchall()
    db.close()
    assert len(records) == 1
    result = TaskResult.model_validate_json(records[0][0])
    assert result.status == 'completed' and result.stats['rows_inserted'] == 2
    assert 'secret' not in records[0][0]


def test_lock_skip_is_skipped_with_string_dates():
    result = receipt('auction-collect', NOW, NOW, 0, {'status': 'skipped_locked', 'dates': ['2026-10-21']})
    assert result.status == 'skipped' and result.reason == 'skipped_locked'


def test_official_redirect_aliases_are_retained_and_cached_across_restart(tmp_path):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        if request.url.host == 'www.sse.com.cn':
            return httpx.Response(301, headers={'Location': str(request.url).replace('www.', 'static.')})
        return httpx.Response(200, content=b'%PDF-1.4\nsame document')

    docs = service(tmp_path, handler)
    db = open_store(tmp_path)
    store_official_frame(db, pd.DataFrame([row()]), 'live', now=NOW)
    result = store_official_frame(db, pd.DataFrame([row('sse')]), 'live', docs, now=NOW)
    assert result['merged'] == 1 and len(requests) == 3
    assert db.execute('SELECT COUNT(*) FROM announcement_document_hash').fetchone()[0] == 3
    db.close()
    db = open_store(tmp_path)
    redirected = row('sse')
    redirected.update(source_key='static-copy', url='https://static.sse.com.cn/1.pdf', canonical_key='url:https://static.sse.com.cn/1.pdf')
    result = store_official_frame(db, pd.DataFrame([redirected]), 'live', now=NOW)
    assert result['merged'] == 1 and result['pending'] == 0
    assert db.execute('SELECT COUNT(*) FROM announcement_event').fetchone()[0] == 1
    db.close()


@pytest.mark.parametrize('target', ['https://example.org/x.pdf', 'http://static.sse.com.cn/x.pdf',
                                  'https://user:pass@static.sse.com.cn/x.pdf', 'https://static.sse.com.cn:444/x.pdf',
                                  'https://www.sse.com.cn/x.pdf'])
def test_redirects_validate_each_hop_before_contact(tmp_path, target):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(301, headers={'Location': target})

    docs = service(tmp_path, handler)
    with pytest.raises(AnnouncementUnavailable):
        docs.document_hash('https://www.sse.com.cn/x.pdf')
    assert len(requests) == 1


def test_redirect_limit_and_pdf_count_time_budgets(tmp_path):
    requests = []

    def redirects(request):
        requests.append(request)
        return httpx.Response(302, headers={'Location': f'https://static.sse.com.cn/{len(requests)}.pdf'})

    docs = service(tmp_path, redirects)
    with pytest.raises(AnnouncementUnavailable, match='redirect limit'):
        docs.document_hash('https://www.sse.com.cn/a.pdf')
    assert len(requests) == 4
    docs = service(tmp_path, lambda r: httpx.Response(200, content=b'%PDF-count'), max_documents=1)
    docs.document_hash('https://www.sse.com.cn/one.pdf')
    with pytest.raises(VerificationDeferred, match='document budget'):
        docs.document_hash('https://www.sse.com.cn/two.pdf')
    ticks = iter([0, 61])
    docs = service(tmp_path, lambda r: pytest.fail('expired budget must not fetch'), monotonic=lambda: next(ticks))
    with pytest.raises(VerificationDeferred, match='time budget'):
        docs.document_hash('https://www.sse.com.cn/one.pdf')


def test_pending_budget_backoff_progress_and_first_seen_survive_restart(tmp_path):
    db = open_store(tmp_path)
    store_official_frame(db, pd.DataFrame([row()]), 'live', now=NOW)
    old_event = tuple(db.execute('SELECT * FROM announcement_event').fetchone())
    docs = service(tmp_path, lambda r: httpx.Response(200, content=b'%PDF-identical'), max_documents=1)
    stats = store_official_frame(db, pd.DataFrame([row('sse')]), 'live', docs, respect_backoff=True, now=NOW)
    assert stats['pending'] == 1
    retry = dict(db.execute('SELECT * FROM announcement_identity_retry').fetchone())
    assert retry['attempts'] == 0  # Budget deferral is not a network failure.
    db.close()
    db = open_store(tmp_path)
    docs = service(tmp_path, lambda r: httpx.Response(200, content=b'%PDF-identical'))
    assert retry_pending(db, docs, now=NOW + timedelta(minutes=10)) == {'identity_recovered': 1, 'identity_pending': 0}
    assert tuple(db.execute('SELECT * FROM announcement_event').fetchone()) == old_event
    assert db.execute('SELECT first_observed_at FROM announcement_source_observation WHERE source="sse"').fetchone()[0] == NOW.isoformat(timespec='microseconds')
    assert len(docs.document_hashes) == 1  # The first completed PDF survived budget exhaustion.
    db.close()


def test_failed_pending_defers_network_but_healthy_primary_keeps_collecting(tmp_path):
    db = open_store(tmp_path)
    store_official_frame(db, pd.DataFrame([row()]), 'live', now=NOW)
    requests = []

    def fail(request):
        requests.append(request)
        return httpx.Response(503)

    docs = service(tmp_path, fail)
    store_official_frame(db, pd.DataFrame([row('sse')]), 'live', docs, respect_backoff=True, now=NOW)
    assert len(requests) == 1
    assert retry_pending(db, docs, now=NOW + timedelta(minutes=9))['identity_pending'] == 1
    assert len(requests) == 1
    db.close()
    # Reopen and retry with an actual later clock; exponential backoff persists.
    db = open_store(tmp_path)
    docs = service(tmp_path, fail)
    retry_pending(db, docs, now=NOW + timedelta(minutes=10))
    state = dict(db.execute('SELECT * FROM announcement_identity_retry').fetchone())
    assert state['attempts'] == 2
    assert datetime.fromisoformat(state['next_attempt_at']) == NOW + timedelta(minutes=30)
    db.close()

    class HealthyPrimary:
        def fetch(self, source, day):
            if source == 'sse':
                raise AnnouncementUnavailable('mirror unavailable')
            return pd.DataFrame([row()]) if source == 'cninfo' else pd.DataFrame(), {'source': source, 'status': 'complete'}

    # Use future retry timestamps relative to runtime so this tests status isolation.
    db = open_store(tmp_path)
    db.execute('UPDATE announcement_identity_retry SET next_attempt_at=?', ((datetime.now(timezone.utc) + timedelta(hours=6)).isoformat(),))
    db.commit()
    db.close()
    result = collect_announcements(tmp_path, [DAY], service=HealthyPrimary())
    assert result['status'] == 'ok' and result['identity_pending'] == 1


def test_pending_rotation_uses_unattempted_then_oldest_and_never_same_china_day():
    windows = [(date(2026, 9, d), date(2026, 9, d)) for d in (7, 10, 11)]
    manifest = {'20260907_20260907': {'status': 'incomplete', 'attempts': 1}}
    assert _minute_attempt_queue(windows, manifest, NOW) == [windows[1], windows[2], windows[0]]
    manifest['20260910_20260910'] = {'status': 'running', 'total_attempts': 1, 'last_attempt_at_utc': NOW.isoformat()}
    assert _minute_attempt_queue(windows, json.loads(json.dumps(manifest)), NOW) == [windows[2], windows[0]]
    # UTC date unchanged, China has crossed midnight.
    tomorrow_cn = NOW.replace(hour=17)
    assert _minute_attempt_queue(windows, manifest, tomorrow_cn) == [windows[2], windows[0], windows[1]]


def test_parent_records_actual_rotated_date_and_reports_no_progress(tmp_path, monkeypatch):
    from scripts.run_csi300_minute_backfill import run_backfill
    source = tmp_path / 'source'
    root = source / 'artifacts/auction_history_tushare'
    root.mkdir(parents=True)
    (root / 'minute_fetch_report.json').write_text(json.dumps({'remaining_windows': 12, 'remaining_dates': ['2026-09-07', '2026-09-10']}))
    calls = []

    def child(command, **kwargs):
        calls.append(command)
        (root / 'minute_manifest.json').write_text(json.dumps({'20260910_20260910': {'status': 'incomplete'}}))
        (root / 'minute_fetch_report.json').write_text(json.dumps({'remaining_windows': 12, 'remaining_dates': ['2026-09-07', '2026-09-10'],
            'attempted_dates': ['2026-09-10'], 'reservation': kwargs['env']['DEEPSTOCK_MINUTE_RESERVATION']}))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr('scripts.run_csi300_minute_backfill.subprocess.run', child)
    monkeypatch.setattr('scripts.run_csi300_minute_backfill.synchronize', lambda *a: None)
    result = run_backfill(source, Path('/python'), tmp_path / 'destination')
    assert result['status'] == 'input_blocked' and result['no_progress']
    assert result['attempted_dates'] == ['2026-09-10'] and len(calls) == 1
    ledger = json.loads((tmp_path / 'destination/csi300_sync/minute_quota_ledger.json').read_text())
    assert ledger['attempts'][0]['window_status'] == 'incomplete'


def test_minute_filter_audit_preserves_exact_time_and_invalid_price_gates():
    frame = pd.DataFrame([{'ts_code': f'{code:06d}.SH', 'trade_time': stamp,
                           'open': 10, 'high': 11, 'low': 9, 'close': 10, 'vol': vol, 'amount': amount}
                          for code, stamp, vol, amount in [(1, '2026-10-21 09:31:00', 100, 1000),
                              (2, '2026-10-21 09:32:00', 100, 1000), (3, '2026-10-21 09:31:00', 0, 1000),
                              (4, '2026-10-21 09:31:00', 100, 2000)]])
    result = normalize_tushare_stk_mins_0931(frame, DAY)
    assert result.stock_code.tolist() == ['000001']
    assert result.attrs['normalization_audit'] == {'received_rows': 4, 'provider_rows': 4,
        'exact_0931_rows': 3, 'valid_activity_rows': 2, 'valid_vwap_rows': 1, 'deduplicated_rows': 1}


@pytest.mark.parametrize('empty', [None, pd.DataFrame(columns=['trade_time'])])
def test_empty_minute_response_is_captured_once_without_extra_call(tmp_path, monkeypatch, empty):
    from deepstock.strategies.cn.auction.providers import CapturedTushare
    from deepstock.strategies.cn.auction.tushare_history import fetch_tushare_historical_0931
    captures, calls = [], []

    class Client:
        def stk_mins(self, **kwargs):
            calls.append(kwargs)
            return empty

    def capture(*args, **kwargs):
        captures.append((args, kwargs))
        return 'empty-raw-version'

    monkeypatch.setattr('deepstock.strategies.cn.auction.quota.consume_minute_request', lambda: None)
    monkeypatch.setattr('deepstock.data.capture.capture_response', capture)
    result = fetch_tushare_historical_0931(['600001.SH'], DAY, pro=CapturedTushare(Client(), state_path=tmp_path / 'state.db'))
    assert result.empty and len(calls) == len(captures) == 1
    assert captures[0][0][2]['rows'] == [] and captures[0][1]['restricted']
    assert result.attrs['raw_version'] == 'empty-raw-version'
    assert result.attrs['normalization_audit']['received_rows'] == 0


def test_real_child_selection_rotates_after_empty_and_persists_metadata(tmp_path, monkeypatch):
    import scripts.backtest_auction_features as child
    snapshots = pd.DataFrame([{'stock_code': '600001', 'ts_code': '600001.SH'}])
    monkeypatch.setattr(child, 'read_clean_csv', lambda *a, **kw: snapshots.copy())
    monkeypatch.setattr(child, '_load_historical_members', lambda codes: [])
    monkeypatch.setattr(child, '_historical_membership', lambda dates, _: {d: {'600001'} for d in dates})
    monkeypatch.setattr(child, 'get_pro', lambda: SimpleNamespace(trade_cal=lambda **kw: pd.DataFrame({'cal_date': ['20260907', '20260910']})))
    requests = []

    def empty(symbols, day):
        requests.append(day)
        return pd.DataFrame()

    monkeypatch.setattr(child, 'fetch_tushare_historical_0931', empty)
    args = SimpleNamespace(output_root=str(tmp_path), start=date(2026, 9, 7), end=date(2026, 9, 10),
        minute_source='tushare', date_concurrency=1, minimum_date_coverage=.9, maximum_windows_per_run=1,
        retries=0, request_spacing=0, retry_wait_seconds=0)
    first = child.fetch_execution_entries(args)
    second = child.fetch_execution_entries(args)
    third = child.fetch_execution_entries(args)
    assert first['attempted_dates'] == ['2026-09-07']
    assert second['attempted_dates'] == ['2026-09-10']
    assert third['attempted_dates'] == [] and len(requests) == 2
    assert first['remaining_dates'] == second['remaining_dates'] == ['2026-09-07', '2026-09-10']
    persisted = json.loads((tmp_path / 'minute_manifest.json').read_text())
    assert all(item['total_attempts'] == 1 and item['last_attempt_at_utc'] for item in persisted.values())


@pytest.mark.parametrize('outcome', ['timeout', 'quota', 'all_ready'])
def test_parent_handles_timeout_quota_and_only_finalizes_all_ready(tmp_path, monkeypatch, outcome):
    from scripts.run_csi300_minute_backfill import run_backfill, REPORT_NAME
    source = tmp_path / 'source'
    root = source / 'artifacts/auction_history_tushare'
    root.mkdir(parents=True)
    (root / 'minute_fetch_report.json').write_text(json.dumps({'remaining_windows': 1, 'remaining_dates': ['2026-09-29']}))
    calls = []

    def child(command, **kwargs):
        calls.append(command)
        if 'finalize-minute-backfill' in command:
            (root / f'{REPORT_NAME}.json').write_text('{}')
            return SimpleNamespace(returncode=0)
        if outcome == 'timeout':
            raise subprocess.TimeoutExpired(command, 180)
        status = {'status': 'ok'} if outcome == 'all_ready' else {'status': 'error', 'error': '频率超限'}
        (root / 'minute_manifest.json').write_text(json.dumps({'20260929_20260929': status}))
        (root / 'minute_fetch_report.json').write_text(json.dumps({'remaining_windows': 0 if outcome == 'all_ready' else 1,
            'attempted_dates': ['2026-09-29'], 'reservation': kwargs['env']['DEEPSTOCK_MINUTE_RESERVATION']}))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr('scripts.run_csi300_minute_backfill.subprocess.run', child)
    monkeypatch.setattr('scripts.run_csi300_minute_backfill.synchronize', lambda *a: None)
    result = run_backfill(source, Path('/python'), tmp_path / 'destination')
    assert result['status'] == {'timeout': 'fetch_failed', 'quota': 'waiting_for_provider_quota', 'all_ready': 'complete'}[outcome]
    assert len(calls) == (2 if outcome == 'all_ready' else 1)
