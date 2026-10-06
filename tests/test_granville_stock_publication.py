from copy import deepcopy
import pytest
from deepstock.data.store import write_json
from deepstock.web.database import SessionLocal
from deepstock.web.granville_stock_runs import ingest_granville_stock_runs
from deepstock.web.models import ResearchRun, ResearchReport
from test_web_platform import initialized_database


def fixture_value():
    metrics = {"total_return": .01, "annualized_return": .001, "sharpe_ratio": .02, "maximum_drawdown": -.1,
               "annualized_turnover": 2., "average_exposure": .1}
    cfg = {"principal_variant": "trend_pullback", "principal_exit_policy": "time_7", "variants": ["ma_cross", "trend_pullback", "deviation_reversal"], "exit_policies": ["time_7", "trend_only"]}
    cases = [{"variant": v, "exit_policy": e, "cost_case": c, "status": "completed", "periods": {"full": {**metrics, "annualized_return": .9 if e == "trend_only" else .001}}}
             for v in cfg["variants"] for e in cfg["exit_policies"] for c in ["base", "stress"]]
    return {"id": "granville-stocks-synthetic-fixture", "strategy_id": "granville_stock_portfolio", "status": "completed",
            "data_start": "2025-01-02", "data_end": "2026-09-29", "as_of_date": "2026-10-06", "config_hash": "f" * 64,
            "config": cfg, "summary": "synthetic test only", "report_md": "# Unit fixture, not performance evidence",
            "market_results": {m: {"metrics": deepcopy(metrics), "cases": deepcopy(cases)} for m in ["US", "CN"]}}


def test_stock_publication_preserves_principal_block_and_all_comparisons(tmp_path):
    value = fixture_value()
    cn = value["market_results"]["CN"]
    principal = next(c for c in cn["cases"] if (c["variant"], c["exit_policy"], c["cost_case"]) == ("trend_pullback", "time_7", "base"))
    principal.update(status="blocked", periods={}, blocking_reason="synthetic unverified terminal proceeds")
    cn["metrics"] = None
    value["status"] = "completed_with_blocks"
    path = tmp_path / "artifacts/research/granville-stocks/unit/publication.json"
    write_json(path, value)
    with SessionLocal() as session:
        try:
            assert ingest_granville_stock_runs(session, tmp_path) == {"runs": 1}
            assert ingest_granville_stock_runs(session, tmp_path) == {"runs": 0}
            run = session.get(ResearchRun, value["id"])
            assert run.details["market_results"]["CN"]["metrics"] is None
            assert len(run.details["market_results"]["US"]["cases"]) == 12
            value["summary"] = "tampered"
            write_json(path, value)
            with pytest.raises(ValueError, match="Immutable"):
                ingest_granville_stock_runs(session, tmp_path)
        finally:
            session.rollback()
            report = session.get(ResearchReport, value["id"] + "-report")
            if report:
                session.delete(report)
                session.flush()
            run = session.get(ResearchRun, value["id"])
            if run:
                session.delete(run)
            session.commit()


def test_stock_candidate_cannot_be_dropped_or_replaced(tmp_path):
    value = fixture_value()
    value["market_results"]["CN"]["cases"].pop()
    write_json(tmp_path / "artifacts/research/granville-stocks/unit/publication.json", value)
    with SessionLocal() as session:
        with pytest.raises(ValueError, match="All fixed"):
            ingest_granville_stock_runs(session, tmp_path)
        session.rollback()
