from datetime import date, datetime
import hashlib
import json

import httpx
import pandas as pd
import pytest

from deepstock.strategies.cn.auction.announcements import (
    AnnouncementUnavailable, OfficialAnnouncements, SHANGHAI, fetch_official_announcements,
)
from deepstock.strategies.cn.auction.forward import collect_announcements, open_store, store_announcement_frame

DAY = date(2026, 10, 20)


def native(source, number=1):
    if source == 'cninfo':
        return {'secCode': '600001', 'secName': '公司', 'announcementId': str(number),
                'announcementTitle': '<em>回购股份</em>公告',
                'announcementTime': int(datetime(2026, 10, 20, tzinfo=SHANGHAI).timestamp() * 1000),
                'adjunctUrl': f'finalpage/2026-10-20/{number}.PDF'}
    if source == 'sse':
        return {'SECURITY_CODE': '600001', 'SECURITY_NAME': '公司', 'SSEDATE': DAY.isoformat(),
                'TITLE': '回购股份公告', 'URL': f'/disclosure/{number}.pdf', 'ADDDATE': '2026-10-19 20:00:00'}
    return {'secCode': ['000001'], 'secName': ['公司'], 'annId': str(number), 'id': f'uuid-{number}',
            'title': '公司：回购股份公告', 'publishTime': '2026-10-20 20:00:00',
            'attachPath': f'/disc/{number}.pdf'}


def payload(source, rows, total):
    if source == 'cninfo':
        return {'announcements': rows, 'totalAnnouncement': total, 'totalpages': 1}
    if source == 'sse':
        return {'result': rows, 'pageHelp': {'total': total}}
    return {'data': rows, 'announceCount': total}


def make_service(tmp_path, handler, **kwargs):
    captures = []

    def capture(provider, endpoint, value, market, **meta):
        captures.append(value)
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OfficialAnnouncements(client, capture=capture, pause=lambda _: None, root=tmp_path, **kwargs), captures


@pytest.mark.parametrize('source', ['cninfo', 'sse', 'szse'])
def test_official_pages_register_clean_inputs_and_use_response_observation(tmp_path, source):
    requests = []

    def handler(request):
        requests.append(request)
        page = len(requests)
        return httpx.Response(200, json=payload(source, [native(source, page)], 2))

    service, captures = make_service(tmp_path, handler, page_size=1)
    frame, report = service.fetch(source, DAY)
    assert len(requests) == 2 and len(frame) == 2
    assert report['status'] == 'complete' and report['native_rows'] == 2
    assert report['clean_version'] == frame.attrs['data_version']
    assert frame.title.tolist() == ['回购股份公告'] * 2
    assert frame.observed_at.tolist() == [value['observed_at'] for value in captures]
    assert all(datetime.fromisoformat(t).tzinfo for t in frame.observed_at)
    if source == 'sse':
        assert frame.published_at.isna().all()  # ADDDATE is never availability.


def test_cninfo_uses_supported_page_size_and_default_stable_sort(tmp_path):
    seen = []

    def handler(request):
        from urllib.parse import parse_qs
        query = parse_qs(request.content.decode(), keep_blank_values=True)
        seen.append(query)
        return httpx.Response(200, json=payload('cninfo', [], 0))

    service, _ = make_service(tmp_path, handler)
    service.fetch('cninfo', DAY)
    assert seen[0]['pageSize'] == ['30']
    assert seen[0]['sortName'] == seen[0]['sortType'] == ['']


@pytest.mark.parametrize('problem', ['repeat', 'changed_total', 'truncated', 'html', 'missing_total', 'outside_date', 'null_timestamp'])
def test_incomplete_or_invalid_official_scans_are_captured_and_blocked(tmp_path, problem):
    calls = []

    def handler(request):
        calls.append(request)
        page = len(calls)
        if problem == 'html':
            return httpx.Response(200, text='<html>maintenance</html>')
        row = native('cninfo', page if problem != 'repeat' else 1)
        if problem == 'outside_date':
            row['announcementTime'] = 0
        if problem == 'null_timestamp':
            row['announcementTime'] = None
        body = payload('cninfo', [] if problem == 'truncated' and page == 2 else [row],
                       3 if problem == 'changed_total' and page == 2 else 2)
        if problem == 'missing_total':
            del body['totalAnnouncement']
        return httpx.Response(200, json=body)

    service, captures = make_service(tmp_path, handler, page_size=1)
    with pytest.raises(AnnouncementUnavailable):
        service.fetch('cninfo', DAY)
    assert len(captures) == len(calls) > 0
    assert not list((tmp_path / 'artifacts/providers/official-announcements').glob('*.csv.gz'))


def test_source_failure_uses_both_exchanges_and_reports_partial_coverage(tmp_path):
    class Service:
        def fetch(self, source, day):
            if source == 'cninfo':
                raise httpx.ReadTimeout('source timeout')
            return pd.DataFrame([{'source': source, 'observed_at': '2026-10-20T21:00:00+08:00'}]), {'source': source, 'status': 'complete'}

    frame, reports, coverage = fetch_official_announcements(DAY, Service())
    assert coverage and set(frame.source) == {'sse', 'szse'}
    assert reports[0]['status'] == 'unavailable'

    class Incomplete(Service):
        def fetch(self, source, day):
            if source == 'sse':
                raise AnnouncementUnavailable('not complete')
            return super().fetch(source, day)

    frame, _, coverage = fetch_official_announcements(DAY, Incomplete())
    assert not coverage and frame.source.tolist() == ['szse']


def test_successful_empty_sources_are_verified_but_failed_pages_keep_evidence_versions(tmp_path):
    def handler(request):
        if 'cninfo' in request.url.host:
            return httpx.Response(200, json={'totalAnnouncement': 2, 'announcements': [native('cninfo')]})
        source = 'sse' if 'sse' in request.url.host else 'szse'
        return httpx.Response(200, json=payload(source, [], 0))

    service, _ = make_service(tmp_path, handler)
    frame, reports, coverage = fetch_official_announcements(DAY, service)
    assert frame.empty and coverage
    assert reports[0]['status'] == 'unavailable' and len(reports[0]['versions']) == 2
    assert reports[1]['native_rows'] == reports[2]['native_rows'] == 0


def test_sse_schema_failure_does_not_prevent_other_sources(tmp_path):
    def handler(request):
        if 'sse' in request.url.host:
            return httpx.Response(200, json={'pageHelp': [], 'result': []})
        source = 'cninfo' if 'cninfo' in request.url.host else 'szse'
        return httpx.Response(200, json=payload(source, [], 0))

    service, _ = make_service(tmp_path, handler)
    frame, reports, coverage = fetch_official_announcements(DAY, service)
    assert frame.empty and coverage and reports[1]['status'] == 'unavailable'


def official_row(source='cninfo', number=1, code='600001.SH'):
    return {'source': source, 'source_key': str(number), 'canonical_key': f'cninfo:{number}',
            'ts_code': code, 'ann_date': DAY.isoformat(), 'title': '回购股份公告', 'name': '公司',
            'url': f'https://static.cninfo.com.cn/finalpage/2026-10-20/{number}.PDF',
            'observed_at': '2026-10-20T21:00:01.123456+08:00', 'capture_version': 'a' * 64}


def test_legacy_migration_and_cross_source_ids_preserve_events_and_restart(tmp_path):
    first = datetime(2026, 10, 20, 20, tzinfo=SHANGHAI)
    row = official_row()
    legacy = {k: row[k] for k in ('ts_code', 'ann_date', 'title', 'name', 'url')}
    db = open_store(tmp_path)
    store_announcement_frame(db, pd.DataFrame([legacy]), first, 'backfill')
    before = tuple(db.execute('SELECT * FROM announcement_event').fetchone())
    db.execute('DELETE FROM announcement_identity')
    db.execute('DELETE FROM announcement_identity_migration')
    db.commit()
    db.close()
    db = open_store(tmp_path)
    assert db.execute('SELECT COUNT(*) FROM announcement_identity').fetchone()[0] == 1
    szse = dict(row, source='szse', url='https://www.szse.cn/disc/uuid.pdf', source_key='uuid')
    stats = store_announcement_frame(db, pd.DataFrame([row, szse]), first, 'live')
    assert stats['inserted'] == 0 and stats['merged'] == 2
    assert tuple(db.execute('SELECT * FROM announcement_event').fetchone()) == before
    db.close()
    db = open_store(tmp_path)
    assert store_announcement_frame(db, pd.DataFrame([szse]), first)['inserted'] == 0
    assert tuple(db.execute('SELECT * FROM announcement_event').fetchone()) == before
    assert db.execute('SELECT COUNT(*) FROM announcement_source_observation').fetchone()[0] == 2
    db.close()


def test_sse_identity_requires_pdf_bytes_and_persists_hashes(tmp_path):
    db = open_store(tmp_path)
    first = datetime(2026, 10, 20, 19, tzinfo=SHANGHAI)
    store_announcement_frame(db, pd.DataFrame([official_row()]), first)
    sse = dict(official_row('sse'), canonical_key='url:https://www.sse.com.cn/one.pdf',
               source_key='one', url='https://www.sse.com.cn/one.pdf')
    result = store_announcement_frame(db, pd.DataFrame([sse]), first)
    assert result['pending'] == 1 and result['inserted'] == 0
    assert db.execute('SELECT reason FROM announcement_source_observation WHERE source="sse"').fetchone()[0]

    class Documents:
        calls = []

        def document_hash(self, url):
            self.calls.append(url)
            return 'b' * 64

    docs = Documents()
    result = store_announcement_frame(db, pd.DataFrame([sse]), first, identity_service=docs)
    assert result['merged'] == 1 and result['pending'] == 0 and len(docs.calls) == 2
    assert db.execute('SELECT COUNT(*) FROM announcement_event').fetchone()[0] == 1
    # A different title cannot force a duplicate; known URL identity is enough.
    sse['title'] = 'mirror headline'
    store_announcement_frame(db, pd.DataFrame([sse]), first, identity_service=docs)
    assert len(docs.calls) == 2
    assert db.execute('SELECT reason FROM announcement_source_observation WHERE source="sse"').fetchone()[0] is None
    db.close()


def test_same_titles_with_different_official_ids_stay_distinct_and_stock_scoped(tmp_path):
    db = open_store(tmp_path)
    rows = [official_row(), official_row(number=2), official_row(code='600002.SH')]
    result = store_announcement_frame(db, pd.DataFrame(rows), datetime.now(SHANGHAI))
    assert result['inserted'] == 3
    assert {r[0] for r in db.execute('SELECT first_seen_at FROM announcement_event')} == {rows[0]['observed_at']}
    db.close()


def test_sse_failover_recovery_and_hash_cache_survive_restart(tmp_path):
    first = datetime.now(SHANGHAI)
    sse = dict(official_row('sse'), canonical_key='url:https://www.sse.com.cn/one.pdf',
               source_key='one', url='https://www.sse.com.cn/one.pdf')
    db = open_store(tmp_path)
    store_announcement_frame(db, pd.DataFrame([sse]), first, 'backfill')
    before = tuple(db.execute('SELECT * FROM announcement_event').fetchone())

    class Documents:
        calls = []

        def document_hash(self, url):
            self.calls.append(url)
            return 'c' * 64

    docs = Documents()
    stats = store_announcement_frame(db, pd.DataFrame([official_row()]), first, identity_service=docs)
    assert stats['inserted'] == 0 and stats['merged'] == 1 and len(docs.calls) == 2
    db.close()
    db = open_store(tmp_path)
    szse = dict(official_row('szse'), url='https://www.szse.cn/new-mirror.pdf')
    store_announcement_frame(db, pd.DataFrame([szse]), first)  # shared ID, no further PDF needed
    assert tuple(db.execute('SELECT * FROM announcement_event').fetchone()) == before
    assert db.execute('SELECT COUNT(*) FROM announcement_document_hash').fetchone()[0] == 2
    db.close()


def test_collector_defaults_to_official_sources_and_retains_source_failures(tmp_path, monkeypatch):
    class Service:
        closed = False

        def fetch(self, source, day):
            if source != 'szse':
                raise AnnouncementUnavailable('source unavailable')
            return pd.DataFrame([official_row('szse', code='000001.SZ')]), {'source': source, 'status': 'complete'}

        def close(self):
            self.closed = True

    service = Service()
    monkeypatch.setattr('deepstock.strategies.cn.auction.forward.OfficialAnnouncements', lambda: service)
    result = collect_announcements(tmp_path, [DAY])
    assert result['status'] == 'input_blocked' and service.closed
    assert result['dates'][0]['inserted'] == 1
    result = collect_announcements(tmp_path, [DAY])
    assert result['dates'][0]['inserted'] == 0
    db = open_store(tmp_path)
    assert db.execute('SELECT COUNT(*) FROM announcement_event').fetchone()[0] == 1
    assert db.execute('SELECT COUNT(*) FROM announcement_source_run').fetchone()[0] == 6
    assert {r[0] for r in db.execute('SELECT status FROM collector_run')} == {'input_blocked'}
    db.close()


def test_document_verification_retains_pdf_and_rejects_html_and_foreign_hosts(tmp_path):
    service, captures = make_service(tmp_path, lambda request: httpx.Response(200, content=b'%PDF-1.4\nbytes'))
    digest = service.document_hash('https://www.sse.com.cn/test.pdf')
    assert digest == hashlib.sha256(b'%PDF-1.4\nbytes').hexdigest()
    assert captures[0]['sha256'] == digest
    assert (tmp_path / captures[0]['local_document']).read_bytes() == b'%PDF-1.4\nbytes'
    with pytest.raises(AnnouncementUnavailable):
        service.document_hash('https://example.com/test.pdf')
    service, _ = make_service(tmp_path, lambda request: httpx.Response(200, text='<html>blocked</html>'))
    with pytest.raises(AnnouncementUnavailable, match='not a PDF'):
        service.document_hash('https://www.sse.com.cn/blocked.pdf')
