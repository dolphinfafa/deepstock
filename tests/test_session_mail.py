"""No real SMTP, IMAP, OpenAI requests or Codex turns in these tests."""
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deepstock.session_mail import (ContactConfig, WAIT_SECONDS, deliver_replies, eligible_thread,
                                    observe, poll_replies, reply_target, register_reply_hint,
                                    search_token, send_completions, send_contact, send_due,
                                    send_reminder, state_store, synchronize)


@pytest.fixture
def config(tmp_path):
    return ContactConfig(tmp_path, 'owner@example.test', 'agents@example.test', 'imap.example.test', 993, 'ssl',
                         SimpleNamespace(smtp_host='smtp.example.test', smtp_port=465, smtp_security='ssl',
                                         smtp_username='agents@example.test', smtp_password='synthetic'),
                         'agents@example.test', 'synthetic')


@pytest.fixture
def db(tmp_path):
    store = state_store(tmp_path / 'private/state.sqlite3')
    store.execute("UPDATE settings SET value=1000 WHERE key='activated_at'")
    store.commit()
    yield store
    store.close()


def thread(config, **changes):
    return {'id': 'thread-a', 'cwd': str(config.root), 'source': 'vscode', 'status': {'type': 'idle'}, **changes}


def turn(**changes):
    return {'id': 'turn-a', 'status': 'completed', 'completedAt': 2000,
            'items': [{'type': 'agentMessage', 'phase': 'final', 'text': '请回复研究方向'}], **changes}


def prepare(db, config, state='sent', message_id='<deepstock-a@example.test>'):
    observe(db, thread(config), turn(), config.root)
    db.execute('INSERT INTO reminders VALUES (?, ?, ?, ?, ?)', (message_id, 'thread-a', 'turn-a', state, 4000))
    db.commit()


def mail(*, sender='owner@example.test', reply_to='<deepstock-a@example.test>',
         refs=None, message_id='<owner-reply@example.test>', html=False):
    value = EmailMessage()
    value['From'] = sender
    value['Message-ID'] = message_id
    value['Subject'] = 'Re: [deepstock] test'
    if reply_to:
        value['In-Reply-To'] = reply_to
    if refs:
        value['References'] = refs
    value.set_content('<p>邮件回复</p><script>ignored()</script>' if html else '邮件回复', subtype='html' if html else 'plain')
    return value.as_bytes(policy=policy.default)


def test_wait_boundary_and_single_send_persist_across_restart(db, config):
    observe(db, thread(config), turn(), config.root)
    calls = []
    sender = lambda *args: calls.append(args)
    assert send_due(db, config, sender, 2000 + WAIT_SECONDS - .001) == 0
    assert send_due(db, config, sender, 2000 + WAIT_SECONDS) == 1
    assert send_due(db, config, sender, 2000 + WAIT_SECONDS + 60) == 0
    db_path = Path(db.execute('PRAGMA database_list').fetchone()[2])
    again = state_store(db_path)
    try:
        assert send_due(again, config, sender, 999999) == 0
        assert again.execute("SELECT value FROM settings WHERE key='activated_at'").fetchone()[0] == 1000
    finally:
        again.close()
    assert len(calls) == 1


@pytest.mark.parametrize('phase', ['final', 'final_answer'])
def test_codex_completed_summary_final_phase_starts_timer(db, config, phase):
    observe(db, thread(config), turn(items=[
        {'type': 'userMessage', 'content': [{'type': 'text', 'text': '用户消息'}]},
        {'type': 'agentMessage', 'phase': phase, 'text': '等待用户回复'},
    ]), config.root)
    session = db.execute('SELECT * FROM sessions').fetchone()
    assert session['status'] == 'waiting'
    assert session['final_text'] == '等待用户回复'
    assert send_due(db, config, lambda *_: None, 2000 + WAIT_SECONDS) == 1


@pytest.mark.parametrize('change', [{'completedAt': 999}, {'status': 'inProgress'}, {'completedAt': None},
                                    {'items': []}, {'items': [{'type': 'agentMessage', 'phase': 'commentary', 'text': '进度'}]}])
def test_no_old_history_running_or_unrecognized_message_send(db, config, change):
    observe(db, thread(config), turn(**change), config.root)
    assert send_due(db, config, lambda *_: pytest.fail('Must not send'), 999999) == 0


@pytest.mark.parametrize('changes', [{'cwd': '/another/workspace'}, {'parentThreadId': 'parent'},
                                    {'agentRole': 'subagent'}, {'ephemeral': True}, {'source': 'exec'}])
def test_other_projects_and_subagents_excluded(db, config, changes):
    assert not eligible_thread(thread(config, **changes), config.root)
    observe(db, thread(config, **changes), turn(), config.root)
    assert db.execute('SELECT count(*) FROM sessions').fetchone()[0] == 0


def test_codex_reply_cancels_old_pending_timer(db, config):
    observe(db, thread(config), turn(), config.root)
    observe(db, thread(config), turn(id='new-user-turn', status='inProgress'), config.root)
    assert send_due(db, config, lambda *_: pytest.fail('Canceled'), 999999) == 0


@pytest.mark.parametrize('state', ['sending', 'failed_or_ambiguous'])
def test_crash_or_ambiguous_send_never_resubmitted(db, config, state):
    prepare(db, config, state=state)
    assert send_due(db, config, lambda *_: pytest.fail('Unsafe retry'), 999999) == 0


def test_send_exception_claim_is_durable(db, config):
    observe(db, thread(config), turn(), config.root)
    def failed(*_):
        raise TimeoutError('SMTP DATA outcome ambiguous')
    assert send_due(db, config, failed, 999999) == 0
    assert db.execute('SELECT state FROM reminders').fetchone()[0] == 'failed_or_ambiguous'
    assert send_due(db, config, lambda *_: pytest.fail('Unsafe retry'), 9999999) == 0


def test_from_display_reply_to_and_owner_only_smtp(monkeypatch, config):
    capture = {}
    class SMTP:
        def __init__(self, **kwargs): capture['connect'] = kwargs
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def login(self, *args): capture['login'] = args
        def send_message(self, value, **kwargs):
            capture.update(message=value, envelope=kwargs)
            return {}
    monkeypatch.setattr('deepstock.session_mail.smtplib.SMTP_SSL', SMTP)
    send_reminder(config, {'thread_id': 'thread-a', 'final_text': '待回复'}, '<deepstock-a@example.test>')
    assert capture['message']['From'] == 'deepstock <agents@example.test>'
    assert capture['message']['Reply-To'] == 'deepstock <agents@example.test>'
    assert capture['message']['Message-ID'] == '<deepstock-a@example.test>'
    assert capture['envelope']['to_addrs'] == ['owner@example.test']
    assert 'context' in capture['connect']


class Mailbox:
    def __init__(self, values, searched=None, oversized=(), folders=None):
        self.values = values
        self.calls = []
        self.body_fetches = []
        self.header_fetches = []
        self.oversized = oversized
        self.folders = folders or {'"INBOX"': values}
        self.validities = {name: 1 for name in self.folders}
        self.current = '"INBOX"'
        self.fail_headers = set()
        self.fail_bodies = set()
        self.logged_out = False

    def list(self):
        self.calls.append(('list', ()))
        return 'OK', [b'(\\HasNoChildren) "/" ' + name.encode() for name in self.folders]

    def select(self, folder, readonly=False):
        assert readonly
        assert folder in self.folders
        self.current = folder
        self.calls.append(('select', (folder, readonly)))
        return 'OK', [str(len(self.folders[folder])).encode()]

    def response(self, field):
        if field == 'UIDVALIDITY':
            return field, [str(self.validities[self.current]).encode()]
        assert field == 'UIDNEXT'
        return field, [str(max([int(v) for v in self.folders[self.current]] or [0]) + 1).encode()]

    def logout(self):
        self.logged_out = True

    def uid(self, operation, *args):
        self.calls.append((operation, args))
        values = self.folders[self.current]
        if operation == 'search':
            assert args[:2] == (None, 'UID')
            lower, upper = map(int, args[2].split(':'))
            return 'OK', [b' '.join(uid for uid in values if lower <= int(uid) <= upper)]
        assert operation == 'fetch'
        requested, fields = args
        assert 'BODY.PEEK' in fields and 'UID' in fields
        results = []
        for uid in requested.encode().split(b','):
            if uid not in values:
                continue
            raw = values[uid]
            if 'HEADER.FIELDS' in fields:
                if (self.current,uid) in self.fail_headers:
                    return 'NO', []
                self.header_fetches.append((self.current,uid))
                header = raw.split(b'\n\n', 1)[0] + b'\n\n'
                size = 999999 if uid in self.oversized else len(raw)
                results.append((f'1 (UID {int(uid)} RFC822.SIZE {size} BODY[HEADER] {{100}}'.encode(), header))
            else:
                self.body_fetches.append(uid)
                if (self.current,uid) in self.fail_bodies:
                    return 'NO', []
                results.append((f'1 (UID {int(uid)} BODY[] {{100}}'.encode(), raw))
            results.append(b')')
        return 'OK', results


def test_shared_mailbox_only_matching_reply_body_read_and_deduplicated(db, config):
    prepare(db, config)
    mailbox = Mailbox({b'1': mail(reply_to='<another-agent@example.test>', message_id='<other1@example.test>'),
                       b'2': mail(sender='other@example.test>', message_id='<other2@example.test>'),
                       b'3': mail(), b'4': mail(reply_to=None, message_id='<other4@example.test>')})
    assert poll_replies(db, config, mailbox) == 1
    assert mailbox.body_fetches == [b'3']
    assert poll_replies(db, config, mailbox) == 0
    assert mailbox.body_fetches == [b'3']
    assert db.execute('SELECT body FROM replies').fetchone()[0] == '邮件回复'
    assert db.execute('SELECT status FROM sessions').fetchone()[0] == 'answered'


@pytest.mark.parametrize('kind', ['references', 'html'])
def test_references_and_html_replies_work_without_html_execution(db, config, kind):
    prepare(db, config)
    value = mail(reply_to=None, refs='<other-agent@example.test> <deepstock-a@example.test>') if kind == 'references' else mail(html=True)
    mailbox = Mailbox({b'3': value})
    assert poll_replies(db, config, mailbox) == 1
    assert db.execute('SELECT body FROM replies').fetchone()[0] == '邮件回复'


def test_oversized_message_body_not_fetched(db, config):
    prepare(db, config)
    mailbox = Mailbox({b'3': mail()}, oversized=[b'3'])
    assert poll_replies(db, config, mailbox) == 0
    assert mailbox.body_fetches == []


def test_uid_search_failure_is_visible_without_advancing_cursor(db, config):
    prepare(db, config)
    class Unsupported(Mailbox):
        def uid(self, operation, *args):
            assert operation == 'search'
            return 'BAD', [b'Unsupported search']
    mailbox = Unsupported({b'3': mail()})
    assert poll_replies(db, config, mailbox) == 0
    saved = db.execute('SELECT last_uid,last_error FROM mail_folders').fetchone()
    assert saved['last_uid'] == 0 and saved['last_error'] == 'RuntimeError'
    assert mailbox.body_fetches == []


def test_direct_reply_has_priority_over_old_reference():
    header = BytesParser(policy=policy.default).parsebytes(mail(reply_to='<deepstock-new@example.test>', refs='<deepstock-a@example.test>'))
    assert reply_target(header, {'<deepstock-a@example.test>', '<deepstock-new@example.test>'}, 'owner@example.test') == '<deepstock-new@example.test>'


def test_multiple_from_addresses_are_rejected():
    header = BytesParser(policy=policy.default).parsebytes(mail(sender='owner@example.test, other@example.test'))
    assert reply_target(header, {'<deepstock-a@example.test>'}, 'owner@example.test') is None


@pytest.mark.parametrize('direct,refs', [('<other-agent@example.test>', '<deepstock-a@example.test>'),
                                         (None, '<deepstock-a@example.test> <other-agent@example.test>')])
def test_reply_to_another_agent_not_read_even_if_our_id_is_in_old_references(db, config, direct, refs):
    prepare(db, config)
    mailbox = Mailbox({b'1': mail(reply_to=direct, refs=refs)})
    assert poll_replies(db, config, mailbox) == 0
    assert mailbox.body_fetches == []


class Codex:
    def __init__(self, config, fail=False): self.config, self.fail, self.calls = config, fail, []
    def request(self, method, params):
        self.calls.append((method, params))
        if method == 'thread/read': return {'thread': thread(self.config)}
        if self.fail: raise TimeoutError('Ambiguous RPC submission')
        return {'turn': {'id': 'email-turn'}}


def test_reply_forwarded_to_original_session_once_with_no_permission_overrides(db, config):
    prepare(db, config)
    poll_replies(db, config, Mailbox({b'3': mail()}))
    codex = Codex(config)
    assert deliver_replies(db, config, codex) == 1
    assert deliver_replies(db, config, codex) == 0
    method, params = codex.calls[-1]
    assert method == 'turn/start' and params['threadId'] == 'thread-a'
    assert set(params) == {'threadId', 'clientUserMessageId', 'input', 'turnTrigger'}
    assert '邮件回复' in params['input'][0]['text']


def test_ambiguous_reply_forward_never_replayed(db, config):
    prepare(db, config)
    poll_replies(db, config, Mailbox({b'3': mail()}))
    codex = Codex(config, fail=True)
    assert deliver_replies(db, config, codex) == 0
    assert db.execute('SELECT state FROM replies').fetchone()[0] == 'submission_ambiguous'
    assert deliver_replies(db, config, codex) == 0
    assert len(codex.calls) == 2


def test_archived_session_not_resumed_by_email(db, config):
    prepare(db, config)
    poll_replies(db, config, Mailbox({b'3': mail()}))
    db.execute("UPDATE sessions SET status='inactive'"); db.commit()
    codex = Codex(config)
    assert deliver_replies(db, config, codex) == 0
    assert codex.calls == []


def test_history_recovers_legacy_reply_without_owner_hint_or_header_search(db, config):
    prepare(db, config)
    mailbox = Mailbox({b'3': mail()})
    assert poll_replies(db, config, mailbox) == 1
    assert poll_replies(db, config, mailbox) == 0
    assert mailbox.body_fetches == [b'3']
    assert all(args[1] == 'UID' for op,args in mailbox.calls if op == 'search')


def test_unknown_parent_and_search_injection_not_registered(db):
    with pytest.raises(ValueError, match='our own'):
        register_reply_hint(db, '<owner@example.test>', '<other-agent@example.test>')
    with pytest.raises(ValueError, match='Invalid'):
        register_reply_hint(db, '<owner"injection@example.test>', '<other-agent@example.test>')


@pytest.mark.parametrize('value,expected', [
    ({}, 1), ({'sender': 'other@example.test'}, 0),
    ({'reply_to': '<other-agent@example.test>', 'refs': '<deepstock-a@example.test>'}, 0),
])
def test_random_subject_candidates_still_require_owner_and_exact_nearest_parent(db, config, value, expected):
    prepare(db, config)
    mid = '<deepstock-a@example.test>'
    db.execute('INSERT INTO message_tokens VALUES (?, ?)', (mid, search_token(mid)))
    db.commit()
    mailbox = Mailbox({b'3': mail(**value)})
    assert poll_replies(db, config, mailbox) == expected
    assert mailbox.body_fetches == ([b'3'] if expected else [])
    assert all(args[1] == 'UID' for op, args in mailbox.calls if op == 'search')



def test_active_codex_turn_defers_reply_without_claim(db, config):
    prepare(db, config)
    poll_replies(db, config, Mailbox({b'3': mail()}))
    class Active(Codex):
        def request(self, method, params):
            assert method == 'thread/read'
            return {'thread': thread(self.config, status={'type': 'active'})}
    assert deliver_replies(db, config, Active(config)) == 0
    assert db.execute('SELECT state FROM replies').fetchone()[0] == 'received'


def ready_email_completion(db, config):
    prepare(db, config)
    poll_replies(db, config, Mailbox({b'3': mail()}))
    assert deliver_replies(db, config, Codex(config)) == 1
    class Completed(Codex):
        def request(self, method, params):
            if method == 'thread/list': return {'data': [thread(config)], 'nextCursor': None}
            assert method == 'thread/turns/list'
            # A subsequent Codex turn must not hide the completed email task.
            return {'data': [turn(id='codex-later'), turn(id='email-turn')]}
    synchronize(db, config, Completed(config))


def test_email_task_completion_sent_immediately_once_with_reply_headers(db, config):
    ready_email_completion(db, config)
    calls = []
    assert send_completions(db, config, lambda *args, **kwargs: calls.append((args, kwargs))) == 1
    assert send_completions(db, config, lambda *_: pytest.fail('Duplicate result')) == 0
    assert calls[0][1] == {'parent_id': '<owner-reply@example.test>', 'references': ['<deepstock-a@example.test>']}
    assert db.execute('SELECT state FROM completions').fetchone()[0] == 'sent'
    observe(db, thread(config), turn(id='email-turn'), config.root)
    assert send_due(db, config, lambda *_: pytest.fail('Email turn must not wait/send a reminder'), 999999) == 0


def test_ambiguous_completion_is_not_retried(db, config):
    ready_email_completion(db, config)
    def failed(*args, **kwargs): raise TimeoutError('Ambiguous SMTP DATA')
    assert send_completions(db, config, failed) == 0
    assert db.execute('SELECT state FROM completions').fetchone()[0] == 'failed_or_ambiguous'
    assert send_completions(db, config, lambda *_: pytest.fail('Unsafe resend')) == 0


def test_reply_to_our_completion_is_accepted(db, config):
    ready_email_completion(db, config)
    assert send_completions(db, config, lambda *args, **kwargs: None) == 1
    mid = db.execute('SELECT message_id FROM completions').fetchone()[0]
    value = mail(reply_to=mid, refs='<deepstock-a@example.test>', message_id='<owner-second@example.test>')
    mailbox = Mailbox({b'7': value})
    assert poll_replies(db, config, mailbox) == 1
    assert db.execute('SELECT count(*) FROM replies').fetchone()[0] == 2


def test_completion_formatter_keeps_ascii_token_and_standard_reply_headers(monkeypatch, config):
    sent = []
    class SMTP:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def login(self, *args): pass
        def send_message(self, value, **kwargs): sent.append(value); return {}
    monkeypatch.setattr('deepstock.session_mail.smtplib.SMTP_SSL', SMTP)
    mid = '<deepstock-completion@example.test>'
    send_contact(config, {'thread_id': 'thread-a', 'final_text': '任务完成'}, mid,
                 parent_id='<owner-reply@example.test>', references=['<deepstock-a@example.test>'])
    assert sent[0]['In-Reply-To'] == '<owner-reply@example.test>'
    assert sent[0]['References'] == '<deepstock-a@example.test> <owner-reply@example.test>'
    assert search_token(mid).encode() in sent[0].as_bytes().split(b'\n\n')[0]
    assert sent[0]['From'] == 'deepstock <agents@example.test>'



def test_completion_turn_found_beyond_latest_page(db, config):
    prepare(db, config)
    poll_replies(db, config, Mailbox({b'3': mail()}))
    assert deliver_replies(db, config, Codex(config)) == 1
    class Paged(Codex):
        def request(self, method, params):
            if method == 'thread/list': return {'data': [thread(config)], 'nextCursor': None}
            if params.get('cursor') == 'older':
                return {'data': [turn(id='email-turn')], 'nextCursor': None}
            return {'data': [turn(id='newer-turn')], 'nextCursor': 'older'}
    synchronize(db, config, Paged(config))
    assert db.execute('SELECT turn_id FROM completions').fetchone()[0] == 'email-turn'
    assert db.execute('SELECT turn_id FROM sessions').fetchone()[0] == 'newer-turn'
