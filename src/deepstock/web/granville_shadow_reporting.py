"""Bounded aggregate Granville reports; existing performance remains immutable."""
import hashlib

from deepstock.strategies.us.granville_shadow import fingerprint, semantic_identity, validate_bundle
from deepstock.web.models import ProgressEvent, ResearchReport


def ingest(session, value):
    item = validate_bundle(value)
    identity = semantic_identity(value)
    identity['status'] = 'recorded' if item.status == 'already_recorded' else item.status
    digest = fingerprint(identity)
    report_id = 'granville-shadow-' + item.data_date.isoformat() + '-' + digest[:16]
    if session.get(ResearchReport, report_id):
        return {'status': 'already_recorded', 'report_id': report_id}
    text = f'''# 葛兰威尔美股影子观察

数据日期：{item.data_date}；状态：{item.status}。固定 trend_pullback/time_7；最多5仓，单次目标16%，开仓总上限80%。

事前计划：{item.planned_sessions}；有效计划结算：{item.shadow_sessions}/60；漏计划持仓估值日：{item.missed_plan_sessions}；输入阻塞：{item.coverage_failures}。

60日只允许人工复核流程与成本，不自动晋升。下一交易日开盘模拟成交只使用事前保存的意图；漏计划日不补造决策。
复权经济单位不等于真实股数、分红到账或券商成交。尚未启用IBKR Paper下单，历史WBA及长历史缺口保留。
原始数据、计划、逐笔成交和持仓只留量化电脑；当前成员按采集时实际获知状态记录。原US/CN历史绩效不改写。

| 对照 | 成本后累计收益 | 最大回撤 | Sharpe | 成本USD | 换手 | 持仓数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
'''
    for name, metrics in item.summaries.items():
        text += f'| {name} | {metrics.total_return:.4%} | {metrics.max_drawdown:.4%} | {metrics.sharpe} | {metrics.costs_usd:.2f} | {metrics.turnover:.4f} | {metrics.position_count} |\n'
    session.add(ResearchReport(id=report_id, strategy_id='granville_stock_portfolio', run_id=None,
        title='葛兰威尔美股前瞻影子记录', report_type='granville_prospective_shadow', as_of_date=item.data_date.isoformat(),
        content=text, artifact_path='artifacts/granville_shadow_node/latest.json', content_hash=hashlib.sha256(text.encode()).hexdigest()))
    session.add(ProgressEvent(id=digest[:32], strategy_id='granville_stock_portfolio', stage='prospective',
        title='美股固定规则影子观察', detail=f'截至{item.data_date}：{item.shadow_sessions}/60个有效结算日；{item.status}；仅影子观察。',
        status='current' if item.status in ('recorded', 'already_recorded', 'preflight_ready') else 'blocked',
        source='granville_shadow_node', occurred_at=item.captured_at_utc))
    return {'status': 'published', 'report_id': report_id}
