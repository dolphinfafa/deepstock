"""Persistent official-source identities without rewriting historical events."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from urllib.parse import urlsplit

import httpx
import pandas as pd

from .announcements import AnnouncementUnavailable, VerificationDeferred, identity


SCHEMA = """
CREATE TABLE IF NOT EXISTS announcement_identity (
    identity_key TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    event_id TEXT NOT NULL REFERENCES announcement_event(event_id),
    PRIMARY KEY(identity_key, stock_code)
);
CREATE TABLE IF NOT EXISTS announcement_source_observation (
    source TEXT NOT NULL,
    source_key TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    event_id TEXT REFERENCES announcement_event(event_id),
    first_observed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    reason TEXT,
    raw_json TEXT NOT NULL,
    PRIMARY KEY(source, source_key, stock_code)
);
CREATE TABLE IF NOT EXISTS announcement_document_hash (
    url TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS announcement_identity_migration (version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS announcement_source_run (
    run_id TEXT NOT NULL REFERENCES collector_run(run_id),
    source TEXT NOT NULL,
    report_json TEXT NOT NULL,
    PRIMARY KEY(run_id, source)
);
CREATE TABLE IF NOT EXISTS announcement_identity_retry (
    source TEXT NOT NULL,
    source_key TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    observation_mode TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    next_attempt_at TEXT NOT NULL,
    PRIMARY KEY(source, source_key, stock_code)
);
"""


def keys_for(row):
    return {key for key in (row.get('canonical_key'), identity(row.get('url'))) if isinstance(key, str) and key}


def register_identity(db, event_id, stock, row):
    for key in keys_for(row):
        existing = db.execute('SELECT event_id FROM announcement_identity WHERE identity_key=? AND stock_code=?',
                              (key, stock)).fetchone()
        if existing and existing[0] != event_id:
            raise AnnouncementUnavailable('Conflicting official announcement identities')
        db.execute('INSERT OR IGNORE INTO announcement_identity VALUES (?, ?, ?)', (key, stock, event_id))


def seed_legacy_identities(db):
    db.executescript(SCHEMA)
    # Old pending rows have no retained mode: recover conservatively as backfill.
    db.execute('''INSERT OR IGNORE INTO announcement_identity_retry
        (source, source_key, stock_code, observation_mode, next_attempt_at)
        SELECT source, source_key, stock_code, 'backfill', first_observed_at
        FROM announcement_source_observation WHERE event_id IS NULL AND reason IS NOT NULL''')
    if not db.execute('SELECT 1 FROM announcement_identity_migration WHERE version=1').fetchone():
        for row in db.execute('SELECT event_id, stock_code, url FROM announcement_event').fetchall():
            register_identity(db, row['event_id'], row['stock_code'], dict(row))
        db.execute('INSERT INTO announcement_identity_migration VALUES (1)')
    db.commit()


def _digest(db, service, url):
    row = db.execute('SELECT sha256 FROM announcement_document_hash WHERE url=?', (url,)).fetchone()
    if row:
        return row[0]
    if service is None:
        raise AnnouncementUnavailable('Document identity service unavailable')
    value = service.document_hash(url)
    for alias in getattr(service, 'document_aliases', {}).get(url, (url,)):
        db.execute('INSERT OR IGNORE INTO announcement_document_hash VALUES (?, ?)', (alias, value))
    return value


def _is_sse(url):
    return urlsplit(str(url)).hostname in {'www.sse.com.cn', 'static.sse.com.cn'}


def _resolve(db, row, stock, source_date, service):
    keys = keys_for(row)
    # Only previously verified redirect/PDF aliases can expand an identity.
    for alias in db.execute('''SELECT b.url FROM announcement_document_hash a
            JOIN announcement_document_hash b ON a.sha256=b.sha256 WHERE a.url=?''', (row['url'],)):
        key = identity(alias[0])
        if key:
            keys.add(key)
    matches = {result[0] for key in keys for result in db.execute(
        'SELECT event_id FROM announcement_identity WHERE identity_key=? AND stock_code=?', (key, stock))}
    if len(matches) > 1:
        raise AnnouncementUnavailable('Conflicting source aliases')
    if matches:
        return matches.pop()
    # Only the SSE mirror uses different document IDs. Dates/stocks narrow PDF
    # comparisons; neither dates, names nor identical titles establish identity.
    candidates = [dict(item) for item in db.execute(
        'SELECT event_id, url FROM announcement_event WHERE stock_code=? AND source_date=?', (stock, source_date))
        if _is_sse(item['url']) != _is_sse(row['url'])]
    if not candidates:
        return None
    digest = _digest(db, service, row['url'])
    failed = False
    for candidate in candidates:
        try:
            if _digest(db, service, candidate['url']) == digest:
                matches.add(candidate['event_id'])
        except VerificationDeferred:
            raise
        except (httpx.HTTPError, ValueError, RuntimeError):
            failed = True
    if len(matches) > 1:
        raise AnnouncementUnavailable('Multiple existing events have the same document')
    if failed:
        raise AnnouncementUnavailable('Cross-source document identity could not be verified')
    if matches:
        return matches.pop()
    return None


def store_official_frame(db, frame, observation_mode, service=None, *, respect_backoff=False, now=None):
    now = now or datetime.now(timezone.utc)
    stamp = now.astimezone(timezone.utc).isoformat()
    stats = {'received': len(frame), 'inserted': 0, 'updated': 0, 'merged': 0, 'pending': 0}
    # Each row carries the actual response-read timestamp, never request start
    # or the nominal publication date supplied by the provider.
    retries = {(r['source'], r['source_key'], r['stock_code']): dict(r)
               for r in db.execute('SELECT * FROM announcement_identity_retry')}
    rows = frame.to_dict('records')
    if respect_backoff:
        rows.sort(key=lambda r: retries.get((r['source'], str(r['source_key']), r['ts_code'][:6]), {}).get('last_attempt_at') or '')
    for row in rows:
        stock = row['ts_code'][:6]
        observed = datetime.fromisoformat(row['observed_at'])
        if observed.tzinfo is None:
            raise ValueError('Official observations require a timezone')
        seen = observed.isoformat(timespec='microseconds')
        source_date = row['ann_date'][:10]
        source, key = row['source'], str(row['source_key'])
        retry = retries.get((source, key, stock))
        old = db.execute('''SELECT first_observed_at FROM announcement_source_observation
            WHERE source=? AND source_key=? AND stock_code=?''', (source, key, stock)).fetchone()
        first_seen = old[0] if old else seen
        raw = json.dumps(row, ensure_ascii=False, default=str)
        reason = None
        event_id = None
        deferred = False
        try:
            if respect_backoff and retry and datetime.fromisoformat(retry['next_attempt_at']) > now:
                raise VerificationDeferred('Disclosure identity retry is waiting for backoff')
            event_id = _resolve(db, row, stock, source_date, service)
        except (httpx.HTTPError, ValueError, RuntimeError) as error:
            reason = f'{type(error).__name__}: {error}'[:250]
            deferred = isinstance(error, VerificationDeferred)
            stats['pending'] += 1
        if reason is None:
            if event_id is None:
                native_key = f'{key}|{stock}'
                event_id = hashlib.sha256(f'{source}|{native_key}'.encode()).hexdigest()
                db.execute('''INSERT INTO announcement_event
                    (event_id, source, source_key, stock_code, ts_code, company_name,
                     source_date, title, url, first_seen_at, last_seen_at, observation_mode, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (event_id, source, native_key, stock, row['ts_code'], row.get('name'), source_date,
                     row['title'], row['url'], first_seen, seen,
                     retry['observation_mode'] if retry else observation_mode, raw))
                stats['inserted'] += 1
            else:
                stats['merged'] += 1
            register_identity(db, event_id, stock, row)
            db.execute('DELETE FROM announcement_identity_retry WHERE source=? AND source_key=? AND stock_code=?',
                       (source, key, stock))
        else:
            attempts = (retry['attempts'] if retry else 0) + (0 if deferred else 1)
            last = (retry['last_attempt_at'] if retry else None) if deferred else stamp
            next_at = (retry['next_attempt_at'] if retry else stamp) if deferred else (
                now + timedelta(seconds=min(21600, 600 * 2 ** min(attempts - 1, 6)))).astimezone(timezone.utc).isoformat()
            db.execute('''INSERT INTO announcement_identity_retry VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, source_key, stock_code) DO UPDATE SET
                attempts=excluded.attempts, last_attempt_at=excluded.last_attempt_at, next_attempt_at=excluded.next_attempt_at''',
                (source, key, stock, retry['observation_mode'] if retry else observation_mode, attempts, last, next_at))
        # Store each mirror's evidence separately. Existing event payloads,
        # first_seen_at and live/backfill mode remain immutable.
        db.execute('''INSERT INTO announcement_source_observation VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_key, stock_code) DO UPDATE SET
            event_id=excluded.event_id, last_observed_at=MAX(announcement_source_observation.last_observed_at, excluded.last_observed_at),
            reason=excluded.reason, raw_json=excluded.raw_json''',
            (source, key, stock, event_id, seen, seen, reason, raw))
    db.commit()
    return stats


def retry_pending(db, service, *, now=None):
    """Recover a bounded oldest-first batch using the same per-round PDF budget."""
    now = now or datetime.now(timezone.utc)
    pending = db.execute('''SELECT o.raw_json, r.observation_mode FROM announcement_identity_retry r
        JOIN announcement_source_observation o USING(source, source_key, stock_code)
        WHERE julianday(r.next_attempt_at)<=julianday(?)
        ORDER BY r.last_attempt_at IS NOT NULL, julianday(r.last_attempt_at), o.first_observed_at
        LIMIT 200''', (now.isoformat(),)).fetchall()
    recovered = 0
    for item in pending:
        stats = store_official_frame(db, pd.DataFrame([json.loads(item['raw_json'])]),
                                     item['observation_mode'], service, respect_backoff=True, now=now)
        recovered += stats['inserted'] + stats['merged']
    return {'identity_recovered': recovered,
            'identity_pending': db.execute('SELECT COUNT(*) FROM announcement_identity_retry').fetchone()[0]}
