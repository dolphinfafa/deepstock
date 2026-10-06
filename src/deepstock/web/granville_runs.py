"""Publish the fixed principal and all comparisons, never rank new returns."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from deepstock.web.models import Metric, ResearchNote, ResearchReport, ResearchRun, Strategy


def ingest_granville_runs(session, root: Path):
    count = 0
    for path in sorted((root / "artifacts/research/granville").glob("*/publication.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if value["strategy_id"] != "granville_ma_swing" or value["config"]["principal_variant"] != "trend_pullback":
            raise ValueError("Invalid fixed Granville principal")
        if set(value["market_results"]) != {"US", "CN"}:
            raise ValueError("Both requires two independent market results")
        if session.get(Strategy, value["strategy_id"]) is None:
            continue
        run_id = value["id"]
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        existing = session.get(ResearchRun, run_id)
        if existing:
            if existing.source_hash != checksum:
                raise ValueError("Immutable Granville publication changed")
            continue
        session.add(ResearchRun(id=run_id, strategy_id=value["strategy_id"], run_type="granville_fixed_dual_market", status=value["status"],
                                data_start=value["data_start"], data_end=value["data_end"], as_of_date=value["as_of_date"],
                                config_hash=value["config_hash"], code_version=value.get("code_version"), summary=value["summary"],
                                source_hash=checksum, artifact_path=str(path.relative_to(root)), details=value, finished_at=datetime.now(timezone.utc)))
        session.flush()
        for market, result in value["market_results"].items():
            cases = {(case["variant"], case["cost_case"]): case for case in result["cases"]}
            if set(cases) != {(v, cost) for v in value["config"]["variants"] for cost in ["base", "stress"]}:
                raise ValueError("All fixed candidates/costs must remain visible")
            if result["metrics"] != cases[("trend_pullback", "base")]["periods"]["full"]:
                raise ValueError("Displayed metrics differ from fixed principal")
            for name in ["total_return", "annualized_return", "sharpe_ratio", "maximum_drawdown", "annualized_turnover", "average_exposure"]:
                session.add(Metric(run_id=run_id, name=name, scope=market + "_full", value=result["metrics"][name],
                                   unit="number" if name in {"sharpe_ratio", "annualized_turnover"} else "ratio"))
        content = value["report_md"]
        session.add(ResearchReport(id=run_id + "-report", strategy_id=value["strategy_id"], run_id=run_id,
                                   report_type="granville_dual_market_comparison", title="葛兰威尔：双市场固定候选完整回测报告",
                                   as_of_date=value["as_of_date"], format="markdown", content=content,
                                   content_hash=hashlib.sha256(content.encode()).hexdigest()))
        count += 1
    # The already-approved original note belongs to this independent candidate.
    note = session.get(ResearchNote, "3406ec87-93b6-4cf0-b6ad-4ab7697560c5")
    if note and note.user_decision == "approved_for_test" and session.get(Strategy, "granville_ma_swing"):
        note.strategy_id = "granville_ma_swing"
        note.scope_type = "strategy"
    session.commit()
    return {"runs": count}
