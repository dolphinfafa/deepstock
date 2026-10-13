"""Synthetic folder/cursor/routing cases: no live mail or Codex submissions."""
from email import policy
from email.parser import BytesParser
import json
from pathlib import Path
import sqlite3

import pytest

from deepstock import mail_discovery
from deepstock.session_mail import deliver_replies, poll_replies, receive_replies, reply_text, state_store
from test_session_mail import Codex, Mailbox, config, db, mail, prepare, thread


def record(db, mid='<owner-reply@example.test>'):
    return db.execute('SELECT * FROM mail_messages WHERE message_key=?', (mid,)).fetchone()


def test_history_batches_incremental_new_mail_and_restart(db, config):
    prepare(db, config)
    mailbox = Mailbox({b'1': mail(reply_to=None, message_id='<first@example.test>'),
                       b'2': mail(reply_to=None, message_id='<second@example.test>'), b'8': mail()})
    assert poll_replies(db, config, mailbox, batch_size=2) == 0
    assert mail_discovery.diagnostics(db)['mail_history_pending'] == 1
    assert db.execute('SELECT last_uid FROM mail_folders').fetchone()[0] == 2
    reopened = state_store(Path(db.execute('PRAGMA database_list').fetchone()[2]))
    try:
        assert poll_replies(reopened, config, mailbox, batch_size=2) == 1
        assert mail_discovery.diagnostics(reopened)['mail_history_pending'] == 0
        reads = list(mailbox.header_fetches)
        assert poll_replies(reopened, config, mailbox) == 0
        assert mailbox.header_fetches == reads
        mailbox.values[b'12'] = mail(message_id='<new@example.test>')
        assert poll_replies(reopened, config, mailbox) == 1
        searches = [args[2] for op,args in mailbox.calls if op == 'search']
        assert searches == ['1:8', '3:8', '9:12']
        assert mailbox.body_fetches == [b'8', b'12']
    finally:
        reopened.close()


def test_header_batch_transaction_rolls_back_before_cursor_advance(db, config, monkeypatch):
    prepare(db, config)
    mailbox = Mailbox({b'1': mail(message_id='<one@example.test>'), b'2': mail(message_id='<two@example.test>')})
    original = mail_discovery.save_header
    def interrupted(*args):
        if args[4] == 2:
            raise RuntimeError('Synthetic interruption')
        return original(*args)
    monkeypatch.setattr(mail_discovery, 'save_header', interrupted)
    assert poll_replies(db, config, mailbox) == 0
    assert db.execute('SELECT count(*) FROM mail_messages').fetchone()[0] == 0
    assert db.execute('SELECT last_uid FROM mail_folders').fetchone()[0] == 0
    monkeypatch.setattr(mail_discovery, 'save_header', original)
    assert poll_replies(db, config, mailbox) == 2
    assert mailbox.body_fetches == [b'1', b'2']


def test_all_folders_duplicates_and_move_do_not_repeat_body_or_submission(db, config):
    prepare(db, config)
    value = mail()
    folders = {'"INBOX"': {b'1': value}, '"Spam"': {b'3': value},
               '"Deleted Messages"': {b'9': value}, '"Archive"': {}}
    mailbox = Mailbox({}, folders=folders)
    assert poll_replies(db, config, mailbox) == 1
    assert len(mailbox.body_fetches) == 1
    assert db.execute('SELECT count(*) FROM mail_locations').fetchone()[0] == 3
    assert mail_discovery.diagnostics(db)['mail_folders'] == 4
    codex = Codex(config)
    assert deliver_replies(db, config, codex) == 1
    folders['"INBOX"'].clear()
    folders['"Archive"'][b'7'] = value
    assert poll_replies(db, config, mailbox) == 0
    assert len(mailbox.body_fetches) == 1
    assert deliver_replies(db, config, codex) == 0
    reopened = state_store(Path(db.execute('PRAGMA database_list').fetchone()[2]))
    try:
        assert poll_replies(reopened, config, mailbox) == 0
        assert deliver_replies(reopened, config, codex) == 0
    finally:
        reopened.close()


def test_uidvalidity_change_rescans_headers_but_deduplicates_bodies(db, config):
    prepare(db, config)
    mailbox = Mailbox({b'9': mail()})
    assert poll_replies(db, config, mailbox) == 1
    mailbox.validities['"INBOX"'] = 2
    mailbox.values.clear()
    mailbox.values.update({b'1': mail(), b'2': mail(message_id='<new@example.test>')})
    assert poll_replies(db, config, mailbox) == 1
    assert mailbox.body_fetches == [b'9', b'2']
    assert db.execute('SELECT uidvalidity,last_uid FROM mail_folders').fetchone()[:] == (2, 2)
    assert db.execute('SELECT count(*) FROM mail_locations WHERE uidvalidity=1').fetchone()[0] == 0


def test_out_of_order_reply_chain_waits_for_confirmed_parent(db, config):
    prepare(db, config)
    mailbox = Mailbox({b'1': mail(reply_to='<parent@example.test>', message_id='<child@example.test>'),
                       b'2': mail(message_id='<parent@example.test>')})
    assert poll_replies(db, config, mailbox, batch_size=1) == 0
    assert record(db, '<child@example.test>')['reason'] == 'parent_unconfirmed'
    assert mailbox.body_fetches == []
    assert poll_replies(db, config, mailbox, batch_size=1) == 2
    assert mailbox.body_fetches == [b'2', b'1']
    assert record(db, '<child@example.test>')['thread_id'] == 'thread-a'
    assert record(db, '<child@example.test>')['chain_depth'] == 2
    codex = Codex(config)
    assert deliver_replies(db, config, codex) == 1
    assert db.execute("SELECT message_id FROM replies WHERE state='delivered'").fetchone()[0] == '<parent@example.test>'


@pytest.mark.parametrize('direct,refs', [
    ('<deepstock-a@example.test> <deepstock-b@example.test>', None),
    ('<deepstock-a@example.test>', '<deepstock-b@example.test>'),
    (None, '<deepstock-a@example.test> <deepstock-b@example.test>'),
])
def test_different_session_associations_never_fetch_body(db, config, direct, refs):
    prepare(db, config)
    db.execute('INSERT INTO reminders VALUES (?,?,?,?,?)', ('<deepstock-b@example.test>', 'thread-b', 'turn-b', 'sent', 1))
    db.commit()
    mailbox = Mailbox({b'1': mail(reply_to=direct, refs=refs)})
    assert poll_replies(db, config, mailbox) == 0
    assert record(db)['reason'] == 'session_conflict'
    assert mailbox.body_fetches == []


def test_same_session_direct_parents_and_old_references_use_nearest_direct_parent(db, config):
    prepare(db, config)
    db.execute('INSERT INTO reminders VALUES (?,?,?,?,?)', ('<deepstock-b@example.test>', 'thread-a', 'turn-b', 'sent', 1))
    db.commit()
    mailbox = Mailbox({b'1': mail(reply_to='<deepstock-a@example.test> <deepstock-b@example.test>')})
    assert poll_replies(db, config, mailbox) == 1
    assert record(db)['parent_id'] == '<deepstock-b@example.test>'


@pytest.mark.parametrize('kwargs,reason', [
    ({'reply_to': None}, 'subject_only'),
    ({'reply_to': 'malformed', 'refs': '<deepstock-a@example.test>'}, 'parent_unconfirmed'),
    ({'reply_to': '<unknown@example.test>', 'refs': '<deepstock-a@example.test>'}, 'parent_unconfirmed'),
    ({'sender': 'other@example.test'}, 'sender_not_owner'),
    ({'sender': 'owner@example.test, other@example.test'}, 'sender_not_owner'),
])
def test_uncertain_or_other_agent_mail_is_header_only(db, config, kwargs, reason):
    prepare(db, config)
    mailbox = Mailbox({b'1': mail(**kwargs)})
    assert poll_replies(db, config, mailbox) == 0
    assert record(db)['reason'] == reason
    assert mailbox.body_fetches == []


def test_foreign_sender_cannot_extend_verified_chain(db, config):
    prepare(db, config)
    mailbox = Mailbox({b'1': mail(sender='agents@example.test', message_id='<foreign@example.test>'),
                       b'2': mail(reply_to='<foreign@example.test>', refs='<deepstock-a@example.test>')})
    assert poll_replies(db, config, mailbox) == 0
    assert mailbox.body_fetches == []
    assert record(db)['reason'] == 'parent_unconfirmed'


def test_duplicate_identity_with_conflicting_headers_is_quarantined(db, config):
    prepare(db, config)
    mailbox = Mailbox({}, folders={'"INBOX"': {b'1': mail()},
        '"Spam"': {b'2': mail(reply_to='<another-agent@example.test>')}})
    assert poll_replies(db, config, mailbox) == 0
    assert record(db)['reason'] == 'duplicate_identity_conflict'
    assert mailbox.body_fetches == []


def test_new_conflicting_copy_suspends_an_already_queued_reply(db, config):
    prepare(db, config)
    mailbox = Mailbox({b'1': mail()})
    assert poll_replies(db, config, mailbox) == 1
    mailbox.folders['"Spam"'] = {b'2': mail(reply_to='<another-agent@example.test>')}
    mailbox.validities['"Spam"'] = 1
    assert poll_replies(db, config, mailbox) == 0
    assert deliver_replies(db, config, Codex(config)) == 0
    assert db.execute('SELECT state FROM replies').fetchone()[0] == 'received'
    assert mailbox.body_fetches == [b'1']


def test_oversized_copy_is_not_downloaded_as_body_failure_fallback(db, config):
    prepare(db, config)
    mailbox = Mailbox({}, oversized=[b'2'], folders={'"INBOX"': {b'1': mail()}, '"Spam"': {b'2': mail()}})
    mailbox.fail_bodies.add(('"INBOX"', b'1'))
    assert poll_replies(db, config, mailbox) == 0
    assert mailbox.body_fetches == [b'1']
    assert record(db)['body_state'] == 'retry'


def test_submitting_claim_survives_restart_without_replay(db, config):
    prepare(db, config)
    assert poll_replies(db, config, Mailbox({b'1': mail()})) == 1
    db.execute("UPDATE replies SET state='submitting'")
    db.commit()
    reopened = state_store(Path(db.execute('PRAGMA database_list').fetchone()[2]))
    try:
        codex = Codex(config)
        assert deliver_replies(reopened, config, codex) == 0
        assert codex.calls == []
        assert mail_discovery.diagnostics(reopened)['replies_queued'] == 0
    finally:
        reopened.close()


def test_missing_identity_stays_pending(db, config):
    prepare(db, config)
    raw = mail().replace(b'Message-ID: <owner-reply@example.test>\n', b'')
    mailbox = Mailbox({b'1': raw})
    assert poll_replies(db, config, mailbox) == 0
    assert db.execute('SELECT reason FROM mail_messages').fetchone()[0] == 'missing_message_id'
    assert mailbox.body_fetches == []


def test_body_failure_keeps_cursor_and_retries_with_backoff(db, config):
    prepare(db, config)
    mailbox = Mailbox({b'1': mail()})
    mailbox.fail_bodies.add(('"INBOX"', b'1'))
    assert poll_replies(db, config, mailbox, now=1000) == 0
    assert record(db)['body_state'] == 'retry'
    assert db.execute('SELECT last_uid FROM mail_folders').fetchone()[0] == 1
    assert poll_replies(db, config, mailbox, now=1030) == 0
    assert mailbox.body_fetches == [b'1']
    mailbox.fail_bodies.clear()
    assert poll_replies(db, config, mailbox, now=1060) == 1
    assert poll_replies(db, config, mailbox, now=1090) == 0
    assert mailbox.body_fetches == [b'1', b'1']


def test_changed_body_headers_block_descendants_in_same_poll(db, config):
    prepare(db, config)
    class Changed(Mailbox):
        def uid(self, operation, *args):
            if operation == 'fetch' and 'HEADER.FIELDS' not in args[1] and args[0] == '1':
                return 'OK', [(b'1 (UID 1 BODY[] {100}', mail(sender='other@example.test>'))]
            return super().uid(operation, *args)
    mailbox = Changed({b'1': mail(), b'2': mail(reply_to='<owner-reply@example.test>', message_id='<child@example.test>')})
    assert poll_replies(db, config, mailbox) == 0
    assert record(db)['identity_conflict'] == 1
    assert record(db, '<child@example.test>')['reason'] == 'parent_unconfirmed'
    assert mailbox.body_fetches == []


def test_scan_error_in_one_folder_does_not_stop_other_folders(db, config):
    prepare(db, config)
    mailbox = Mailbox({}, folders={'"INBOX"': {b'1': mail(message_id='<one@example.test>')}, '"Spam"': {b'2': mail()}})
    mailbox.fail_headers.add(('"INBOX"', b'1'))
    assert poll_replies(db, config, mailbox) == 1
    assert mail_discovery.diagnostics(db)['mail_scan_errors'] == 1
    assert mailbox.body_fetches == [b'2']
    mailbox.fail_headers.clear()
    assert poll_replies(db, config, mailbox) == 1
    assert mail_discovery.diagnostics(db)['mail_scan_errors'] == 0


def test_folder_listing_skips_noselect_and_preserves_escaped_names():
    class Listing:
        def list(self):
            return 'OK', [b'(\\Noselect) "/" "Parent"', b'(\\HasNoChildren) "/" "Deleted Messages"',
                          b'() "/" "a\\"b"', (b'() "/" {7}', b'Archive')]
    assert mail_discovery.selectable_folders(Listing()) == ['"Deleted Messages"', '"a\\"b"', '"Archive"']


def test_migration_backs_up_legacy_dedup_records_and_never_downloads_again(tmp_path, config):
    path = tmp_path / 'legacy/state.sqlite3'
    legacy = state_store(path)
    for table in ('mail_messages', 'mail_locations', 'mail_folders', 'outbound_text'):
        legacy.execute(f'DROP TABLE {table}')
    legacy.execute('INSERT INTO reminders VALUES (?,?,?,?,?)', ('<deepstock-a@example.test>', 'thread-a', 'turn-a', 'sent', 1))
    for mid in ('<owner-reply@example.test>', '<second@example.test>'):
        legacy.execute('INSERT INTO replies VALUES (?,?,?,?,?,?,?)', (mid, '<deepstock-a@example.test>',
            'thread-a', 'already handled', 'adopted_in_current_turn', 1, 'stable-' + mid))
    legacy.commit()
    legacy.close()
    upgraded = state_store(path)
    try:
        backups = list(path.parent.glob('*.before-discovery-*.sqlite3'))
        assert len(backups) == 1
        with sqlite3.connect(backups[0]) as backup:
            assert backup.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert backup.execute('SELECT count(*) FROM replies').fetchone()[0] == 2
            assert not backup.execute("SELECT 1 FROM sqlite_master WHERE name='mail_messages'").fetchone()
        mailbox = Mailbox({b'1': mail(), b'2': mail(message_id='<second@example.test>')})
        assert poll_replies(upgraded, config, mailbox) == 0
        assert mailbox.body_fetches == []
        assert upgraded.execute('SELECT count(*) FROM replies').fetchone()[0] == 2
    finally:
        upgraded.close()


def test_bridge_receives_while_codex_unavailable_then_delivers_once(db, config, monkeypatch):
    import scripts.run_session_mail_bridge as bridge
    prepare(db, config)
    mailbox = Mailbox({b'1': mail()})
    monkeypatch.setenv('DEEPSTOCK_SESSION_EMAIL_ENABLED', 'true')
    monkeypatch.setenv('DEEPSTOCK_SCHEDULED_EMAIL_ENABLED', 'false')
    monkeypatch.setenv('DEEPSTOCK_CODEX_SOCKET', str(config.root / 'offline.sock'))
    monkeypatch.setattr(bridge.ContactConfig, 'load', lambda: config)
    monkeypatch.setattr(bridge, 'state_store', lambda _: db)
    monkeypatch.setattr(bridge, 'receive_replies', lambda *args: receive_replies(*args, mailbox_factory=lambda _: mailbox))
    monkeypatch.setattr(bridge.signal, 'signal', lambda *_: None)
    monkeypatch.setattr('sys.argv', ['bridge', '--once'])
    (config.root / 'artifacts/session-contact/private').mkdir(parents=True)
    assert bridge.main() == 1
    status = json.loads((config.root / 'artifacts/session-contact/private/status.json').read_text())
    assert status['replies_received'] == 1 and status['replies_queued'] == 1
    assert mailbox.logged_out
    assert bridge.main() == 1
    assert mailbox.body_fetches == [b'1']
    assert deliver_replies(db, config, Codex(config)) == 1
    assert deliver_replies(db, config, Codex(config)) == 0


@pytest.mark.parametrize('mode', ['offline', 'archived', 'active', 'unknown', 'resume_busy'])
def test_unavailable_session_preserves_queue_without_claim(db, config, mode):
    prepare(db, config)
    assert poll_replies(db, config, Mailbox({b'1': mail()})) == 1
    class Unavailable(Codex):
        def request(self, method, params):
            assert method != 'turn/start'
            if mode == 'offline':
                raise ConnectionError('Offline')
            if mode == 'archived':
                return {'thread': thread(config, archived=True)}
            value = 'notLoaded' if mode == 'resume_busy' and method == 'thread/read' else 'active' if mode == 'resume_busy' else mode
            return {'thread': thread(config, status={'type': value})}
    assert deliver_replies(db, config, Unavailable(config)) == 0
    assert db.execute('SELECT state FROM replies').fetchone()[0] == 'received'


@pytest.mark.parametrize('html', [False, True])
def test_only_known_quoted_content_removed_inline_and_uncertain_content_retained(html):
    message = BytesParser(policy=policy.default).parsebytes(mail(html=html))
    if html:
        message.set_content('<p>新增请求</p><blockquote><p>这是已知父邮件中的问题第一行。</p>'
                            '<p>这是已知父邮件中的问题第二行。</p></blockquote><p>行内回答</p>'
                            '<blockquote><p>无法确认的引用</p></blockquote>', subtype='html')
    else:
        message.set_content('新增请求\n> 这是已知父邮件中的问题第一行。\n> 这是已知父邮件中的问题第二行。\n'
                            '行内回答\n> 无法确认的引用\n不带引用标记的原文保留')
    result = reply_text(message, ['这是已知父邮件中的问题第一行。\n这是已知父邮件中的问题第二行。'])
    assert '已知父邮件' not in result
    assert all(value in result for value in ('新增请求', '行内回答', '无法确认的引用'))
    if not html:
        assert '不带引用标记的原文保留' in result
