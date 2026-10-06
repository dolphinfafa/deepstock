from copy import deepcopy
from types import SimpleNamespace

import pytest

from deepstock.data.store import write_json
from deepstock.web.annualization import annualization_payload
from deepstock.web.granville_runs import ingest_granville_runs
from deepstock.web.database import SessionLocal
from deepstock.web.models import ResearchRun, ResearchReport
from test_web_platform import client, initialized_database, _login


def publication_fixture():
    # Deliberately make the alternative larger; publishing must not pick it.
    metrics = {"total_return": .01, "annualized_return": .001, "sharpe_ratio": .02, "maximum_drawdown": -.1,
               "annualized_turnover": 2., "average_exposure": .1}
    cases = [{"variant": v, "cost_case": cost, "periods": {"full": {**metrics, "annualized_return": .9 if v == "ma_cross" else .001}}}
             for v in ["ma_cross", "trend_pullback", "deviation_reversal"] for cost in ["base", "stress"]]
    return {"id": "granville-unit-publication", "strategy_id": "granville_ma_swing", "status": "completed",
            "data_start": "2014-01-02", "data_end": "2026-09-29", "as_of_date": "2026-10-06", "config_hash": "f" * 64,
            "config": {"principal_variant": "trend_pullback", "variants": ["ma_cross", "trend_pullback", "deviation_reversal"]},
            "summary": "synthetic publication test only", "report_md": "# Synthetic unit fixture\nNo performance evidence.",
            "market_results": {market: {"metrics": deepcopy(metrics), "cases": deepcopy(cases)} for market in ["US", "CN"]}}


def test_dual_market_annualization_never_merges_currencies():
    run = SimpleNamespace(status="completed", strategy_id="granville_ma_swing", details={"market_results": {"US": {}, "CN": {}}})
    value = annualization_payload(None, run)
    assert value["value"] is None and value["scope"] == "separate_markets"
    assert "USD/CNY" in value["reason"]


def test_publication_retains_principal_all_cases_and_immutable_hash(tmp_path):
    path = tmp_path / "artifacts/research/granville/unit/publication.json"
    value = publication_fixture()
    write_json(path, value)
    with SessionLocal() as session:
        try:
            assert ingest_granville_runs(session, tmp_path) == {"runs": 1}
            assert ingest_granville_runs(session, tmp_path) == {"runs": 0}
            run = session.get(ResearchRun, value["id"])
            assert run.details["market_results"]["US"]["metrics"]["annualized_return"] == .001
            value["summary"] = "changed"
            write_json(path, value)
            with pytest.raises(ValueError, match="Immutable"):
                ingest_granville_runs(session, tmp_path)
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


def test_missing_candidate_cannot_be_published(tmp_path):
    value = publication_fixture()
    value["id"] = "granville-invalid-unit"
    value["market_results"]["CN"]["cases"].pop()
    write_json(tmp_path / "artifacts/research/granville/unit/publication.json", value)
    with SessionLocal() as session:
        with pytest.raises(ValueError, match="All fixed"):
            ingest_granville_runs(session, tmp_path)
        session.rollback()


def test_strategy_api_has_two_markets_or_explicitly_planned_state(client):
    headers, _ = _login(client)
    response = client.get("/api/strategies/granville_ma_swing", headers=headers)
    assert response.status_code == 200
    value = response.json()
    assert value["market"] == "Both" and value["execution_status"] == "research_only_no_orders"
    assert value["live_eligible"] is False
    # CI has no licensed artifacts; successful ingestion is tested above.
    if value["latest_run"]["status"] == "completed":
        assert value["annualization"]["value"] is None
        assert set(value["market_results"]) == {"US", "CN"}
        assert all(len(r["cases"]) == 6 for r in value["market_results"].values())
    else:
        assert value["latest_run"]["status"] == "planned"
