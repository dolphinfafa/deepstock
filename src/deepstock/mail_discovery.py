"""Read-only, resumable IMAP discovery; all classification stays on the server."""
from __future__ import annotations

from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

MESSAGE_IDS = re.compile(r'<[^<>\s]+>')
MAX_MAIL_BYTES = 256 * 1024
HEADER_BATCH = 100
BODY_BATCH = 20
BODY_RETRY_SECONDS = 60
HEADER_FIELDS = '(UID RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM MESSAGE-ID IN-REPLY-TO REFERENCES SUBJECT)])'


def migrate(db, path: Path):
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='mail_messages'").fetchone():
        return
    # A private store upgrade must be recoverable before any schema mutation.
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='replies'").fetchone():
        backup = path.with_name(path.stem + '.before-discovery-' +
                                datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + path.suffix)
        backup.touch(mode=0o600, exist_ok=False)
        with sqlite3.connect(backup) as target:
            db.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Private mail backup integrity check failed')
    db.executescript('''BEGIN;
        CREATE TABLE mail_folders (folder TEXT PRIMARY KEY, uidvalidity INTEGER NOT NULL,
            last_uid INTEGER NOT NULL, history_until INTEGER NOT NULL, high_uid INTEGER NOT NULL,
            present INTEGER NOT NULL DEFAULT 1, last_error TEXT, scanned_at REAL);
        CREATE TABLE mail_messages (message_key TEXT PRIMARY KEY, message_id TEXT,
            owner_sender INTEGER NOT NULL, direct_ids TEXT NOT NULL, reference_ids TEXT NOT NULL,
            has_direct INTEGER NOT NULL, has_references INTEGER NOT NULL, subject_hint INTEGER NOT NULL,
            fingerprint TEXT, identity_conflict INTEGER NOT NULL DEFAULT 0,
            classification TEXT NOT NULL DEFAULT 'pending_confirmation', reason TEXT,
            parent_id TEXT, thread_id TEXT, turn_id TEXT, chain_depth INTEGER,
            body_state TEXT NOT NULL DEFAULT 'pending', next_attempt REAL NOT NULL DEFAULT 0,
            body_error TEXT, first_seen REAL NOT NULL);
        CREATE TABLE mail_locations (folder TEXT NOT NULL, uidvalidity INTEGER NOT NULL, uid INTEGER NOT NULL,
            message_key TEXT NOT NULL, size INTEGER NOT NULL, PRIMARY KEY(folder, uidvalidity, uid));
        CREATE INDEX mail_locations_message ON mail_locations(message_key);
        CREATE INDEX mail_messages_work ON mail_messages(classification, body_state, next_attempt);
        CREATE TABLE outbound_text (message_id TEXT PRIMARY KEY, body TEXT NOT NULL);
        COMMIT;
    ''')


def seed_processed(db):
    """Migrate old deduplication records without ever fetching their bodies again."""
    db.execute('''INSERT OR IGNORE INTO mail_messages
        (message_key,message_id,owner_sender,direct_ids,reference_ids,has_direct,has_references,
         subject_hint,classification,parent_id,thread_id,body_state,first_seen)
        SELECT message_id,message_id,1,json_array(reminder_id),'[]',1,0,0,
               'confirmed_reply',reminder_id,thread_id,'queued',received_at FROM replies''')
    db.commit()


def selectable_folders(client):
    status, entries = client.list()
    if status != 'OK':
        raise RuntimeError('Cannot list mail folders')
    folders = []
    for entry in entries or []:
        if entry is None:
            continue
        literal = None
        if isinstance(entry, tuple):
            entry, literal = entry
        match = re.fullmatch(rb'\(([^)]*)\)\s+(NIL|"(?:[^"\\]|\\.)*")\s+(.+)', entry)
        if not match:
            raise RuntimeError('Malformed mail folder listing')
        if b'\\noselect' in match[1].lower().split():
            continue
        name = match[3]
        if literal is not None:
            name = b'"' + literal.replace(b'\\', b'\\\\').replace(b'"', b'\\"') + b'"'
        if b'\r' in name or b'\n' in name:
            raise RuntimeError('Invalid mail folder name')
        # LIST's quoted/modified-UTF7 name is kept intact for SELECT/EXAMINE.
        folder = name.decode('ascii')
        if folder not in folders:
            folders.append(folder)
    return folders


def select_folder(client, folder):
    status, _ = client.select(folder, readonly=True)
    if status != 'OK':
        raise RuntimeError('Cannot select read-only mail folder')
    values = []
    for field in ('UIDVALIDITY', 'UIDNEXT'):
        _, data = client.response(field)
        if not data or not data[0] or not data[0].isdigit() or int(data[0]) < 1:
            raise RuntimeError('Mail folder has no valid UID cursor')
        values.append(int(data[0]))
    return values[0], values[1] - 1


def header_identity(header, owner):
    senders = getaddresses([str(v) for v in header.get_all('From', [])])
    owner_sender = len(senders) == 1 and senders[0][1].casefold() == owner.casefold()
    mids = header.get_all('Message-ID', [])
    mid = str(mids[0]).strip() if len(mids) == 1 else ''
    if not MESSAGE_IDS.fullmatch(mid):
        mid = None
    direct = MESSAGE_IDS.findall(' '.join(str(v) for v in header.get_all('In-Reply-To', [])))
    refs = MESSAGE_IDS.findall(' '.join(str(v) for v in header.get_all('References', [])))
    has_direct = 'In-Reply-To' in header
    has_refs = 'References' in header
    fingerprint = hashlib.sha256(json.dumps([mid, senders, direct, refs, has_direct, has_refs],
                                            ensure_ascii=True).encode()).hexdigest()
    return dict(message_id=mid, owner_sender=int(owner_sender), direct_ids=json.dumps(direct),
                reference_ids=json.dumps(refs), has_direct=int(has_direct), has_references=int(has_refs),
                subject_hint=int('deepstock' in str(header.get('Subject', '')).casefold()),
                fingerprint=fingerprint)


def save_header(db, config, folder, validity, uid, size, raw):
    fields = header_identity(BytesParser(policy=policy.default).parsebytes(raw), config.owner)
    key = fields['message_id'] or 'uidless-' + hashlib.sha256(f'{folder}:{validity}:{uid}'.encode()).hexdigest()
    old = db.execute('SELECT fingerprint FROM mail_messages WHERE message_key=?', (key,)).fetchone()
    if old and old['fingerprint'] and old['fingerprint'] != fields['fingerprint']:
        db.execute('UPDATE mail_messages SET identity_conflict=1 WHERE message_key=?', (key,))
    elif not old or old['fingerprint'] is None:
        db.execute('''INSERT INTO mail_messages
            (message_key,message_id,owner_sender,direct_ids,reference_ids,has_direct,has_references,
             subject_hint,fingerprint,first_seen) VALUES (:message_key,:message_id,:owner_sender,:direct_ids,
             :reference_ids,:has_direct,:has_references,:subject_hint,:fingerprint,:first_seen)
            ON CONFLICT(message_key) DO UPDATE SET fingerprint=excluded.fingerprint,
             owner_sender=excluded.owner_sender,direct_ids=excluded.direct_ids,reference_ids=excluded.reference_ids,
             has_direct=excluded.has_direct,has_references=excluded.has_references,subject_hint=excluded.subject_hint''',
            dict(fields, message_key=key, first_seen=time.time()))
    db.execute('INSERT OR REPLACE INTO mail_locations VALUES (?, ?, ?, ?, ?)', (folder, validity, uid, key, size))


def scan_folder(db, config, client, folder, batch_size):
    validity, upper = select_folder(client, folder)
    saved = db.execute('SELECT * FROM mail_folders WHERE folder=?', (folder,)).fetchone()
    with db:
        if not saved or saved['uidvalidity'] != validity:
            db.execute('DELETE FROM mail_locations WHERE folder=?', (folder,))
            db.execute('INSERT OR REPLACE INTO mail_folders VALUES (?, ?, 0, ?, ?, 1, NULL, NULL)',
                       (folder, validity, upper, upper))
        else:
            db.execute('UPDATE mail_folders SET present=1,high_uid=?,last_error=NULL WHERE folder=?', (upper, folder))
    last = db.execute('SELECT last_uid FROM mail_folders WHERE folder=?', (folder,)).fetchone()[0]
    if last >= upper:
        with db:
            db.execute('UPDATE mail_folders SET scanned_at=? WHERE folder=?', (time.time(), folder))
        return
    # Never use n:*: IMAP's reversed ranges can return an already-scanned last UID.
    status, result = client.uid('search', None, 'UID', f'{last + 1}:{upper}')
    if status != 'OK':
        raise RuntimeError('Incremental UID search failed')
    uids = sorted({int(v) for v in (result[0] or b'').split() if last < int(v) <= upper})
    batch = uids[:batch_size]
    pieces = []
    if batch:
        status, result = client.uid('fetch', ','.join(map(str, batch)), HEADER_FIELDS)
        if status != 'OK':
            raise RuntimeError('Incremental header fetch failed')
        pieces = [p for p in result or [] if isinstance(p, tuple)]
    parsed = []
    for envelope, raw in pieces:
        uid, size = re.search(rb'\bUID\s+(\d+)', envelope), re.search(rb'\bRFC822.SIZE\s+(\d+)', envelope)
        if not uid or not size or int(uid[1]) not in batch:
            raise RuntimeError('Unverifiable mail header UID or size')
        parsed.append((int(uid[1]), int(size[1]), raw))
    with db:
        for uid, size, raw in parsed:
            save_header(db, config, folder, validity, uid, size, raw)
        # Expunged UIDs absent from a successful FETCH can safely be skipped.
        next_uid = batch[-1] if len(uids) > len(batch) else upper
        db.execute('UPDATE mail_folders SET last_uid=?,scanned_at=? WHERE folder=?', (next_uid, time.time(), folder))


def classify(db):
    roots = {r['message_id']: (r['thread_id'], r['turn_id'], 0) for r in db.execute('''
        SELECT message_id,thread_id,turn_id FROM reminders
        UNION ALL SELECT message_id,thread_id,turn_id FROM completions WHERE state<>'ready'
        UNION ALL SELECT message_id,thread_id,NULL AS turn_id FROM scheduled_messages''')}
    rows = {r['message_key']: r for r in db.execute('SELECT * FROM mail_messages')}
    legacy = {r['message_id']: (r['thread_id'], None, 1) for r in db.execute('SELECT * FROM replies')
              if not rows.get(r['message_id']) or rows[r['message_id']]['fingerprint'] is None}
    resolved, reasons = {}, {}

    def resolve(mid, visiting=()):
        if mid in roots:
            return roots[mid]
        if mid in legacy:
            return legacy[mid]
        if mid in resolved:
            return resolved[mid]
        row = rows.get(mid)
        if row is None:
            return None
        if mid in visiting or len(visiting) >= 100:
            reasons[mid] = 'cyclic_or_deep_chain'
            return None
        if row['identity_conflict']:
            reasons[mid] = 'duplicate_identity_conflict'
            return None
        if not row['owner_sender'] or not row['message_id']:
            reasons[mid] = 'sender_not_owner' if not row['owner_sender'] else 'missing_message_id'
            return None
        direct, refs = json.loads(row['direct_ids']), json.loads(row['reference_ids'])
        links = {p: resolve(p, (*visiting, mid)) for p in dict.fromkeys([*direct, *refs])}
        if len({value[0] for value in links.values() if value}) > 1:
            reasons[mid] = 'session_conflict'
            return None
        parents = direct if row['has_direct'] else refs[-1:]
        if not parents or any(links.get(p) is None for p in parents):
            reasons[mid] = ('parent_unconfirmed' if row['has_direct'] or row['has_references'] else
                            'subject_only' if row['subject_hint'] else 'no_association')
            return None
        value = links[parents[-1]]
        resolved[mid] = value[0], value[1], value[2] + 1
        return resolved[mid]

    with db:
        for mid, row in rows.items():
            value = resolve(mid)
            if mid in roots:
                classification, reason, parent = 'outbound', None, None
            elif value:
                classification, reason = 'confirmed_reply', None
                parents = json.loads(row['direct_ids']) if row['has_direct'] else json.loads(row['reference_ids'])
                parent = parents[-1]
            else:
                classification = 'pending_confirmation' if row['owner_sender'] else 'other'
                reason, parent = reasons.get(mid, 'parent_unconfirmed'), None
            db.execute('''UPDATE mail_messages SET classification=?,reason=?,parent_id=?,thread_id=?,
                turn_id=?,chain_depth=? WHERE message_key=?''',
                (classification, reason, parent, value[0] if value else None,
                 value[1] if value else None, value[2] if value else None, mid))


def quoted_texts(db, row):
    texts, mid, seen = [], row['parent_id'], set()
    while mid and mid not in seen:
        seen.add(mid)
        saved = db.execute('SELECT body FROM outbound_text WHERE message_id=?', (mid,)).fetchone()
        reply = db.execute('SELECT body,reminder_id FROM replies WHERE message_id=?', (mid,)).fetchone()
        if saved:
            texts.append(saved[0])
        elif reply:
            texts.append(reply['body'])
        else:
            saved = db.execute('''SELECT final_text FROM completions WHERE message_id=?
                UNION ALL SELECT final_text FROM scheduled_messages WHERE message_id=?''', (mid, mid)).fetchone()
            if saved:
                texts.append(saved[0])
        parent = db.execute('SELECT parent_id FROM mail_messages WHERE message_key=?', (mid,)).fetchone()
        mid = reply['reminder_id'] if reply else parent[0] if parent else None
    return texts


def download_replies(db, config, client, text_reader, now):
    count, attempts = 0, 0
    rows = db.execute('''SELECT * FROM mail_messages WHERE classification='confirmed_reply'
        AND body_state IN ('pending','retry') AND next_attempt<=? ORDER BY chain_depth,first_seen''', (now,)).fetchall()
    for row in rows:
        if attempts >= BODY_BATCH:
            break
        if db.execute('SELECT classification FROM mail_messages WHERE message_key=?',
                      (row['message_key'],)).fetchone()[0] != 'confirmed_reply':
            continue
        if db.execute('SELECT 1 FROM replies WHERE message_id=?', (row['message_id'],)).fetchone():
            with db:
                db.execute("UPDATE mail_messages SET body_state='queued' WHERE message_key=?", (row['message_key'],))
            continue
        locations = db.execute('''SELECT l.* FROM mail_locations l JOIN mail_folders f ON f.folder=l.folder
            AND f.uidvalidity=l.uidvalidity WHERE f.present=1 AND l.message_key=? ORDER BY l.size,l.folder''',
            (row['message_key'],)).fetchall()
        if not locations:
            continue
        if locations[0]['size'] > MAX_MAIL_BYTES:
            with db:
                db.execute("UPDATE mail_messages SET body_state='rejected',body_error='oversized' WHERE message_key=?",
                           (row['message_key'],))
            continue
        attempts += 1
        error = 'body_unavailable'
        for location in locations:
            if location['size'] > MAX_MAIL_BYTES:
                continue
            try:
                validity, _ = select_folder(client, location['folder'])
                if validity != location['uidvalidity']:
                    raise RuntimeError('UID validity changed before body fetch')
                status, result = client.uid('fetch', str(location['uid']), '(UID BODY.PEEK[])')
                if status != 'OK':
                    raise RuntimeError('Confirmed body fetch failed')
                blobs = [p for p in result or [] if isinstance(p, tuple)]
                if not blobs:
                    continue
                if len(blobs) != 1:
                    raise RuntimeError('Unexpected body fetch response')
                envelope, raw = blobs[0]
                uid = re.search(rb'\bUID\s+(\d+)', envelope)
                if not uid or int(uid[1]) != location['uid']:
                    raise RuntimeError('Body fetch UID mismatch')
                if len(raw) > MAX_MAIL_BYTES:
                    with db:
                        db.execute("UPDATE mail_messages SET body_state='rejected',body_error='oversized' WHERE message_key=?",
                                   (row['message_key'],))
                    break
                message = BytesParser(policy=policy.default).parsebytes(raw)
                if header_identity(message, config.owner)['fingerprint'] != row['fingerprint']:
                    with db:
                        db.execute("UPDATE mail_messages SET identity_conflict=1,classification='pending_confirmation',"
                                   "reason='body_header_mismatch',body_state='rejected' WHERE message_key=?", (row['message_key'],))
                    classify(db)
                    break
                text = text_reader(message, quoted_texts(db, row))
                if not text or len(text) > 24000:
                    with db:
                        db.execute("UPDATE mail_messages SET body_state='rejected',body_error=? WHERE message_key=?",
                                   ('empty' if not text else 'text_too_large', row['message_key']))
                    break
                client_id = 'deepstock-email-' + hashlib.sha256(row['message_id'].encode()).hexdigest()
                with db:
                    inserted = db.execute('INSERT OR IGNORE INTO replies VALUES (?, ?, ?, ?, ?, ?, ?)',
                        (row['message_id'], row['parent_id'], row['thread_id'], text, 'received', now, client_id))
                    db.execute("UPDATE mail_messages SET body_state='queued',body_error=NULL WHERE message_key=?", (row['message_key'],))
                    db.execute("UPDATE reminders SET state='replied_by_email' WHERE message_id=?", (row['parent_id'],))
                    db.execute("UPDATE sessions SET status='answered' WHERE thread_id=? AND turn_id=? AND status<>'inactive'",
                               (row['thread_id'], row['turn_id']))
                count += inserted.rowcount
                break
            except Exception as exc:
                error = type(exc).__name__
        else:
            with db:
                db.execute("UPDATE mail_messages SET body_state='retry',next_attempt=?,body_error=? WHERE message_key=?",
                           (now + BODY_RETRY_SECONDS, error, row['message_key']))
    return count


def poll(db, config, client, text_reader, *, batch_size=HEADER_BATCH, now=None):
    now = time.time() if now is None else now
    seed_processed(db)
    folders = selectable_folders(client)
    with db:
        db.execute('UPDATE mail_folders SET present=0')
        for folder in folders:
            db.execute('UPDATE mail_folders SET present=1 WHERE folder=?', (folder,))
    for folder in folders:
        try:
            scan_folder(db, config, client, folder, batch_size)
        except Exception as exc:
            # Keep per-folder failures visible while other folders make progress.
            with db:
                db.execute('''INSERT INTO mail_folders (folder,uidvalidity,last_uid,history_until,high_uid,last_error)
                    VALUES (?,0,0,0,0,?) ON CONFLICT(folder) DO UPDATE SET last_error=excluded.last_error''',
                           (folder, type(exc).__name__))
    classify(db)
    return download_replies(db, config, client, text_reader, now)


def diagnostics(db):
    return {
        'reply_discovery': 'local_uid_scan',
        'mail_folders': db.execute('SELECT count(*) FROM mail_folders WHERE present=1').fetchone()[0],
        'mail_history_pending': db.execute('SELECT count(*) FROM mail_folders WHERE present=1 AND last_uid<history_until').fetchone()[0],
        'mail_scan_errors': db.execute('SELECT count(*) FROM mail_folders WHERE present=1 AND last_error IS NOT NULL').fetchone()[0],
        'mail_pending_confirmation': db.execute("SELECT count(*) FROM mail_messages WHERE classification='pending_confirmation'").fetchone()[0],
        'mail_body_retries': db.execute("SELECT count(*) FROM mail_messages WHERE classification='confirmed_reply' AND body_state='retry'").fetchone()[0],
        'replies_queued': db.execute("SELECT count(*) FROM replies WHERE state='received'").fetchone()[0],
    }
