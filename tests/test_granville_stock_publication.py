from copy import deepcopy
import json
from pathlib import Path
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


def test_cross_node_config_requires_same_parameters_not_same_newline_bytes(tmp_path, monkeypatch):
    from scripts import run_granville_portfolio as cli
    cfg = json.loads((Path(__file__).resolve().parents[1] / "config/granville_portfolio_v1.json").read_text())
    config_path = tmp_path / "config.json"
    write_json(config_path, cfg)
    paths = {}
    for market in ["US", "CN"]:
        paths[market] = tmp_path / (market + ".json")
        write_json(paths[market], {"market": market, "config": cfg, "config_hash": ("a" if market == "US" else "b") * 64,
                                   "status": "completed", "data_versions": [], "input_exclusions": []})
    monkeypatch.setattr(cli, "render_report", lambda value: "# Synthetic unit fixture, not performance evidence")
    value = cli.publish(paths["US"], paths["CN"], tmp_path / "publication", config_path)
    assert value["source_config_hashes"] == {"US": "a" * 64, "CN": "b" * 64}
    modified = json.loads(paths["CN"].read_text())
    modified["config"]["initial_capital"] += 1
    write_json(paths["CN"], modified)
    with pytest.raises(ValueError, match="different market/config"):
        cli.publish(paths["US"], paths["CN"], tmp_path / "invalid", config_path)


def correction_fixture(tmp_path):
    from deepstock.data.membership_contract import load_effective_membership_contract
    cfg = json.loads((Path(__file__).resolve().parents[1] / "config/granville_portfolio_v1.json").read_text())
    write_json(tmp_path / "config/granville_portfolio_v1.json", cfg)
    value = fixture_value()
    value["id"] = "granville-stocks-synthetic-correction"
    value["config"] = cfg
    value["publication_scope"] = "US_effective_membership_correction_v1"
    value["market_results"].pop("CN")
    us = value["market_results"]["US"]
    us.update(config=deepcopy(cfg), membership_contract=load_effective_membership_contract()[0],
              fresh_membership_capture_version="a" * 64, price_inventory_version="b" * 64)
    return value


def test_separate_us_correction_preserves_old_run_and_blocked_candidates(tmp_path):
    value = correction_fixture(tmp_path)
    us = value["market_results"]["US"]
    blocked = next(c for c in us["cases"] if c["variant"] == "deviation_reversal" and c["exit_policy"] == "trend_only")
    blocked.update(status="blocked", periods={}, blocking_reason="synthetic unknown terminal proceeds")
    value["status"] = "completed_with_blocks"
    write_json(tmp_path / "artifacts/research/granville-stocks/correction/publication.json", value)
    old_id = "synthetic-old-cn-preservation"
    with SessionLocal() as session:
        original = {"market_results": {"CN": {"metrics": {"annualized_return": -.05}}}}
        session.add(ResearchRun(id=old_id, strategy_id=value["strategy_id"], run_type="synthetic", status="completed", details=deepcopy(original)))
        session.commit()
        try:
            assert ingest_granville_stock_runs(session, tmp_path) == {"runs": 1}
            assert ingest_granville_stock_runs(session, tmp_path) == {"runs": 0}
            new = session.get(ResearchRun, value["id"])
            assert new.run_type == "granville_stock_us_effective_correction"
            assert set(new.details["market_results"]) == {"US"}
            assert len(new.details["market_results"]["US"]["cases"]) == 12
            assert session.get(ResearchRun, old_id).details == original
            from deepstock.web.annualization import annualization_payload
            assert "A股未重跑" in annualization_payload(session, new)["reason"]
        finally:
            session.rollback()
            report = session.get(ResearchReport, value["id"] + "-report")
            if report:
                session.delete(report)
                session.flush()
            for run_id in [value["id"], old_id]:
                run = session.get(ResearchRun, run_id)
                if run:
                    session.delete(run)
            session.commit()


@pytest.mark.parametrize("mutation", ["unflagged", "stale_cn", "changed_config", "unknown_contract", "missing_case", "detailed_audit"])
def test_us_correction_requires_exact_scope_config_contract_and_all_cases(tmp_path, mutation):
    value = correction_fixture(tmp_path)
    if mutation == "unflagged":
        value.pop("publication_scope")
    elif mutation == "stale_cn":
        value["market_results"]["CN"] = fixture_value()["market_results"]["CN"]
    elif mutation == "changed_config":
        value["config"]["initial_capital"] += 1
    elif mutation == "unknown_contract":
        value["market_results"]["US"]["membership_contract"] = {}
    elif mutation == "missing_case":
        value["market_results"]["US"]["cases"].pop()
    else:
        value["market_results"]["US"]["diagnostics"] = {"sizing_audit": []}
    write_json(tmp_path / "artifacts/research/granville-stocks/correction/publication.json", value)
    with SessionLocal() as session:
        with pytest.raises(ValueError):
            ingest_granville_stock_runs(session, tmp_path)
        session.rollback()


def test_correction_publisher_keeps_us_alone_and_provenance(tmp_path, monkeypatch):
    from scripts import run_granville_portfolio as cli
    value = correction_fixture(tmp_path)
    us = value["market_results"]["US"]
    us.update(market="US", status="completed", config_hash="a" * 64, data_versions=[], input_exclusions=[], code_provenance={"base_commit": "synthetic"})
    source = tmp_path / "market_summary.json"
    write_json(source, us)
    monkeypatch.setattr(cli, "render_report", lambda value: "# Synthetic correction only")
    result = cli.publish_corrected_us(source, tmp_path / "published", tmp_path / "config/granville_portfolio_v1.json")
    assert set(result["market_results"]) == {"US"} and result["code_version"] == "synthetic"
    assert result["publication_scope"] == "US_effective_membership_correction_v1"
    us["config"]["initial_capital"] += 1
    write_json(source, us)
    with pytest.raises(ValueError, match="fixed config"):
        cli.publish_corrected_us(source, tmp_path / "invalid", tmp_path / "config/granville_portfolio_v1.json")


def test_export_replaces_per_entry_audits_by_aggregate_counts_without_mutation():
    from scripts.export_granville_portfolio_summary import aggregate_summary
    value = fixture_value()["market_results"]["US"]
    value["diagnostics"] = {"sizing_audit": [{"status": "filled", "date": "synthetic", "budget": 123},
                                               {"status": "capacity_blocked", "symbol": "synthetic"}]}
    value["cases"][0]["diagnostics"] = deepcopy(value["diagnostics"])
    original = deepcopy(value)
    result = aggregate_summary(value)
    assert value == original
    assert "sizing_audit" not in result["diagnostics"]
    audit = result["diagnostics"]["sizing_audit_summary"]
    assert audit["rows"] == 2 and audit["statuses"] == {"filled": 1, "capacity_blocked": 1}
    assert len(audit["content_sha256"]) == 64
    assert result["cases"][0]["diagnostics"]["sizing_audit_summary"] == audit
    assert "budget" not in json.dumps(result) and "capacity_blocked" in json.dumps(result)
