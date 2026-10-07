from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from deepstock.data.store import write_json
from deepstock.web.database import Base
from deepstock.web.evidence_notices import ingest_evidence_notices, notices_for_run
from deepstock.web.models import ResearchReport, ResearchRun, Strategy
from scripts.capture_wba_terminal_reference import parse_terms

ROOT = Path(__file__).resolve().parents[1]


def test_notice_only_matches_affected_input_version_and_strategy():
    version = "84993cbfd27161f8e2f498a19fb9a4aa6edb66c4ac81376ac4808fae419de4db"
    details = {"data_versions": [{"version": version}]}
    original = deepcopy(details)
    assert len(notices_for_run(ROOT, "granville_stock_portfolio", details)) == 1
    assert notices_for_run(ROOT, "other", details) == []
    assert notices_for_run(ROOT, "granville_stock_portfolio", {"data_versions": ["corrected-data"]}) == []
    assert notices_for_run(ROOT, "granville_stock_portfolio", {"data_versions": [version]})
    assert details == original


def test_notice_report_is_append_only_and_does_not_replace_performance_run(tmp_path):
    import json
    from deepstock.web.ingestion import ingest_catalog
    from deepstock.web.app import _strategy_summary, report_detail
    catalog = json.loads((ROOT / "config/strategy_catalog.json").read_text(encoding="utf-8"))
    write_json(tmp_path / "config/catalog.json", catalog)
    notice_config = json.loads((ROOT / "config/research_evidence_notices.json").read_text(encoding="utf-8"))
    notice = notice_config["notices"][0]
    path = tmp_path / "config/research_evidence_notices.json"
    write_json(path, notice_config)
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            ingest_catalog(session, tmp_path / "config/catalog.json")
            details = {"data_versions": [{"version": notice["affected_data_versions"][0]}], "market_results": {"US": {}, "CN": {}}}
            session.add(ResearchRun(id="synthetic-old-real-evidence", strategy_id="granville_stock_portfolio",
                                    status="completed_with_blocks", run_type="granville_stock_us_sizing",
                                    as_of_date="2026-10-07", details=details))
            session.commit()
            assert ingest_evidence_notices(session, tmp_path) == {"reports": 1}
            assert ingest_evidence_notices(session, tmp_path) == {"reports": 0}
            report = session.get(ResearchReport, notice["id"] + "-report")
            assert report.run_id is None
            display = _strategy_summary(session, session.get(Strategy, "granville_stock_portfolio"))
            assert display["latest_run"]["id"] == "synthetic-old-real-evidence"
            assert display["evidence_notices"][0]["market"] == "US"
            assert session.get(ResearchRun, "synthetic-old-real-evidence").details == details
            original_report = ResearchReport(id="synthetic-original-report", strategy_id="granville_stock_portfolio",
                                              run_id="synthetic-old-real-evidence", report_type="historical", title="Original",
                                              as_of_date="2026-10-07", content="unchanged historical report", content_hash="abc")
            session.add(original_report)
            session.commit()
            payload = report_detail(original_report.id, session, None)
            assert payload["content"] == "unchanged historical report"
            assert payload["evidence_notices"][0]["id"] == notice["id"]
            notice["report_md"] += "\nChanged"
            write_json(path, notice_config)
            with pytest.raises(ValueError, match="Immutable evidence notice"):
                ingest_evidence_notices(session, tmp_path)
    finally:
        engine.dispose()


def test_official_cash_plus_right_terms_never_fabricate_full_proceeds():
    source = "<p>August 28, 2025 cash consideration of $11.45 per WBA share; one non-transferable right; up to an additional $3.00 in cash per WBA share from net proceeds of the future monetization; has ceased trading</p>"
    _, value = parse_terms(source)
    assert value["cash_consideration_usd_per_raw_share"] == 11.45
    assert value["contingent_cash_upper_bound_usd"] == 3.
    assert value["verified_full_proceeds"] is False
    assert value["right_valuation"] is None and value["cash_settlement_date"] is None
    with pytest.raises(ValueError, match="missing"):
        parse_terms(source.replace("one non-transferable right", "cash only"))
