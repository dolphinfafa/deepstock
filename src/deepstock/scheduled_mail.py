"""Private, durable aggregate receipts for authorized scheduled-task emails."""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, field_validator, model_validator

TASKS = {
    'auction-collect': '集合竞价公告采集',
    'auction-signal': '集合竞价正式信号',
    'auction-fill': '集合竞价入场记录',
    'auction-settle': '集合竞价次日结算',
    'auction-settle-counterfactual': '集合竞价反事实结算',
    'auction-report': '集合竞价日报',
    'auction-minute-backfill': '集合竞价历史分钟补数',
    'app-ingest': '研究状态导入',
    'database-backup': '数据库备份',
    'defensive-observation': '防御型 ETF 定时观察',
}
HIGH_FREQUENCY = {'auction-collect', 'app-ingest'}
STAT_LABELS = {
    'completed_cohorts': '完成样本数', 'cumulative_return': '累计收益率',
    'mean_daily_return': '日均收益率', 'daily_win_rate': '日胜率', 'daily_t_stat': '日收益 t 值',
    'completed_windows': '已补齐日期', 'remaining_windows': '缺少日期',
    'frozen_report_ready': '完整冻结报告已生成', 'pending': '待处理笔数', 'filled': '入场笔数',
    'closed': '结算笔数', 'still_open': '未结算笔数', 'missing_price': '缺报价笔数',
    'missed_capacity': '容量阻塞笔数', 'blocked_observations': '阻塞观察数',
    'actionable': '可执行信号', 'health_passed': '健康检查通过',
    'bytes': '备份字节数', 'integrity_ok': '备份完整性检查通过',
    'rows_received': '采集行数', 'rows_inserted': '新增行数',
}
TaskId = Literal['auction-collect', 'auction-signal', 'auction-fill', 'auction-settle',
                 'auction-settle-counterfactual', 'auction-report', 'auction-minute-backfill',
                 'app-ingest', 'database-backup', 'defensive-observation']


class TaskResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    task_id: TaskId
    node: Literal['server', 'quant']
    started_at: AwareDatetime
    finished_at: AwareDatetime
    exit_code: StrictInt = Field(ge=-(2**31), le=2**32 - 1)
    status: Literal['completed', 'failed', 'blocked', 'skipped']
    reason: Literal['completed', 'process_failed', 'waiting_for_quota', 'waiting_for_provider_quota',
                    'fetch_failed', 'finalize_failed', 'missed_decision_cutoff', 'skipped_locked',
                    'skipped_non_trading_day', 'already_recorded', 'nothing_pending', 'result_unavailable']
    data_date: date | None = None
    stats: dict[str, StrictInt | StrictFloat | StrictBool | None] = Field(default_factory=dict)

    @field_validator('stats')
    @classmethod
    def aggregate_only(cls, values):
        if set(values).difference(STAT_LABELS) or any(
            isinstance(v, float) and not math.isfinite(v) for v in values.values()
        ):
            raise ValueError('Only finite approved aggregate statistics are accepted')
        return values

    @model_validator(mode='after')
    def consistent(self):
        if self.finished_at < self.started_at:
            raise ValueError('Task cannot finish before starting')
        if self.node == 'quant' and self.task_id != 'defensive-observation':
            raise ValueError('Quant node may publish only its scheduled observation')
        if self.exit_code != 0 and self.status != 'failed':
            raise ValueError('Nonzero exit must remain failed')
        return self


def outbox(root: Path):
    path = root / 'artifacts/scheduled-mail/private/outbox.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    path.chmod(0o600)
    db.execute('''CREATE TABLE IF NOT EXISTS results
        (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL)''')
    db.commit()
    return db


def enqueue(root: Path, result: TaskResult):
    payload = json.dumps(result.model_dump(mode='json'), sort_keys=True, separators=(',', ':'))
    db = outbox(root)
    try:
        db.execute("INSERT OR IGNORE INTO results VALUES (?, ?, 'pending')", (result.event_id, payload))
        stored = db.execute('SELECT payload FROM results WHERE event_id=?', (result.event_id,)).fetchone()[0]
        if stored != payload:
            raise ValueError('A scheduled receipt identity cannot be overwritten')
        db.commit()
    finally:
        db.close()


def publish_pending(root: Path, source, client=httpx):
    """Windows sends aggregates with its existing node token, never mail credentials."""
    if not source.node_token:
        return 0
    sent = 0
    db = outbox(root)
    try:
        for row in db.execute("SELECT * FROM results WHERE state='pending' ORDER BY rowid").fetchall():
            event = TaskResult.model_validate_json(row['payload'])
            if event.node != 'quant':
                continue
            response = client.post(f'{source.public_base_url}/api/agent/scheduled-results',
                headers={'Authorization': f'Bearer {source.node_token}'},
                json=event.model_dump(mode='json'), timeout=20)
            response.raise_for_status()
            db.execute("UPDATE results SET state='published' WHERE event_id=?", (event.event_id,))
            db.commit()
            sent += 1
    finally:
        db.close()
    return sent


def summarize(result: TaskResult):
    labels = {'completed': '完成', 'failed': '失败', 'blocked': '阻塞', 'skipped': '跳过'}
    reasons = {'completed': '任务执行完成', 'process_failed': '程序执行失败',
        'waiting_for_quota': '等待调用额度', 'waiting_for_provider_quota': '供应商额度不足',
        'fetch_failed': '补数失败', 'finalize_failed': '完整报告生成失败',
        'missed_decision_cutoff': '错过正式信号截止时间', 'skipped_locked': '已有任务运行，跳过',
        'skipped_non_trading_day': '非交易日', 'already_recorded': '已有记录',
        'nothing_pending': '没有待处理记录', 'result_unavailable': '任务结果摘要缺失或未能核对'}
    local = ZoneInfo('Asia/Shanghai')
    lines = [f'任务：{TASKS[result.task_id]}', f'节点：{result.node}',
             f'状态：{labels[result.status]}（{reasons[result.reason]}）', f'退出码：{result.exit_code}',
             f'开始：{result.started_at.astimezone(local).isoformat()}',
             f'结束：{result.finished_at.astimezone(local).isoformat()}']
    if result.data_date:
        lines.append(f'数据日期：{result.data_date.isoformat()}')
    for key, value in result.stats.items():
        if value is None:
            continue
        if key.endswith('return') or key == 'daily_win_rate':
            shown = f'{value:.2%}'
        elif isinstance(value, bool):
            shown = '是' if value else '否'
        else:
            shown = str(value)
        lines.append(f'{STAT_LABELS[key]}：{shown}')
    return '\n'.join(lines)


def notify_pending(db, config, sender, *, thread_id: str, scope='results', now=None):
    """Claim in contact state before SMTP; crashes/ambiguous sends never replay."""
    from email.utils import make_msgid
    from deepstock.session_mail import search_token
    if scope not in ('results', 'all'):
        raise ValueError('Invalid scheduled email scope')
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    db.execute("INSERT OR IGNORE INTO settings VALUES ('scheduled_activated_at', ?)", (now,))
    db.commit()
    activation = db.execute("SELECT value FROM settings WHERE key='scheduled_activated_at'").fetchone()[0]
    sent = 0
    queue = outbox(config.root)
    try:
        for row in queue.execute("SELECT * FROM results WHERE state='pending' ORDER BY rowid").fetchall():
            result = TaskResult.model_validate_json(row['payload'])
            if result.finished_at.timestamp() < activation:
                state = 'before_activation'
            elif scope == 'results' and result.task_id in HIGH_FREQUENCY and result.status == 'completed':
                state = 'suppressed_success'
            else:
                mid = make_msgid(idstring='deepstock-scheduled', domain=config.mailbox.split('@')[-1])
                text = summarize(result)
                claim = db.execute('''INSERT OR IGNORE INTO scheduled_messages
                    VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (result.event_id, mid, thread_id, result.task_id, text, 'sending', now))
                if claim.rowcount:
                    db.execute('INSERT INTO message_tokens VALUES (?, ?)', (mid, search_token(mid)))
                    db.commit()
                    try:
                        sender(config, {'thread_id': thread_id, 'final_text': text}, mid,
                               task_label=TASKS[result.task_id])
                    except Exception:
                        state = 'failed_or_ambiguous'
                    else:
                        state = 'sent'
                        sent += 1
                    db.execute('UPDATE scheduled_messages SET state=? WHERE event_id=?', (state, result.event_id))
                    db.commit()
                else:
                    db.commit()
                    state = 'previously_claimed'
            queue.execute('UPDATE results SET state=? WHERE event_id=?', (state, result.event_id))
            queue.commit()
    finally:
        queue.close()
    return sent
