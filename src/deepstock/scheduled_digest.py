"""Twice-daily, durable scheduled-result digests; no model or provider calls."""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
from email.utils import make_msgid
import os
from uuid import uuid4
from zoneinfo import ZoneInfo

from deepstock.scheduled_mail import STAT_LABELS, TASKS, TaskResult, outbox

LOCAL = ZoneInfo('Asia/Shanghai')
SLOTS = (time(9, 50), time(20))
STATUS = {'completed': '完成', 'failed': '失败', 'blocked': '阻塞', 'skipped': '正常跳过'}
REASONS = {
    'process_failed': '程序执行失败，具体原因待核查',
    'waiting_for_quota': '本地调用额度保护，等待下一次允许调用',
    'waiting_for_provider_quota': '供应商返回调用额度不足',
    'fetch_failed': '补数请求失败，具体原因待核查',
    'finalize_failed': '完整报告生成失败',
    'missed_decision_cutoff': '错过正式决策截止时间，不能补作事前计划',
    'result_unavailable': '结果摘要缺失或无法核对',
    'input_blocked': '研究输入完整性检查未通过，具体原因待核查',
    'settlement_blocked': '事前计划结算缺少报价',
    'already_recorded': '已有记录', 'nothing_pending': '无需处理',
    'skipped_locked': '已有任务运行', 'skipped_non_trading_day': '非交易日',
}
SECTIONS = (
    ('公告采集', ('auction-collect',)),
    ('集合竞价', ('auction-signal', 'auction-fill', 'auction-settle',
                  'auction-settle-counterfactual', 'auction-report')),
    ('历史分钟补数', ('auction-minute-backfill',)),
    ('ADX', ('adx-shadow',)), ('葛兰威尔', ('granville-shadow',)),
    ('防御型ETF', ('defensive-observation',)),
    ('系统维护', ('app-ingest', 'database-backup')),
)
SUM_FIELDS = {'auction-collect': ('rows_inserted', 'identity_recovered'),
              'auction-fill': ('filled',), 'auction-settle': ('closed',),
              'auction-settle-counterfactual': ('closed',)}
LAST_FIELDS = {
    'auction-collect': ('identity_pending',),
    'auction-minute-backfill': ('completed_windows', 'remaining_windows', 'no_progress', 'frozen_report_ready'),
    'auction-signal': ('actionable', 'health_passed'),
    'auction-fill': ('missing_price', 'missed_capacity'),
    'auction-settle': ('still_open', 'blocked_observations'),
    'auction-settle-counterfactual': ('still_open', 'blocked_observations'),
    'auction-report': ('completed_cohorts', 'cumulative_return', 'mean_daily_return'),
    'adx-shadow': ('shadow_sessions', 'coverage_failures'),
    'granville-shadow': ('shadow_sessions', 'coverage_failures'),
    'database-backup': ('integrity_ok',),
}


@contextmanager
def _delivery_lock(root):
    """OS releases this lock on death; SQLite receipt writers remain independent."""
    path = root / 'artifacts/scheduled-mail/private/digest.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            if os.name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def latest_slot(now):
    local = datetime.fromtimestamp(now, LOCAL)
    candidates = [datetime.combine(local.date(), t, LOCAL) for t in SLOTS]
    return max(t.timestamp() for t in candidates if t <= local) if local.time() >= SLOTS[0] else (
        datetime.combine(local.date() - timedelta(days=1), SLOTS[-1], LOCAL).timestamp())


def _effective(rows):
    # An idempotent/no-work retry cannot clear an earlier actual failure.
    return next((r for r in reversed(rows) if r.status != 'skipped'), rows[-1])


def _value(key, value):
    if key.endswith('return') or key == 'daily_win_rate':
        return f'{value:.2%}'
    if isinstance(value, bool):
        return '是' if value else '否'
    return str(value)


def _task_text(rows):
    last = _effective(rows)
    counts = Counter(r.status for r in rows)
    parts = [f"执行{len(rows)}次（" + '、'.join(f'{STATUS[k]}{counts[k]}次' for k in STATUS if counts[k]) + '）',
             f'最新有效状态：{STATUS[last.status]}']
    anomalies = [r for r in rows if r.status in ('failed', 'blocked')]
    if anomalies:
        reasons = Counter(r.reason for r in anomalies)
        parts.append('本期异常：' + '；'.join(f'{REASONS.get(k, "具体原因待核查")}，{v}次' for k, v in sorted(reasons.items())))
        codes = sorted({r.exit_code for r in anomalies if r.exit_code})
        if codes:
            parts.append('失败退出码：' + '、'.join(map(str, codes)))
        if last.status == 'completed':
            parts.append('其后有成功执行，本期异常记录仍保留')
    elif counts['skipped']:
        reasons = Counter(r.reason for r in rows if r.status == 'skipped')
        parts.append('跳过原因：' + '、'.join(REASONS.get(k, '无需处理') for k in sorted(reasons)))
    for key in SUM_FIELDS.get(last.task_id, ()):
        values = [r.stats[key] for r in rows if type(r.stats.get(key)) in (int, float)]
        if values:
            label = '恢复身份数' if key == 'identity_recovered' else STAT_LABELS[key]
            parts.append(f'本期{label}：{sum(values)}')
    fields = LAST_FIELDS.get(last.task_id, ())
    measured = next((r for r in reversed(rows) if any(r.stats.get(k) is not None for k in fields)), None)
    metrics = [f'{STAT_LABELS[key]}：{_value(key, measured.stats[key])}' for key in fields
               if measured is not None and measured.stats.get(key) is not None]
    if metrics:
        stamp = f'（数据日{measured.data_date}）' if measured.data_date else ''
        parts.append(f'最近记录{stamp}：' + '，'.join(metrics))
    if last.data_date and (measured is None or measured.data_date != last.data_date):
        parts.append(f'最新有效数据日：{last.data_date}')
    return '；'.join(parts) + '。'


def summarize_digest(events, *, start, end, created, unconfirmed=0):
    """Use only validated aggregate receipts; snapshot counters are never summed."""
    unique = {event.event_id: event for event in events}
    grouped = defaultdict(list)
    for event in sorted(unique.values(), key=lambda r: (r.finished_at, r.event_id)):
        grouped[event.task_id, event.node].append(event)
    unresolved = [rows for rows in grouped.values() if _effective(rows).status in ('failed', 'blocked')]
    attention = len(unresolved) + bool(unconfirmed)
    due = datetime.fromtimestamp(end, LOCAL)
    label = f'{"早报" if due.time() == SLOTS[0] else "晚报"} · {due:%Y-%m-%d} · 待关注{attention}项'
    span = f'{datetime.fromtimestamp(start, LOCAL):%m-%d %H:%M} 至 {due:%m-%d %H:%M}（北京时间）'
    counts = Counter(event.status for event in unique.values())
    lines = [f'汇总范围：{span}',
             f'本期共{len(unique)}次执行、{len(grouped)}类任务；' +
             '，'.join(f'{STATUS[k]}{counts[k]}次' for k in STATUS if counts[k]) + '。',
             f'最新有效状态仍有{len(unresolved)}类任务需要关注。' if unresolved else '本期各任务最新有效状态均无失败或阻塞。']
    if created > end + 60:
        lines.append('本封为补发汇总；错过的发送时段已合并，不逐封补发。')
    delayed = sum(r.finished_at.timestamp() < start for r in unique.values())
    if delayed:
        lines.append(f'含{delayed}次延迟上传或切换前待发送记录，实际数据日期按各任务注明。')
    if unconfirmed:
        lines.append(f'此前{unconfirmed}封汇总的发送结果未确认，未自动重发；需核查邮件服务记录。')
    for section, tasks in SECTIONS:
        entries = [(task, node) for task in tasks for node in ('server', 'quant') if (task, node) in grouped]
        if entries:
            lines.extend(['', section])
            for task, node in entries:
                lines.append(f'{TASKS[task]}（{"服务器" if node == "server" else "量化电脑"}）：' + _task_text(grouped[task, node]))
    actions = set()
    for rows in unresolved:
        reason = _effective(rows).reason
        if reason in ('waiting_for_quota', 'waiting_for_provider_quota'):
            actions.add('等待允许调用的时段；持续限额时核对供应商与本地调用记录')
        elif reason in ('input_blocked', 'settlement_blocked'):
            actions.add('核查输入完整性及缺失报价，不能跳过数据检查')
        else:
            actions.add('核查相应任务的本地日志和执行结果')
    if actions:
        lines.extend(['', '待处理：' + '；'.join(sorted(actions)) + '。'])
    return label, '\n'.join(lines)


def _prepare(queue, contact, config, thread_id, now):
    """Freeze one catch-up batch atomically before any SMTP or contact claim."""
    cutoff = latest_slot(now)
    with queue:
        queue.execute('BEGIN IMMEDIATE')
        old = queue.execute("SELECT * FROM digest_batches WHERE state='prepared' ORDER BY slot_at LIMIT 1").fetchone()
        if old and (old['slot_at'] >= cutoff or contact.execute(
                'SELECT 1 FROM scheduled_messages WHERE event_id=?', (old['batch_id'],)).fetchone()):
            return old
        # On first activation, pending arrivals preceding the current boundary
        # are eligible. Already-sent/suppressed history never enters this query.
        earliest = queue.execute('''SELECT MIN(a.received_at) FROM receipt_arrivals a
            JOIN results r USING(event_id) WHERE r.state='pending' ''').fetchone()[0]
        initial = latest_slot(min(now, earliest)) if earliest is not None else cutoff
        queue.execute("INSERT OR IGNORE INTO digest_settings VALUES ('closed_until', ?)", (initial,))
        start = queue.execute("SELECT value FROM digest_settings WHERE key='closed_until'").fetchone()[0]
        if old:
            # Under the delivery lock, a never-claimed older preparation can be
            # superseded by one catch-up batch. Keep its text/members for audit.
            queue.execute("UPDATE digest_batches SET state='superseded' WHERE batch_id=?", (old['batch_id'],))
            queue.execute('''UPDATE results SET state='pending' WHERE state='digest_reserved'
                AND event_id IN (SELECT event_id FROM digest_members WHERE batch_id=?)''', (old['batch_id'],))
            start = old['start_at']
        if cutoff <= start:
            return None
        # An interrupted legacy per-result send must not be replayed in a digest.
        claims = {r[0] for r in contact.execute('SELECT event_id FROM scheduled_messages')}
        candidates = queue.execute('''SELECT r.* FROM results r JOIN receipt_arrivals a USING(event_id)
            WHERE r.state='pending' AND a.received_at < ? ORDER BY a.received_at, r.event_id''', (cutoff,)).fetchall()
        events = []
        for row in candidates:
            if row['event_id'] in claims:
                queue.execute("UPDATE results SET state='previously_claimed' WHERE event_id=?", (row['event_id'],))
            else:
                events.append(TaskResult.model_validate_json(row['payload']))
        queue.execute("UPDATE digest_settings SET value=? WHERE key='closed_until'", (cutoff,))
        if not events:
            return None
        unconfirmed = queue.execute("SELECT COUNT(*) FROM digest_batches WHERE state='failed_or_ambiguous'").fetchone()[0]
        label, text = summarize_digest(events, start=start, end=cutoff, created=now, unconfirmed=unconfirmed)
        batch_id = uuid4().hex
        mid = make_msgid(idstring='deepstock-digest', domain=config.mailbox.split('@')[-1])
        queue.execute('INSERT INTO digest_batches VALUES (?,?,?,?,?,?,?,?,?)',
                      (batch_id, cutoff, start, now, thread_id, mid, label, text, 'prepared'))
        queue.executemany('INSERT INTO digest_members VALUES (?,?)', ((e.event_id, batch_id) for e in events))
        queue.executemany("UPDATE results SET state='digest_reserved' WHERE event_id=?", ((e.event_id,) for e in events))
        return queue.execute('SELECT * FROM digest_batches WHERE batch_id=?', (batch_id,)).fetchone()


def _finish(queue, batch_id, state):
    with queue:
        queue.execute('UPDATE digest_batches SET state=? WHERE batch_id=?', (state, batch_id))
        queue.execute('''UPDATE results SET state=? WHERE event_id IN
            (SELECT event_id FROM digest_members WHERE batch_id=?)''', ('digest_' + state, batch_id))


def _notify_locked(contact, config, sender, *, thread_id, now):
    from deepstock.session_mail import contact_text, search_token
    queue = outbox(config.root)
    try:
        batch = _prepare(queue, contact, config, thread_id, now)
        if batch is None:
            return 0
        session = {'thread_id': batch['thread_id'], 'final_text': batch['final_text'], 'scheduled_digest': True}
        with contact:
            claim = contact.execute('''INSERT OR IGNORE INTO scheduled_messages VALUES (?,?,?,?,?,?,?)''',
                (batch['batch_id'], batch['message_id'], batch['thread_id'], 'scheduled-digest',
                 batch['final_text'], 'sending', now))
            if claim.rowcount:
                contact.execute('INSERT INTO message_tokens VALUES (?,?)', (batch['message_id'], search_token(batch['message_id'])))
                contact.execute('INSERT INTO outbound_text VALUES (?,?)',
                    (batch['message_id'], contact_text(session, task_label=batch['task_label'])))
        if not claim.rowcount:
            state = contact.execute('SELECT state FROM scheduled_messages WHERE event_id=?', (batch['batch_id'],)).fetchone()[0]
            if state == 'sending':
                with contact:
                    contact.execute("UPDATE scheduled_messages SET state='failed_or_ambiguous' WHERE event_id=?",
                                    (batch['batch_id'],))
            _finish(queue, batch['batch_id'], 'sent' if state == 'sent' else 'failed_or_ambiguous')
            return 0
        try:
            sender(config, session, batch['message_id'], task_label=batch['task_label'])
        except Exception:
            state = 'failed_or_ambiguous'
        else:
            state = 'sent'
        with contact:
            contact.execute('UPDATE scheduled_messages SET state=? WHERE event_id=?', (state, batch['batch_id']))
        _finish(queue, batch['batch_id'], state)
        return int(state == 'sent')
    finally:
        queue.close()


def notify_digest(contact, config, sender, *, thread_id, now=None):
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    with _delivery_lock(config.root) as acquired:
        if not acquired:
            return 0
        return _notify_locked(contact, config, sender, thread_id=thread_id, now=now)
