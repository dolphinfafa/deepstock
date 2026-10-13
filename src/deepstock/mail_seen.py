"""Durable, narrowly authorized Seen flags after actual session processing."""
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
import re
import sqlite3
import time

from deepstock.mail_discovery import HEADER_FIELDS, header_identity


def migrate(db, path):
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='mail_processed'").fetchone():
        return
    backup = path.with_name(path.stem + '.before-seen-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + path.suffix)
    backup.touch(mode=0o600, exist_ok=False)
    with sqlite3.connect(backup) as target:
        db.backup(target)
        if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Private Seen backup failed')
    db.executescript('''BEGIN;
        CREATE TABLE mail_processed (message_key TEXT PRIMARY KEY, turn_id TEXT NOT NULL,
            evidence_type TEXT NOT NULL, item_id TEXT, observed_at REAL NOT NULL);
        CREATE TABLE mail_seen (folder TEXT NOT NULL, uidvalidity INTEGER NOT NULL, uid INTEGER NOT NULL,
            message_key TEXT NOT NULL, state TEXT NOT NULL, next_attempt REAL NOT NULL DEFAULT 0,
            last_error TEXT, PRIMARY KEY(folder,uidvalidity,uid));
        COMMIT;''')


def processing_evidence(item):
    kind = item.get('type')
    if kind == 'agentMessage' and item.get('text', '').strip():
        return True
    if kind in ('commandExecution', 'fileChange', 'mcpToolCall', 'dynamicToolCall'):
        # A declined approval or an in-progress placeholder is not execution.
        return item.get('status') in ('completed', 'failed')
    if kind == 'webSearch':
        return bool(item.get('action'))
    return False


def observe_processing(db, codex):
    rows = db.execute('''SELECT r.message_id,r.thread_id,t.turn_id FROM replies r
        JOIN reply_turns t ON t.reply_id=r.message_id JOIN mail_messages m ON m.message_key=r.message_id
        WHERE m.classification='confirmed_reply' AND NOT EXISTS
        (SELECT 1 FROM mail_processed p WHERE p.message_key=r.message_id)''').fetchall()
    for row in rows:
        # Existing completed assistant replies are independently sufficient.
        saved = db.execute('SELECT 1 FROM completions WHERE reply_id=? AND trim(final_text)<>\'\'',
                           (row['message_id'],)).fetchone()
        evidence = {'type': 'saved_reply', 'id': None} if saved else None
        cursor = None
        try:
            while evidence is None:
                params = {'threadId': row['thread_id'], 'turnId': row['turn_id'], 'limit': 100,
                          'sortDirection': 'asc'}
                if cursor:
                    params['cursor'] = cursor
                page = codex.request('thread/items/list', params)
                for entry in page['data']:
                    if entry.get('turnId') == row['turn_id'] and processing_evidence(entry['item']):
                        evidence = entry['item']
                        break
                cursor = page.get('nextCursor')
                if evidence or not cursor:
                    break
        except Exception:
            continue
        if evidence:
            with db:
                db.execute('INSERT OR IGNORE INTO mail_processed VALUES (?,?,?,?,?)',
                           (row['message_id'], row['turn_id'], evidence['type'], evidence.get('id'), time.time()))


def mark_processed(db, config, client, now=None):
    now = time.time() if now is None else now
    with db:
        db.execute('''INSERT OR IGNORE INTO mail_seen (folder,uidvalidity,uid,message_key,state)
            SELECT l.folder,l.uidvalidity,l.uid,l.message_key,'pending' FROM mail_locations l
            JOIN mail_processed p ON p.message_key=l.message_key
            JOIN mail_messages m ON m.message_key=l.message_key
            JOIN mail_folders f ON f.folder=l.folder AND f.uidvalidity=l.uidvalidity
            WHERE f.present=1 AND m.classification='confirmed_reply' AND m.identity_conflict=0''')
    rows = db.execute('''SELECT s.*,m.fingerprint FROM mail_seen s
        JOIN mail_messages m ON m.message_key=s.message_key
        JOIN mail_folders f ON f.folder=s.folder AND f.uidvalidity=s.uidvalidity
        WHERE s.state='pending' AND s.next_attempt<=? AND f.present=1
        AND m.classification='confirmed_reply' AND m.identity_conflict=0 LIMIT 20''', (now,)).fetchall()
    marked = 0
    for row in rows:
        state, error = 'pending', None
        try:
            status, _ = client.select(row['folder'], readonly=False)
            if status != 'OK':
                raise RuntimeError('Writable folder selection failed')
            _, response = client.response('UIDVALIDITY')
            if not response or int(response[0]) != row['uidvalidity']:
                raise RuntimeError('Seen UID validity changed')
            status, parts = client.uid('fetch', str(row['uid']), HEADER_FIELDS)
            blobs = [p for p in parts or [] if isinstance(p, tuple)]
            if status != 'OK':
                raise RuntimeError('Seen header verification failed')
            if not blobs:
                state = 'gone'
            elif len(blobs) != 1:
                raise RuntimeError('Unexpected Seen header response')
            else:
                envelope, raw = blobs[0]
                uid = re.search(rb'\bUID\s+(\d+)', envelope)
                identity = header_identity(BytesParser(policy=policy.default).parsebytes(raw), config.owner)
                if not uid or int(uid[1]) != row['uid'] or identity['fingerprint'] != row['fingerprint']:
                    raise RuntimeError('Seen identity mismatch')
                status, _ = client.uid('store', str(row['uid']), '+FLAGS.SILENT', '(\\Seen)')
                if status != 'OK':
                    raise RuntimeError('Seen flag write failed')
                # Confirm only flags, never fetch the body or clear other flags.
                status, flags = client.uid('fetch', str(row['uid']), '(UID FLAGS)')
                uid_pattern = rb'\bUID\s+' + str(row['uid']).encode() + rb'\b'
                flag_pattern = rb'FLAGS\s+\([^)]*\\Seen(?:\s|\))'
                if status != 'OK' or not any(re.search(uid_pattern,p,re.I) and re.search(flag_pattern,p,re.I) for p in flags or [] if isinstance(p,bytes)):
                    raise RuntimeError('Seen flag not confirmed')
                state, marked = 'done', marked + 1
        except Exception as exc:
            error = type(exc).__name__
        with db:
            db.execute('''UPDATE mail_seen SET state=?,next_attempt=?,last_error=?
                WHERE folder=? AND uidvalidity=? AND uid=?''',
                (state, now + 60, error, row['folder'], row['uidvalidity'], row['uid']))
    return marked


def diagnostics(db):
    return {'mail_seen_done': db.execute("SELECT count(*) FROM mail_seen WHERE state='done'").fetchone()[0],
            'mail_seen_pending': db.execute("SELECT count(*) FROM mail_seen WHERE state='pending'").fetchone()[0]}


def flush(db, config, mailbox_factory):
    # The flag queue is independent of Codex and does not repeat task submission.
    if not db.execute('SELECT 1 FROM mail_processed LIMIT 1').fetchone():
        return diagnostics(db)
    try:
        client = mailbox_factory(config)
        try:
            count = mark_processed(db, config, client)
        finally:
            client.logout()
        return {'mail_seen_marked': count, **diagnostics(db)}
    except Exception as exc:
        return {'mail_seen_error': type(exc).__name__, **diagnostics(db)}
