"""Aggregate-only ADX publication into existing report/progress types."""
import hashlib
import json
from datetime import datetime
from deepstock.adx_shadow import validate_bundle, semantic_identity
from deepstock.web.models import ResearchReport, ProgressEvent


def ingest(session,value):
    item=validate_bundle(value)
    identity=semantic_identity(value)
    identity['status']='recorded' if item.status in ('recorded','already_recorded') else item.status
    digest=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    report_id='adx-shadow-'+item.data_date+'-'+digest[:16]
    if session.get(ResearchReport,report_id):
        return {'status':'already_recorded','report_id':report_id}
    text=f'''# ADX 完整路由前瞻对照

数据日期：{item.data_date}；观察段：{item.segment}；状态：{item.status}

已结算交易日：{item.sessions}；ADX有效状态切换：{item.switches}；输入阻塞：{item.coverage_failures}

126个有效交易日且4次切换后进入人工复核。当前复核门槛：{'已达到' if item.review_ready else '未达到'}。
所有历史仅作暖期；完整原始数据、计划和持仓留在量化电脑。未授权订单。

| 对照 | 累计成本后收益 | 最大回撤 | Sharpe | 累计成本 | 换手 |
| --- | ---: | ---: | ---: | ---: | ---: |
'''
    for name,metrics in item.summaries.items():
        text+=f"| {name} | {metrics['total_return']:.4%} | {metrics['max_drawdown']:.4%} | {metrics['sharpe']} | {metrics['transaction_cost']:.4%} | {metrics['turnover']:.4f} |\n"
    session.add(ResearchReport(id=report_id,strategy_id='arc',run_id=None,title='ADX完整路由前瞻记录',
        report_type='prospective_shadow',as_of_date=item.data_date,content=text,
        artifact_path='artifacts/adx_node/latest.json',content_hash=hashlib.sha256(text.encode()).hexdigest()))
    session.add(ProgressEvent(id=digest[:32],strategy_id='arc',stage='prospective',title='ADX前瞻对照',
        detail=f"截至{item.data_date}：{item.sessions}/126个结算日，{item.switches}/4次切换，{item.status}；输入阻塞{item.coverage_failures}。",
        status='blocked' if item.coverage_failures else 'current',source='adx_node',occurred_at=item.captured_at_utc))
    return {'status':'published','report_id':report_id}
