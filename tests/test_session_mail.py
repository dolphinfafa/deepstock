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
                                    observe, poll_replies, reply_target, send_due, send_reminder, state_store)


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
    def __init__(self, values, searched=None, oversized=()):
        self.values = values
        self.searched = list(values) if searched is None else searched
        self.calls = []
        self.body_fetches = []
        self.oversized = oversized

    def uid(self, operation, *args):
        self.calls.append((operation, args))
        if operation == 'search':
            assert args[1:3] in (('HEADER', 'In-Reply-To'), ('HEADER', 'References'))
            assert args[3].startswith('"<deepstock-')
            return 'OK', [b' '.join(self.searched)]
        uid, fields = args
        assert 'BODY.PEEK' in fields
        raw = self.values[uid]
        if 'HEADER.FIELDS' in fields:
            header = raw.split(b'\n\n', 1)[0] + b'\n\n'
            size = 999999 if uid in self.oversized else len(raw)
            return 'OK', [(f'1 (RFC822.SIZE {size} BODY[HEADER] {{100}}'.encode(), header), b')']
        self.body_fetches.append(uid)
        return 'OK', [(b'1 (BODY[] {100}', raw), b')']


def test_shared_mailbox_only_matching_reply_body_read_and_deduplicated(db, config):
    prepare(db, config)
    mailbox = Mailbox({b'1': mail(reply_to='<another-agent@example.test>'),
                       b'2': mail(sender='other@example.test>'),
                       b'3': mail(), b'4': mail(reply_to=None)})
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


def test_imap_search_failure_has_no_broad_fallback(db, config):
    prepare(db, config)
    class Unsupported:
        def uid(self, operation, *args):
            assert operation == 'search'
            return 'BAD', [b'Unsupported search']
    with pytest.raises(RuntimeError, match='broad scan forbidden'):
        poll_replies(db, config, Unsupported())


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
