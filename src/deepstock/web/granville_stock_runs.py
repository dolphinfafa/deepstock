"""Retain every stock candidate and blocked principal; never pick a substitute."""
from datetime import datetime, timezone
import hashlib
import json
from deepstock.web.models import Metric, ResearchReport, ResearchRun, Strategy


def ingest_granville_stock_runs(session, root):
    count = 0
    for path in sorted((root / "artifacts/research/granville-stocks").glob("*/publication.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        cfg = value["config"]
        if value["strategy_id"] != "granville_stock_portfolio" or cfg["principal_variant"] != "trend_pullback" or cfg["principal_exit_policy"] != "time_7" or set(value["market_results"]) != {"US", "CN"}:
            raise ValueError("Invalid fixed stock principal/markets")
        if session.get(Strategy, value["strategy_id"]) is None:
            continue
        for result in value["market_results"].values():
            cases = {(c["variant"], c["exit_policy"], c["cost_case"]): c for c in result["cases"]}
            expected = {(v, e, cost) for v in cfg["variants"] for e in cfg["exit_policies"] for cost in ["base", "stress"]}
            if set(cases) != expected or len(cases) != len(result["cases"]):
                raise ValueError("All fixed stock cases, including blocked cases, must remain visible")
            principal = cases[("trend_pullback", "time_7", "base")]
            if result["metrics"] != principal["periods"].get("full"):
                raise ValueError("Stock display differs from fixed principal")
            for case in cases.values():
                if case["status"] == "blocked" and (not case.get("blocking_reason") or case["periods"]):
                    raise ValueError("Blocked stock case cannot fabricate performance")
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        existing = session.get(ResearchRun, value["id"])
        if existing:
            if existing.source_hash != checksum:
                raise ValueError("Immutable stock publication changed")
            continue
        session.add(ResearchRun(id=value["id"], strategy_id=value["strategy_id"], run_type="granville_stock_fixed_dual_market", status=value["status"],
                                data_start=value["data_start"], data_end=value["data_end"], as_of_date=value["as_of_date"],
                                config_hash=value["config_hash"], code_version=value.get("code_version"), summary=value["summary"],
                                source_hash=checksum, artifact_path=str(path.relative_to(root)), details=value, finished_at=datetime.now(timezone.utc)))
        session.flush()
        for market, result in value["market_results"].items():
            for name in ["total_return", "annualized_return", "sharpe_ratio", "maximum_drawdown", "annualized_turnover", "average_exposure"]:
                if result["metrics"]:
                    session.add(Metric(run_id=value["id"], name=name, scope=market + "_full", value=result["metrics"][name],
                                       unit="number" if name in {"sharpe_ratio", "annualized_turnover"} else "ratio"))
        content = value["report_md"]
        session.add(ResearchReport(id=value["id"] + "-report", strategy_id=value["strategy_id"], run_id=value["id"],
                                   report_type="granville_stock_dual_market_comparison", title="葛兰威尔：双市场多股票组合完整报告",
                                   as_of_date=value["as_of_date"], format="markdown", content=content,
                                   content_hash=hashlib.sha256(content.encode()).hexdigest()))
        count += 1
    session.commit()
    return {"runs": count}
