"""Comparable annualized slots without turning cohort returns into account CAGR."""
from __future__ import annotations
from sqlalchemy import select
from deepstock.web.models import Metric


def annualization_payload(session, run):
    unavailable = {"value": None, "reason": "尚无可靠的成本后连续净值证据", "scope": None, "sessions": None, "short_sample": False}
    if run is None:
        return unavailable
    if "auction" in run.strategy_id:
        return {**unavailable, "reason": "现有指标为逐批次收益，不能当作连续账户年化", "scope": "forward"}
    if run.status not in {"complete", "completed", "ok"}:
        return {**unavailable, "reason": "需要更多数据或尚未完成合格回测"}
    if run.details.get("market_results"):
        return {**unavailable, "reason": "两市场独立计年化，不合并USD/CNY净值；见各市场结果", "scope": "separate_markets"}
    evidence = run.details.get("annualization")
    if evidence:
        return evidence
    metric = session.scalar(select(Metric).where(Metric.run_id == run.id, Metric.name == "annualized_return"))
    if metric is None or metric.value is None:
        return unavailable
    return {"value": metric.value, "scope": metric.scope, "data_start": run.data_start, "data_end": run.data_end,
            "sessions": None, "short_sample": False, "source": "legacy_report", "reason": "历史报告值；原报告未登记净值样本数，未重新推算", "cost_basis": "原研究报告成本口径"}
