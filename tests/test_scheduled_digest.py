"""Digest boundaries, durable delivery and summaries use local synthetic receipts."""
from datetime import datetime, timedelta
import json
import sqlite3

import pytest

from deepstock import scheduled_digest as digest
from deepstock.scheduled_mail import enqueue, notify_pending, outbox
from deepstock.session_mail import send_contact, poll_replies
from test_scheduled_mail import config, db, event


def at(value):
    return datetime.fromisoformat(value + '+08:00')


def add(config, number, when='2026-11-04T09:00:00', *, arrived=None, **changes):
    finished = at(when)
    result = event(event_id=f'{number:032x}', started_at=finished-timedelta(seconds=5),
                   finished_at=finished, data_date=finished.date(), **changes)
    enqueue(config.root, result)
    with outbox(config.root) as q:
        q.execute('UPDATE receipt_arrivals SET received_at=? WHERE event_id=?',
                  (at(arrived or when).timestamp(), result.event_id))
    q.close()
    return result


def notify(db, config, when, calls):
    def sender(_config, session, mid, **kwargs):
        calls.append((session, mid, kwargs))
    return notify_pending(db, config, sender, thread_id='thread-a', scope='digest', now=at(when).timestamp())


def rows(config, table):
    with outbox(config.root) as q:
        result = [dict(r) for r in q.execute('SELECT * FROM ' + table)]
    q.close()
    return result


@pytest.mark.parametrize(('when', 'expected'), [
    ('2026-11-04T09:49:59', '2026-11-03T20:00:00'),
    ('2026-11-04T09:50:00', '2026-11-04T09:50:00'),
    ('2026-11-04T19:59:59', '2026-11-04T09:50:00'),
    ('2026-11-04T20:00:00', '2026-11-04T20:00:00'),
    ('2026-11-05T00:00:00', '2026-11-04T20:00:00'),
])
def test_shanghai_boundaries(when, expected):
    assert digest.latest_slot(at(when).timestamp()) == at(expected).timestamp()


def test_many_runs_one_digest_including_high_frequency_success(db, config):
    for i in range(100):
        blocked = i < 25
        result = add(config, i, task_id='auction-collect', stats={'rows_inserted': 1, 'identity_pending': i},
                     status='blocked' if blocked else 'completed',
                     reason='input_blocked' if blocked else 'completed')
        enqueue(config.root, result)  # Upload retry must not double-count.
    for i in range(100, 120):
        add(config, i, task_id='app-ingest', stats={})
    calls = []
    assert notify(db, config, '2026-11-04T09:49:59', calls) == 0
    assert notify(db, config, '2026-11-04T09:50:00', calls) == 1
    assert notify(db, config, '2026-11-04T09:50:30', calls) == 0
    body = calls[0][0]['final_text']
    assert len(calls) == 1 and '120次执行、2类任务' in body
    assert '本期新增行数：100' in body and '公告身份待核验数：99' in body
    assert '其后有成功执行' in body and '早报' in calls[0][2]['task_label']
    assert all(r['state'] == 'digest_sent' for r in rows(config, 'results'))


def test_empty_windows_and_exact_arrival_boundary_go_to_next_digest(db, config):
    calls = []
    assert notify(db, config, '2026-11-04T09:00:00', calls) == 0
    add(config, 1, '2026-11-04T09:50:00')
    assert notify(db, config, '2026-11-04T09:50:00', calls) == 0
    assert notify(db, config, '2026-11-04T19:59:59', calls) == 0
    assert notify(db, config, '2026-11-04T20:00:00', calls) == 1
    assert notify(db, config, '2026-11-05T09:50:00', calls) == 0
    assert '晚报' in calls[0][2]['task_label']


def test_late_quant_upload_uses_server_arrival_and_preserves_data_date(db, config):
    calls = []
    notify(db, config, '2026-11-04T09:00:00', calls)
    notify(db, config, '2026-11-04T09:50:00', calls)
    add(config, 1, '2026-11-03T08:00:00', arrived='2026-11-04T10:00:00',
        task_id='adx-shadow', node='quant', stats={'shadow_sessions': 8})
    assert notify(db, config, '2026-11-04T10:01:00', calls) == 0
    assert notify(db, config, '2026-11-04T20:00:00', calls) == 1
    assert '延迟上传' in calls[0][0]['final_text']
    assert '2026-11-03' in calls[0][0]['final_text']


def test_snapshot_metrics_never_sum_and_noop_does_not_clear_failure(db, config):
    for i in (1, 2):
        add(config, i, f'2026-11-04T09:0{i}:00', task_id='auction-minute-backfill', status='blocked',
            reason='waiting_for_provider_quota', stats={'completed_windows': 9, 'remaining_windows': 12, 'no_progress': True})
    add(config, 3, '2026-11-04T09:03:00', task_id='auction-minute-backfill', status='skipped', reason='already_recorded', stats={})
    add(config, 4, stats={'cumulative_return': -.0456, 'completed_cohorts': 21})
    add(config, 5, '2026-11-04T09:04:00', stats={'cumulative_return': -.03, 'completed_cohorts': 22})
    calls = []
    assert notify(db, config, '2026-11-04T09:50:00', calls) == 1
    body = calls[0][0]['final_text']
    assert '已补齐日期：9' in body and '已补齐日期：18' not in body
    assert '缺少日期：12' in body and '累计收益率：-3.00%' in body
    assert '供应商返回调用额度不足，2次' in body
    assert '待关注1项' in calls[0][2]['task_label']


def test_restart_before_contact_claim_keeps_frozen_batch(db, config):
    add(config, 1)
    now = at('2026-11-04T09:50:00').timestamp()
    with outbox(config.root) as q:
        batch = digest._prepare(q, db, config, 'thread-a', now)
    q.close()
    add(config, 2, '2026-11-04T09:50:01')
    calls = []
    assert notify(db, config, '2026-11-04T09:50:30', calls) == 1
    assert calls[0][0]['final_text'] == batch['final_text']
    assert notify(db, config, '2026-11-04T20:00:00', calls) == 1
    assert all('1次执行' in c[0]['final_text'] for c in calls)


def test_catchup_combines_missed_slots_and_unclaimed_preparation(db, config):
    add(config, 1)
    with outbox(config.root) as q:
        digest._prepare(q, db, config, 'thread-a', at('2026-11-04T09:50:00').timestamp())
    q.close()
    add(config, 2, '2026-11-04T19:00:00')
    add(config, 3, '2026-11-05T09:00:00')
    calls = []
    assert notify(db, config, '2026-11-05T10:00:00', calls) == 1
    assert notify(db, config, '2026-11-05T10:00:30', calls) == 0
    assert '3次执行' in calls[0][0]['final_text'] and '补发汇总' in calls[0][0]['final_text']
    assert {r['state'] for r in rows(config, 'digest_batches')} == {'superseded', 'sent'}
    assert sum(r['state'] == 'digest_sent' for r in rows(config, 'results')) == 3


def test_no_prepared_batch_downtime_also_sends_one_catchup(db, config):
    calls = []
    notify(db, config, '2026-11-03T19:00:00', calls)
    add(config, 1, '2026-11-03T19:30:00')
    add(config, 2, '2026-11-04T09:00:00')
    assert notify(db, config, '2026-11-05T10:00:00', calls) == 1
    assert notify(db, config, '2026-11-05T10:01:00', calls) == 0
    assert '2次执行' in calls[0][0]['final_text']


def test_smtp_ambiguity_never_retries_or_reassigns_members(db, config):
    add(config, 1)
    attempts = []
    def ambiguous(*args, **kwargs):
        attempts.append(args)
        raise TimeoutError('synthetic private SMTP response')
    assert notify_pending(db, config, ambiguous, thread_id='thread-a', scope='digest',
                          now=at('2026-11-04T09:50:00').timestamp()) == 0
    assert notify(db, config, '2026-11-04T09:51:00', []) == 0
    add(config, 2, '2026-11-04T12:00:00')
    calls = []
    assert notify(db, config, '2026-11-04T20:00:00', calls) == 1
    assert len(attempts) == 1 and '此前1封汇总的发送结果未确认' in calls[0][0]['final_text']
    assert '1次执行' in calls[0][0]['final_text'] and 'private' not in calls[0][0]['final_text']


def test_process_death_after_durable_claim_never_resends(db, config):
    add(config, 1)
    def die(*args, **kwargs):
        raise SystemExit('simulate process death during SMTP')
    with pytest.raises(SystemExit):
        notify_pending(db, config, die, thread_id='thread-a', scope='digest', now=at('2026-11-04T09:50:00').timestamp())
    assert db.execute('SELECT state FROM scheduled_messages').fetchone()[0] == 'sending'
    calls = []
    assert notify(db, config, '2026-11-04T09:51:00', calls) == 0
    assert calls == [] and rows(config, 'digest_batches')[0]['state'] == 'failed_or_ambiguous'
    assert db.execute('SELECT state FROM scheduled_messages').fetchone()[0] == 'failed_or_ambiguous'


def test_cross_database_crash_after_success_only_reconciles_ack(db, config, monkeypatch):
    add(config, 1)
    original = digest._finish
    def die(*args):
        raise RuntimeError('simulate lost queue acknowledgement')
    monkeypatch.setattr(digest, '_finish', die)
    calls = []
    with pytest.raises(RuntimeError):
        notify(db, config, '2026-11-04T09:50:00', calls)
    assert len(calls) == 1 and db.execute('SELECT state FROM scheduled_messages').fetchone()[0] == 'sent'
    monkeypatch.setattr(digest, '_finish', original)
    assert notify(db, config, '2026-11-04T09:51:00', calls) == 0
    assert len(calls) == 1 and rows(config, 'results')[0]['state'] == 'digest_sent'


def test_overlapping_pollers_do_not_send_twice_or_block_receipt_writer(db, config):
    add(config, 1)
    calls = []
    def overlapping(_config, session, mid, **kwargs):
        calls.append(mid)
        assert notify(db, config, '2026-11-04T09:50:00', []) == 0
        add(config, 2, '2026-11-04T09:50:01')  # Writer remains independent of SMTP lock.
    assert notify_pending(db, config, overlapping, thread_id='thread-a', scope='digest',
                          now=at('2026-11-04T09:50:00').timestamp()) == 1
    assert len(calls) == 1 and len(rows(config, 'results')) == 2


def test_legacy_database_migrates_without_resending_history(db, config):
    path = config.root / 'artifacts/scheduled-mail/private/outbox.sqlite3'
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as q:
        q.execute('CREATE TABLE results (event_id TEXT PRIMARY KEY,payload TEXT NOT NULL,state TEXT NOT NULL)')
        for i, state in enumerate(('sent', 'suppressed_success', 'pending', 'pending')):
            e = event(event_id=f'{i:032x}')
            q.execute('INSERT INTO results VALUES (?,?,?)', (e.event_id, e.model_dump_json(), state))
    db.execute('INSERT INTO scheduled_messages VALUES (?,?,?,?,?,?,?)',
               (f'{3:032x}', '<legacy@example.test>', 'thread-a', 'auction-report', 'old', 'sending', 0))
    db.commit()
    with outbox(config.root) as q:
        q.execute('UPDATE receipt_arrivals SET received_at=?', (at('2026-11-04T09:00:00').timestamp(),))
    q.close()
    calls = []
    assert notify(db, config, '2026-11-04T09:50:00', calls) == 1
    assert '1次执行' in calls[0][0]['final_text']
    assert {r['state'] for r in rows(config, 'results')} == {'sent', 'suppressed_success', 'digest_sent', 'previously_claimed'}


def test_preparation_failure_rolls_back_watermark_and_members(db, config, monkeypatch):
    add(config, 1)
    original = digest.summarize_digest
    def fail(*args, **kwargs):
        raise ValueError('synthetic render failure')
    monkeypatch.setattr(digest, 'summarize_digest', fail)
    with pytest.raises(ValueError):
        notify(db, config, '2026-11-04T09:50:00', [])
    assert rows(config, 'digest_batches') == [] and rows(config, 'results')[0]['state'] == 'pending'
    monkeypatch.setattr(digest, 'summarize_digest', original)
    assert notify(db, config, '2026-11-04T09:50:30', []) == 1


def test_duplicate_upload_does_not_move_first_arrival(config):
    e = add(config, 1)
    before = rows(config, 'receipt_arrivals')
    enqueue(config.root, e)
    assert rows(config, 'receipt_arrivals') == before


def test_rolling_restart_old_writer_receipt_acquires_arrival(config, monkeypatch):
    from deepstock import scheduled_mail
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at('2026-11-04T09:10:00').astimezone(tz)
    monkeypatch.setattr(scheduled_mail, 'datetime', Clock)
    with outbox(config.root) as q:
        e = event()
        q.execute('INSERT INTO results VALUES (?,?,?)', (e.event_id, e.model_dump_json(), 'pending'))
    q.close()
    arrivals = rows(config, 'receipt_arrivals')
    assert len(arrivals) == 1 and arrivals[0]['received_at'] == at('2026-11-04T09:10:00').timestamp()


def test_latest_measurement_does_not_carry_forward_stale_failure_flags(db, config):
    add(config, 1, task_id='auction-minute-backfill', status='blocked', reason='input_blocked',
        stats={'no_progress': True, 'completed_windows': 9})
    add(config, 2, '2026-11-04T09:01:00', task_id='auction-minute-backfill',
        stats={'completed_windows': 10, 'remaining_windows': 11})
    calls = []
    assert notify(db, config, '2026-11-04T09:50:00', calls) == 1
    body = calls[0][0]['final_text']
    assert '已补齐日期：10' in body and '本轮补数无进展：是' not in body
    assert '其后有成功执行' in body


def test_digest_bridge_sends_without_codex_connection(config, monkeypatch):
    import scripts.run_session_mail_bridge as bridge
    from deepstock.scheduled_mail import notify_pending as actual_notify
    add(config, 1)
    monkeypatch.setenv('DEEPSTOCK_SESSION_EMAIL_ENABLED', 'true')
    monkeypatch.setenv('DEEPSTOCK_SCHEDULED_EMAIL_ENABLED', 'true')
    monkeypatch.setenv('DEEPSTOCK_SCHEDULED_EMAIL_SCOPE', 'digest')
    monkeypatch.setenv('DEEPSTOCK_SCHEDULED_EMAIL_THREAD_ID', 'thread-a')
    monkeypatch.setenv('DEEPSTOCK_CODEX_SOCKET', str(config.root/'unavailable.sock'))
    monkeypatch.setattr(bridge.ContactConfig, 'load', lambda: config)
    monkeypatch.setattr(bridge.signal, 'signal', lambda *_: None)
    monkeypatch.setattr(bridge, 'receive_replies', lambda *_: {})
    monkeypatch.setattr(bridge, 'notify_pending', lambda *a, **kw: actual_notify(
        *a, **kw, now=at('2026-11-04T09:50:00').timestamp()))
    monkeypatch.setattr('sys.argv', ['bridge', '--once'])
    calls = []
    monkeypatch.setattr(bridge, 'send_contact', lambda *a, **kw: calls.append((a, kw)))
    assert bridge.main() == 1  # Session connection deferred; digest still sent.
    status = json.loads((config.root/'artifacts/session-contact/private/status.json').read_text())
    assert status['scheduled_sent'] == 1 and calls[0][0][1]['scheduled_digest'] is True


def test_digest_smtp_content_and_original_session_reply_route(db, config, monkeypatch):
    from test_session_mail import Mailbox, mail
    capture = {}
    class SMTP:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def login(self, *_): pass
        def send_message(self, message, **kwargs):
            capture.update(message=message, envelope=kwargs)
            return {}
    monkeypatch.setattr('deepstock.session_mail.smtplib.SMTP_SSL', SMTP)
    add(config, 1, task_id='auction-minute-backfill', stats={}, status='blocked', reason='input_blocked')
    assert notify_pending(db, config, send_contact, thread_id='thread-a', scope='digest',
                          now=at('2026-11-04T09:50:00').timestamp()) == 1
    m = capture['message']
    assert '定时任务早报' in str(m['Subject']) and '待关注1项' in str(m['Subject'])
    assert '具体原因待核查' in m.get_content() and '定时任务汇总如下' in m.get_content()
    assert capture['envelope']['to_addrs'] == ['owner@example.test']
    mailbox = Mailbox({b'1': mail(reply_to=m['Message-ID']), b'2': mail(reply_to='<other@example.test>', message_id='<other-reply@example.test>')})
    assert poll_replies(db, config, mailbox) == 1 and mailbox.body_fetches == [b'1']
    assert db.execute('SELECT thread_id FROM replies').fetchone()[0] == 'thread-a'
