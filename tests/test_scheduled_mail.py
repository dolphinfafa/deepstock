"""Scheduled receipt and SMTP tests use synthetic local tasks; no providers/orders."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from deepstock.scheduled_mail import TaskResult, enqueue, notify_pending, outbox, publish_pending, summarize
from deepstock.session_mail import ContactConfig, poll_replies, register_reply_hint, send_contact, state_store
from scripts.run_scheduled_task import command, last_object, receipt, run
from scripts.install_short_term_forward_cron import cron_block
from scripts.install_csi300_research_cron import backfill_block
from scripts.install_deepstock_app_cron import build_block

NOW = datetime(2026, 10, 13, 1, tzinfo=timezone.utc)


def event(**changes):
    values = dict(event_id='a' * 32, task_id='auction-report', node='server',
        started_at=NOW, finished_at=NOW + timedelta(seconds=10), exit_code=0,
        status='completed', reason='completed', data_date='2026-10-12',
        stats={'completed_cohorts': 21, 'cumulative_return': -0.0456})
    return TaskResult(**{**values, **changes})


@pytest.fixture
def config(tmp_path):
    return ContactConfig(tmp_path, 'owner@example.test', 'agents@example.test', 'imap.example.test', 993,
        'ssl', SimpleNamespace(smtp_host='smtp.example.test', smtp_port=465, smtp_security='ssl',
        smtp_username='agents@example.test', smtp_password='synthetic'), 'agents@example.test', 'synthetic')


@pytest.fixture
def db(tmp_path):
    store = state_store(tmp_path / 'contact/private/state.sqlite3')
    yield store
    store.close()


def test_restart_duplicates_activation_scope_and_ambiguous_smtp(db, config):
    old = event(event_id='0' * 32, started_at=NOW-timedelta(days=1), finished_at=NOW-timedelta(hours=1))
    quiet = event(event_id='1' * 32, task_id='auction-collect')
    failed = event(event_id='2' * 32, task_id='app-ingest', exit_code=1, status='failed', reason='process_failed')
    blocked = event(event_id='3' * 32, task_id='auction-minute-backfill', status='blocked', reason='waiting_for_quota')
    for result in (old, quiet, failed, blocked):
        enqueue(config.root, result)
        enqueue(config.root, result)
    calls = []
    def sender(_config, session, mid, **kwargs):
        calls.append((session, mid, kwargs))
        if kwargs['task_label'] == '研究状态导入':
            raise TimeoutError('Ambiguous SMTP DATA')
    assert notify_pending(db, config, sender, thread_id='thread-a', now=NOW.timestamp()) == 1
    assert len(calls) == 2
    assert {r[0] for r in db.execute('SELECT state FROM scheduled_messages')} == {'sent', 'failed_or_ambiguous'}
    assert notify_pending(db, config, lambda *_a, **_k: pytest.fail('Never resend'), thread_id='thread-a', now=NOW.timestamp()) == 0
    # Simulate a crash after the contact claim but before the queue was acknowledged.
    with outbox(config.root) as queue:
        queue.execute("UPDATE results SET state='pending' WHERE event_id=?", (failed.event_id,))
    queue.close()
    assert notify_pending(db, config, lambda *_a, **_k: pytest.fail('Never resend'), thread_id='thread-a', now=NOW.timestamp()) == 0
    assert db.execute('SELECT COUNT(*) FROM scheduled_messages').fetchone()[0] == 2


def test_all_scope_sends_high_frequency_success_separately(db, config):
    enqueue(config.root, event(task_id='app-ingest'))
    calls=[]
    assert notify_pending(db, config, lambda *a, **kw: calls.append(kw), thread_id='thread-a', scope='all', now=NOW.timestamp()) == 1
    assert calls == [{'task_label': '研究状态导入'}]


@pytest.mark.parametrize('changes', [
    {'stats': {'raw_prices': 1}}, {'stats': {'cumulative_return': float('nan')}},
    {'stats': {'pending': 'secret'}}, {'attachment': 'private'}, {'node': 'quant'},
    {'exit_code': 1}, {'finished_at': NOW-timedelta(seconds=1)}, {'started_at': NOW.replace(tzinfo=None)},
])
def test_receipt_rejects_unapproved_data_and_false_success(changes):
    with pytest.raises(ValidationError):
        event(**changes)


def test_payload_identity_cannot_be_overwritten(tmp_path):
    enqueue(tmp_path, event())
    with pytest.raises(ValueError, match='overwritten'):
        enqueue(tmp_path, event(stats={'completed_cohorts': 22}))


def test_node_publication_retries_only_receipt_and_deduplicates(tmp_path):
    result = event(task_id='defensive-observation', node='quant')
    enqueue(tmp_path, result)
    calls=[]
    class Client:
        @staticmethod
        def post(url, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(raise_for_status=lambda: None)
    source=SimpleNamespace(node_token='synthetic-token', public_base_url='https://example.test/deepstock')
    assert publish_pending(tmp_path, source, Client) == 1
    assert publish_pending(tmp_path, source, Client) == 0
    assert calls[0]['json'] == result.model_dump(mode='json')
    assert 'smtp' not in str(calls[0])


def test_real_local_fixture_invoked_once_and_exit_retained(tmp_path, monkeypatch, capfd):
    (tmp_path/'fixture_task.py').write_text('import json,sys\nprint(json.dumps({"status":"ok","secret":"private"}))\nsys.exit(7)\n')
    monkeypatch.setitem(__import__('scripts.run_scheduled_task', fromlist=['MODULES']).MODULES,
                        'database-backup', ('fixture_task', []))
    assert run('database-backup', [], tmp_path) == 7
    assert 'private' in capfd.readouterr().out  # Original local stdout remains intact.
    queue=outbox(tmp_path)
    try:
        rows=queue.execute('SELECT payload FROM results').fetchall()
        assert len(rows)==1 and 'private' not in rows[0][0] and 'secret' not in rows[0][0]
        result=TaskResult.model_validate_json(rows[0][0])
        assert result.status=='failed' and result.exit_code==7
    finally: queue.close()


def test_original_commands_args_and_schedule_times_remain(monkeypatch, tmp_path):
    import sys
    assert command('auction-fill', ['--wait-seconds', '90'], tmp_path) == [
        sys.executable, '-m', 'scripts.short_term_forward', 'fill', '--wait-seconds', '90']
    assert command('auction-minute-backfill', [], tmp_path)[2:]==['scripts.run_csi300_minute_backfill']
    assert command('auction-settle-counterfactual', [], tmp_path)[2:]==['scripts.short_term_forward','settle-counterfactual']
    forward=cron_block(tmp_path, Path('/env/python'))
    assert [' '.join(line.split()[:5]) for line in forward.splitlines() if line and not line.startswith(('#','CRON_TZ'))] == [
        '*/10 0-8 * * 1-5', '0-20/5 9 * * 1-5', '24 9 * * 1-5',
        '*/10 15-23 * * 1-5', '*/30 * * * 0,6', '27,29 9 * * 1-5',
        '31 9 * * 1-5', '33,38,43 9 * * 1-5', '0 10 * * 1-5',
        '20 15 * * 1-5', '5 16,18 * * 1-5', '15 16,18 * * 1-5', '10 18 * * 1-5']
    assert '30 11 * * *' in backfill_block(tmp_path, Path('/env/python'))
    assert '35 12 * * *' in backfill_block(tmp_path, Path('/env/python'))
    assert '*/5 * * * *' in build_block(tmp_path, Path('/env/python'))
    assert '10 3 * * *' in build_block(tmp_path, Path('/env/python'))


def test_only_last_json_summary_used_and_quota_or_cutoff_stays_blocked():
    assert last_object('log\n{"status":"old"}\n{"status":"waiting_for_quota","remaining_windows":12}\n')['remaining_windows']==12
    result=receipt('auction-minute-backfill',NOW,NOW,0,{'status':'waiting_for_quota','remaining_windows':12,'error':'private'})
    assert result.status=='blocked' and 'private' not in summarize(result)
    assert receipt('auction-signal',NOW,NOW,0,{'status':'missed_decision_cutoff'}).status=='blocked'


def test_scheduled_email_separate_subject_and_owner_only_smtp(monkeypatch, config):
    capture={}
    class SMTP:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def login(self, *_): pass
        def send_message(self, message, **kwargs):
            capture.update(message=message, envelope=kwargs);return {}
    monkeypatch.setattr('deepstock.session_mail.smtplib.SMTP_SSL',SMTP)
    send_contact(config,{'thread_id':'thread-a','final_text':summarize(event())},'<deepstock-scheduled@example.test>',task_label='集合竞价日报')
    message=capture['message']
    assert message['From']=='deepstock <agents@example.test>'
    assert '定时任务结果' in str(message['Subject']) and 'In-Reply-To' not in message
    assert capture['envelope']['to_addrs']==['owner@example.test']
    assert '-4.56%' in message.get_content()


def test_scheduled_mail_replies_use_existing_owner_parent_filter(db, config):
    from test_session_mail import Mailbox, mail
    mid='<deepstock-scheduled@example.test>'
    db.execute('INSERT INTO scheduled_messages VALUES (?,?,?,?,?,?,?)',
        ('a'*32,mid,'thread-a','auction-report','aggregate','sent',NOW.timestamp()));db.commit()
    mailbox=Mailbox({b'1':mail(reply_to=mid), b'2':mail(reply_to='<another-agent@example.test>')})
    assert poll_replies(db,config,mailbox)==1
    assert mailbox.body_fetches==[b'1']
    register_reply_hint(db,'<owner-exact@example.test>',mid)


def test_verified_hint_exposes_provider_search_false_negative(db,config):
    from test_session_mail import Mailbox, mail, prepare
    prepare(db,config)
    register_reply_hint(db,'<owner-reply@example.test>','<deepstock-a@example.test>')
    class BrokenSearch(Mailbox):
        def uid(self, operation, *args):
            if operation=='search' and args[2]!='Message-ID': return 'OK',[b'']
            return super().uid(operation,*args)
    assert poll_replies(db,config,BrokenSearch({b'1':mail()}))==1
    assert db.execute("SELECT value FROM settings WHERE key='reply_search_false_negative'").fetchone()[0]==1


def test_bridge_scheduled_mail_runs_while_codex_unavailable(config,monkeypatch):
    import scripts.run_session_mail_bridge as bridge
    now=datetime.now(timezone.utc)+timedelta(seconds=1)
    enqueue(config.root,event(started_at=now,finished_at=now))
    monkeypatch.setenv('DEEPSTOCK_SESSION_EMAIL_ENABLED','true')
    monkeypatch.setenv('DEEPSTOCK_SCHEDULED_EMAIL_ENABLED','true')
    monkeypatch.setenv('DEEPSTOCK_SCHEDULED_EMAIL_THREAD_ID','thread-a')
    monkeypatch.setenv('DEEPSTOCK_SCHEDULED_EMAIL_SCOPE','results')
    monkeypatch.setenv('DEEPSTOCK_CODEX_SOCKET',str(config.root/'unavailable.sock'))
    monkeypatch.setattr(bridge.ContactConfig,'load',lambda:config)
    monkeypatch.setattr(bridge.signal,'signal',lambda *_:None)
    monkeypatch.setattr('sys.argv',['bridge','--once'])
    calls=[]
    monkeypatch.setattr(bridge,'send_contact',lambda *a,**k:calls.append(k))
    assert bridge.main()==1  # Session routing deferred, scheduled email completed.
    status=json.loads((config.root/'artifacts/session-contact/private/status.json').read_text())
    assert len(calls)==1 and status['scheduled_sent']==1 and status['status']=='deferred'
