from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from deepstock.web.config import settings
from deepstock.observation_reporting import validate_bundle
from deepstock.strategy_governance import evaluate_snapshot, load_registry
from deepstock.web.models import (
    DataSourceStatus,
    JobStatus,
    Metric,
    ProgressEvent,
    ResearchReport,
    ResearchRun,
    Strategy,
    StrategyVersion,
    SystemSetting,
    utcnow,
)


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_json(value: Any) -> str:
    return _hash_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _upsert(session: Session, model, key: Any, values: dict[str, Any]):  # type: ignore[no-untyped-def]
    instance = session.get(model, key)
    if instance is None:
        instance = model(**values)
        session.add(instance)
    else:
        for name, value in values.items():
            setattr(instance, name, value)
    return instance


def _seed_settings(session: Session) -> None:
    defaults = {
        "global_kill_switch": settings.global_kill_switch,
        "live_trading_enabled": settings.live_trading_enabled,
        "live_notional_cap_usd": settings.live_notional_cap_usd,
        "email_configured": settings.email_configured,
        "email_test_passed": False,
    }
    for key, value in defaults.items():
        if session.get(SystemSetting, key) is None:
            session.add(SystemSetting(key=key, value=value))
    configured = session.get(SystemSetting, "email_configured")
    if configured is not None:
        configured.value = settings.email_configured


def ingest_catalog(session: Session, catalog_path: Path | None = None) -> dict[str, int]:
    path = catalog_path or settings.project_root / "config/strategy_catalog.json"
    catalog = _load_json(path)
    strategy_count = 0
    metric_count = 0
    for item in catalog["strategies"]:
        strategy_count += 1
        strategy = _upsert(
            session,
            Strategy,
            item["id"],
            {
                "id": item["id"],
                "display_name": item["display_name"],
                "code": item["code"],
                "market": item["market"],
                "asset_class": item["asset_class"],
                "summary": item["summary"],
                "thesis_md": item["thesis_md"],
                "status": item["status"],
                "execution_status": item["execution_status"],
                "spec_path": item["spec_path"],
                "current_version": item["version"],
                "live_eligible": False,
                "is_archived": item.get("is_archived", False),
                "archived_at": datetime.fromisoformat(item["archived_at"]) if item.get("archived_at") else None,
                "archive_reason": item.get("archive_reason"),
                "updated_at": utcnow(),
            },
        )
        # The objects below reference the strategy, but the models intentionally do
        # not expose ORM relationships. Flush the parent first so SQLite foreign-key
        # enforcement does not depend on SQLAlchemy's insert ordering heuristics.
        session.flush()
        config_hash = _hash_json(
            {
                "version": item["version"],
                "thesis": item["thesis_md"],
                "metrics": item.get("metrics", {}),
            }
        )
        version = session.scalar(
            select(StrategyVersion).where(
                StrategyVersion.strategy_id == item["id"],
                StrategyVersion.version == item["version"],
            )
        )
        if version is None:
            session.add(
                StrategyVersion(
                    strategy_id=item["id"],
                    version=item["version"],
                    config_hash=config_hash,
                    config={"catalog_schema": catalog["schema_version"]},
                    frozen=True,
                    frozen_at=utcnow(),
                    active=not item.get("is_archived", False),
                )
            )
        else:
            version.config_hash = config_hash
            version.active = not item.get("is_archived", False)

        run_id = f"catalog-{item['id']}-{item['version']}"
        run = _upsert(
            session,
            ResearchRun,
            run_id,
            {
                "id": run_id,
                "strategy_id": item["id"],
                "run_type": "latest_research",
                "status": "complete" if item["metrics"] else "planned",
                "as_of_date": "2026-10-04",
                "config_hash": config_hash,
                "summary": item["summary"],
                "artifact_path": item["spec_path"],
                "source_hash": config_hash,
                "details": {"catalog_version": catalog["schema_version"]},
            },
        )
        session.flush()
        session.execute(delete(Metric).where(Metric.run_id == run.id))
        for name, metric in item.get("metrics", {}).items():
            session.add(
                Metric(
                    run_id=run.id,
                    scope=metric.get("scope", "overall"),
                    name=name,
                    value=metric.get("value"),
                    text_value=metric.get("text_value"),
                    unit=metric.get("unit"),
                    benchmark=metric.get("benchmark"),
                )
            )
            metric_count += 1

        session.execute(
            delete(ProgressEvent).where(
                ProgressEvent.strategy_id == item["id"],
                ProgressEvent.source == "catalog",
            )
        )
        for progress in item.get("progress", []):
            session.add(
                ProgressEvent(
                    strategy_id=item["id"],
                    stage=progress["stage"],
                    title=progress["title"],
                    detail=progress.get("detail", ""),
                    status=progress.get("status", "current"),
                    source="catalog",
                )
            )

        spec_path = settings.project_root / item["spec_path"]
        if spec_path.exists():
            content = spec_path.read_text(encoding="utf-8")
            content_hash = _hash_bytes(content.encode("utf-8"))
            report_id = f"spec-{item['id']}-{content_hash[:12]}"
            existing_reports = session.scalars(
                select(ResearchReport).where(
                    ResearchReport.strategy_id == item["id"],
                    ResearchReport.report_type == "strategy_specification",
                )
            ).all()
            for existing in existing_reports:
                if existing.id != report_id:
                    session.delete(existing)
            _upsert(
                session,
                ResearchReport,
                report_id,
                {
                    "id": report_id,
                    "strategy_id": item["id"],
                    "run_id": run.id,
                    "title": f"{item['display_name']} 研究规范",
                    "report_type": "strategy_specification",
                    "as_of_date": "2026-10-04",
                    "format": "markdown",
                    "content": content,
                    "artifact_path": item["spec_path"],
                    "content_hash": content_hash,
                },
            )
    _seed_settings(session)
    session.commit()
    return {"strategies": strategy_count, "metrics": metric_count}


def _upsert_metric(
    session: Session,
    run_id: str,
    name: str,
    value: float | None,
    *,
    unit: str = "number",
    scope: str = "live",
) -> None:
    metric = session.scalar(
        select(Metric).where(
            Metric.run_id == run_id,
            Metric.scope == scope,
            Metric.name == name,
        )
    )
    if metric is None:
        session.add(
            Metric(run_id=run_id, scope=scope, name=name, value=value, unit=unit)
        )
    else:
        metric.value = value
        metric.unit = unit


def ingest_auction_forward(session: Session) -> dict[str, Any]:
    report_path = settings.project_root / "artifacts/short_term_forward/reports/latest.json"
    manifest_path = settings.project_root / "artifacts/auction_history_tushare/minute_manifest.json"
    sync_path = settings.project_root / "artifacts/csi300_sync/latest.json"
    backfill_path = settings.project_root / "artifacts/csi300_sync/minute_backfill_status.json"
    backfill = _load_json(backfill_path) if backfill_path.exists() else {}
    result: dict[str, Any] = {"status": "missing"}
    if report_path.exists():
        report = _load_json(report_path)
        source_hash = _hash_bytes(report_path.read_bytes())
        run_id = f"auction-forward-{report['as_of_date']}"
        run = _upsert(
            session,
            ResearchRun,
            run_id,
            {
                "id": run_id,
                "strategy_id": "csi300_opening_auction",
                "run_type": "forward_observation",
                "status": "complete",
                "as_of_date": report["as_of_date"],
                "data_end": report["as_of_date"],
                "config_hash": "auction_context_v1_frozen_20260829",
                "summary": "沪深300集合竞价前向模拟最新正式报告。",
                "artifact_path": str(report_path.relative_to(settings.project_root)),
                "source_hash": source_hash,
                "details": report,
                "finished_at": datetime.fromtimestamp(
                    report_path.stat().st_mtime, timezone.utc
                ),
            },
        )
        session.flush()
        baseline = report["strategies"].get("model_baseline", {})
        gate = report.get("evaluation_gate", {})
        for name, value, unit in (
            ("completed_cohorts", gate.get("completed_baseline_cohorts"), "count"),
            ("cumulative_return", baseline.get("cumulative_return"), "ratio"),
            ("mean_daily_return", baseline.get("mean_daily_return"), "ratio"),
            ("daily_win_rate", baseline.get("daily_win_rate"), "ratio"),
            ("daily_t_stat", baseline.get("daily_t_stat"), "number"),
        ):
            _upsert_metric(session, run.id, name, value, unit=unit, scope="forward")
        content_path = report_path.with_suffix(".md")
        content = (
            content_path.read_text(encoding="utf-8")
            if content_path.exists()
            else "```json\n" + json.dumps(report, ensure_ascii=False, indent=2) + "\n```"
        )
        report_id = f"auction-forward-report-{report['as_of_date']}"
        _upsert(
            session,
            ResearchReport,
            report_id,
            {
                "id": report_id,
                "strategy_id": "csi300_opening_auction",
                "run_id": run.id,
                "title": f"集合竞价前向报告 · {report['as_of_date']}",
                "report_type": "forward_observation",
                "as_of_date": report["as_of_date"],
                "format": "markdown",
                "content": content,
                "artifact_path": str(report_path.relative_to(settings.project_root)),
                "content_hash": _hash_bytes(content.encode("utf-8")),
            },
        )
        result = {
            "status": "ok",
            "as_of_date": report["as_of_date"],
            "completed_cohorts": gate.get("completed_baseline_cohorts"),
        }

    completed = 0
    remaining = 21
    if manifest_path.exists():
        manifest = _load_json(manifest_path)
        target_entries = {
            key: value
            for key, value in manifest.items()
            if "20260831_20260831" <= key <= "20260929_20260929"
            and len(set(key.split("_"))) == 1
        }
        completed = sum(
            1 for key, value in target_entries.items()
            if value.get("status") == "ok" and value.get("coverage", 0) >= 0.90
            and (manifest_path.parent / "minute_entries" / f"{key}.csv.gz").exists()
        )
        remaining = 21 - completed
        _upsert(
            session,
            DataSourceStatus,
            "tushare-auction-minute-backfill",
            {
                "id": "tushare-auction-minute-backfill",
                "provider": "Tushare stk_mins",
                "node": "darwen-server",
                "status": "complete" if remaining == 0 else "backfilling",
                "data_date": "2026-09-29" if remaining == 0 else None,
                "checked_at": utcnow(),
                "details": {**backfill, "completed": completed, "total": 21, "remaining": remaining},
            },
        )
        _upsert(
            session,
            JobStatus,
            "auction-minute-backfill",
            {
                "id": "auction-minute-backfill",
                "display_name": "集合竞价 09:31 历史分钟补数",
                "status": "complete" if remaining == 0 else backfill.get("status", "running"),
                "last_finished_at": datetime.fromtimestamp(
                    manifest_path.stat().st_mtime, timezone.utc
                ),
                "message": f"{completed}/21 完成，剩余 {remaining} 日；每日最多两次，11:30 / 12:35 补数",
                "details": {**backfill, "completed": completed, "remaining": remaining},
            },
        )
    frozen_path = manifest_path.parent / "execution_backtest_20260929_frozen.json"
    if remaining == 0 and frozen_path.exists():
        frozen = _load_json(frozen_path)
        run_id = "auction-september-frozen-prospective"
        _upsert(session, ResearchRun, run_id, {
            "id": run_id, "strategy_id": "csi300_opening_auction", "run_type": "frozen_prospective",
            "status": "complete", "as_of_date": "2026-09-29", "data_start": frozen["data_start"],
            "data_end": frozen["data_end"], "config_hash": "auction_context_v1_frozen_20260829",
            "source_hash": _hash_bytes(frozen_path.read_bytes()), "summary": "21/21 分钟标签补齐后的冻结评估；不用于事后选参。",
            "artifact_path": str(frozen_path.relative_to(settings.project_root)), "details": frozen,
        })
        session.flush()
        markdown = frozen_path.with_suffix(".md")
        content = markdown.read_text(encoding="utf-8") if markdown.exists() else json.dumps(frozen, ensure_ascii=False, indent=2)
        _upsert(session, ResearchReport, run_id + "-report", {
            "id": run_id + "-report", "strategy_id": "csi300_opening_auction", "run_id": run_id,
            "title": "九月集合竞价冻结完整评估", "report_type": "frozen_prospective", "as_of_date": "2026-09-29",
            "format": "markdown", "content": content, "content_hash": _hash_bytes(content.encode()),
            "artifact_path": str(frozen_path.relative_to(settings.project_root)),
        })
    if sync_path.exists():
        sync = _load_json(sync_path)
        _upsert(
            session,
            JobStatus,
            "csi300-artifact-sync",
            {
                "id": "csi300-artifact-sync",
                "display_name": "Darwen → Deepstock 集合竞价同步",
                "status": sync.get("status", "unknown"),
                "last_finished_at": datetime.fromisoformat(sync["completed_at_utc"]),
                "message": "SQLite 一致性备份与增量文件同步",
                "details": sync,
            },
        )
    session.commit()
    return {**result, "minute_completed": completed, "minute_remaining": remaining}


def ingest_defensive_observation(session: Session) -> dict[str, Any]:
    bundle_path = settings.project_root / "artifacts/defensive_node/latest.json"
    if bundle_path.exists():
        result = ingest_defensive_bundle(session, _load_json(bundle_path))
        session.commit()
        return result
    plan_path = settings.project_root / "artifacts/paper/defensive-etf/latest.json"
    observations_path = (
        settings.project_root / "artifacts/paper/defensive-etf/observations.jsonl"
    )
    if not plan_path.exists():
        return {"status": "missing"}
    plan = _load_json(plan_path)
    observations = []
    if observations_path.exists():
        observations = [
            json.loads(line)
            for line in observations_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    unique_sessions = len({row.get("plan_id") for row in observations if row.get("plan_id")})
    data_date = str(plan.get("data_date", "")) or None
    _upsert(
        session,
        DataSourceStatus,
        "defensive-etf-observation",
        {
            "id": "defensive-etf-observation",
            "provider": "Norgate Data",
            "node": "DESKTOP-ORNLESD",
            "status": "shadow_observation",
            "data_date": data_date,
            "checked_at": utcnow(),
            "details": {
                "plan_id": plan.get("plan_id"),
                "plan_status": plan.get("status"),
                "shadow_sessions": unique_sessions,
            },
        },
    )
    _upsert(
        session,
        JobStatus,
        "defensive-etf-observation",
        {
            "id": "defensive-etf-observation",
            "display_name": "防御型 ETF 每日影子观察",
            "status": "ok",
            "last_finished_at": datetime.fromtimestamp(plan_path.stat().st_mtime, timezone.utc),
            "message": f"数据日期 {data_date}，影子会话 {unique_sessions}",
            "details": {"data_date": data_date, "shadow_sessions": unique_sessions},
        },
    )
    session.commit()
    return {"status": "ok", "data_date": data_date, "shadow_sessions": unique_sessions}


def ingest_defensive_bundle(session: Session, bundle: dict[str, Any]) -> dict[str, Any]:
    validate_bundle(bundle)
    snapshot, summary, plan = (bundle[key] for key in ("snapshot", "summary", "plan"))
    assessment = evaluate_snapshot(snapshot, load_registry(settings.project_root / "config/strategy_registry.json"))
    captured = datetime.fromisoformat(bundle["captured_at_utc"])
    data_date = snapshot["data_date"]
    run_id = f"defensive-observation-{snapshot['as_of_date']}-{snapshot['config_hash'][:8]}"
    run = _upsert(session, ResearchRun, run_id, {
        "id": run_id, "strategy_id": "adaptive_defensive_etf", "run_type": "shadow_observation",
        "status": "complete", "as_of_date": snapshot["as_of_date"],
        "data_start": summary["start"], "data_end": data_date,
        "config_hash": snapshot["config_hash"], "source_hash": _hash_json(bundle),
        "summary": f"冻结参数一致；数据截至 {data_date}，有效观察 {snapshot['shadow_sessions']} 次；继续影子观察。",
        "artifact_path": "artifacts/defensive_node/latest.json", "finished_at": captured,
        "details": {"snapshot": snapshot, "assessment": assessment, "target_weights": plan["target_weights"]},
    })
    session.flush()
    for name in ("total_return", "annualized_return", "sharpe_ratio", "maximum_drawdown", "total_transaction_cost", "benchmark_total_return"):
        _upsert_metric(session, run.id, name, summary[name], scope="full_history", unit="number" if name == "sharpe_ratio" else "ratio")
    for name in ("rolling_oos_sharpe", "rolling_oos_max_drawdown", "annualized_turnover", "walk_forward_windows", "negative_walk_forward_windows", "shadow_sessions", "shadow_observation_calendar_days"):
        _upsert_metric(session, run.id, name, snapshot[name], scope="governance", unit="ratio" if name == "rolling_oos_max_drawdown" else "number")
    message = f"数据日期 {data_date}，有效影子会话 {snapshot['shadow_sessions']}，观察 {snapshot['shadow_observation_calendar_days']} 天"
    _upsert(session, JobStatus, "defensive-etf-observation", {
        "id": "defensive-etf-observation", "display_name": "防御型 ETF 每日影子观察",
        "status": "ok", "last_finished_at": captured, "message": message,
        "details": {**snapshot, "assessment": assessment},
    })
    _upsert(session, DataSourceStatus, "defensive-etf-observation", {
        "id": "defensive-etf-observation", "provider": "Norgate Data", "node": "DESKTOP-ORNLESD",
        "status": "shadow_observation", "data_date": data_date, "checked_at": captured,
        "details": {**snapshot, "plan_id": plan["plan_id"]},
    })
    _upsert(session, ProgressEvent, "defensive-node-observation", {
        "id": "defensive-node-observation", "strategy_id": "adaptive_defensive_etf",
        "stage": "shadow", "title": "量化电脑最新观察", "detail": message,
        "status": "current", "source": "quant-computer", "occurred_at": captured,
    })
    failed = [key for key, passed in assessment["checks"].items() if not passed]
    content = (
        f"# 防御型 ETF 冻结参数研究日报\n\n评估日期：{snapshot['as_of_date']}；数据截至：{data_date}。\n\n"
        f"参数：252 日动量、200 日趋势/市场过滤、Top 2、单仓上限 20%、5 bps 成本。\n\n"
        f"全历史区间：{summary['start']} — {data_date}，年化 {summary['annualized_return']:.2%}，"
        f"Sharpe {summary['sharpe_ratio']:.2f}，最大回撤 {summary['maximum_drawdown']:.2%}。\n\n"
        f"最近 252 日 Sharpe {snapshot['rolling_oos_sharpe']:.2f}，回撤 {snapshot['rolling_oos_max_drawdown']:.2%}。\n\n"
        f"Walk-Forward：{snapshot['walk_forward_windows']} 个窗口，{snapshot['negative_walk_forward_windows']} 个负收益窗口。\n\n"
        f"有效观察 {snapshot['shadow_sessions']} 次，{snapshot['shadow_observation_calendar_days']} 个日历日。\n\n"
        f"未通过项目：{', '.join(failed) or '无'}。尚无订单授权。\n\n"
        "本日报已修正此前默认回测与冻结配置不一致的问题。原历史治理决策保留；这些指标属于重新核验后的当前评估。\n"
    )
    _upsert(session, ResearchReport, f"{run_id}-report", {
        "id": f"{run_id}-report", "strategy_id": "adaptive_defensive_etf", "run_id": run.id,
        "title": f"防御 ETF 冻结参数日报 · {snapshot['as_of_date']}", "report_type": "shadow_observation",
        "as_of_date": snapshot["as_of_date"], "format": "markdown", "content": content,
        "artifact_path": "artifacts/defensive_node/latest.json", "content_hash": _hash_bytes(content.encode()),
    })
    return {"status": "ok", "data_date": data_date, "shadow_sessions": snapshot["shadow_sessions"], "run_id": run.id}


def ingest_all(session: Session) -> dict[str, Any]:
    return {
        "catalog": ingest_catalog(session),
        "auction": ingest_auction_forward(session),
        "defensive": ingest_defensive_observation(session),
    }
