from copy import deepcopy
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from deepstock.strategies.both.granville_portfolio import make_panel, run_stock_portfolio, sizing_reference
from deepstock.strategies.us.granville_sizing import validate_sizing, validate_sizing_config
from test_granville_portfolio import panel_fixture, inputs
from test_granville_stock_publication import fixture_value
from deepstock.data.store import write_json
from deepstock.web.database import SessionLocal
from deepstock.web.granville_stock_runs import ingest_granville_stock_runs
from deepstock.web.models import ResearchRun, ResearchReport
from test_web_platform import initialized_database

ROOT = Path(__file__).resolve().parents[1]


def sized_panel():
    p, cfg, rule = panel_fixture()
    p.values["sizing_volatility"][:] = np.array([.01, .02, .03, .04, .10, .02, .02, .02])
    return p, cfg, rule


def test_close_volatility_is_contiguous_ddof_zero_and_prefix_invariant():
    bars, dates, members, div, cfg, rule = inputs()
    p = make_panel(bars, dates, members, "US", rule, cfg)
    expected = pd.Series(np.linspace(10, 15, len(dates))).pct_change(fill_method=None).rolling(20, min_periods=20).std(ddof=0)
    np.testing.assert_allclose(p.values["sizing_volatility"][:, 0], expected, equal_nan=True)
    short = make_panel(bars.loc[bars.date.le(dates[250])], dates[:251], members, "US", rule, cfg)
    np.testing.assert_allclose(p.values["sizing_volatility"][:251], short.values["sizing_volatility"], equal_nan=True)


def test_median_uses_historical_ready_liquid_pool_not_buy_list_or_future_members():
    p, cfg, _ = sized_panel()
    p.eligible[219, 4] = False
    p.signals["ready"][219, 3] = False
    p.values["average_turnover"][219, 2] = 0
    p.signals["trend_pullback"][219, 1] = False
    p.values["sizing_volatility"][219, 0] = np.nan
    ref, n = sizing_reference(p, cfg, "US", 219)
    assert ref == .02 and n == 4


def test_shrink_only_and_prior_close_no_rebalance_or_pyramiding():
    p, cfg, rule = sized_panel()
    # Entry-day volatility must not affect that day's target.
    p.values["sizing_volatility"][220, :] = 50
    d, t, s = run_stock_portfolio(p, cfg, rule, "US", exit_policy="trend_only", sizing_policy="volatility_shrink_only")
    targets = [a["target_weight"] for a in s["sizing_audit"]]
    np.testing.assert_allclose(targets, [.16, .16, .16 * .02 / .03, .08, .032])
    assert len(t) == 5 and t.action.eq("BUY").all()
    assert d.position_count.max() == 5 and d.exposure.iloc[0] < .8 and d.cash.min() > 0
    assert all(a["signal_date"] < a["date"] for a in s["sizing_audit"])


@pytest.mark.parametrize("bad", [0., np.nan, np.inf])
def test_invalid_volatility_blocks_and_records_instead_of_imputing(bad):
    p, cfg, rule = sized_panel()
    p.values["sizing_volatility"][:, 0] = bad
    _, t, s = run_stock_portfolio(p, cfg, rule, "US", sizing_policy="volatility_shrink_only")
    assert "A" not in t.symbol.tolist()
    assert s["invalid_sizing_candidates"] > 0
    assert any(a["status"] == "blocked_invalid_volatility" for a in s["sizing_audit"])


def test_empty_reference_blocks_all_and_cn_experiment_is_rejected():
    p, cfg, rule = sized_panel()
    p.values["sizing_volatility"][:] = 0
    d, t, s = run_stock_portfolio(p, cfg, rule, "US", sizing_policy="volatility_shrink_only")
    assert t.empty and d.cash_fraction.eq(1).all()
    with pytest.raises(ValueError, match="non-US"):
        run_stock_portfolio(p, cfg, rule, "CN", sizing_policy="volatility_shrink_only")


def test_default_fixed_sizing_remains_identical():
    p, cfg, rule = sized_panel()
    a, ta, _ = run_stock_portfolio(p, cfg, rule, "US")
    b, tb, _ = run_stock_portfolio(p, cfg, rule, "US", sizing_policy="fixed_16pct")
    pd.testing.assert_frame_equal(a, b)
    pd.testing.assert_frame_equal(ta, tb)


def test_shrink_portfolio_has_no_future_dependency():
    p, cfg, rule = sized_panel()
    a, ta, _ = run_stock_portfolio(p, cfg, rule, "US", sizing_policy="volatility_shrink_only")
    future = deepcopy(p)
    for k in ["sizing_volatility", "strength", "average_turnover"]:
        future.values[k][245:] *= 100
    future.eligible[245:] = False
    b, tb, _ = run_stock_portfolio(future, cfg, rule, "US", sizing_policy="volatility_shrink_only")
    pd.testing.assert_frame_equal(a.iloc[:25], b.iloc[:25])
    pd.testing.assert_frame_equal(ta.loc[ta.date.lt(str(p.dates[245].date()))].reset_index(drop=True),
                                  tb.loc[tb.date.lt(str(p.dates[245].date()))].reset_index(drop=True))


def sizing_fixture():
    v = fixture_value()
    exp = json.loads((ROOT / "config/granville_us_sizing_v3.json").read_text())
    v["experiment"] = exp
    v["market_results"]["CN"]["current_experiment_status"] = "not_rerun_historical_evidence"
    us = v["market_results"]["US"]
    us["sizing_experiment"] = {"experiment": exp, "cases": []}
    for p in exp["sizing_policies"]:
        for cost in exp["cost_cases"]:
            old = next(c for c in us["cases"] if (c["variant"], c["exit_policy"], c["cost_case"]) == ("trend_pullback", "time_7", cost))
            us["sizing_experiment"]["cases"].append({"sizing_policy": p, "cost_case": cost, "status": "completed",
                                                      "baseline_verified": True, "verified_baseline_artifact_hashes": {"daily": "abc", "trades": "def"},
                                                      "periods": deepcopy(old["periods"])})
    return v


def test_publication_cannot_drop_case_promote_winner_or_relabel_cn():
    v = sizing_fixture()
    validate_sizing(v)
    for mutation in ["drop", "winner", "cn", "verify"]:
        changed = deepcopy(v)
        cases = changed["market_results"]["US"]["sizing_experiment"]["cases"]
        if mutation == "drop":
            cases.pop()
        elif mutation == "winner":
            changed["market_results"]["US"]["metrics"]["annualized_return"] = .9
        elif mutation == "cn":
            changed["market_results"]["CN"]["sizing_experiment"] = {}
        else:
            cases[0]["baseline_verified"] = False
        with pytest.raises(ValueError):
            validate_sizing(changed)
    cfg = deepcopy(v["experiment"])
    cfg["volatility_sessions"] = 10
    with pytest.raises(ValueError):
        validate_sizing_config(cfg)


def test_sizing_ingestion_preserves_both_history_and_is_immutable(tmp_path):
    value = sizing_fixture()
    value["id"] = "granville-sizing-synthetic-fixture"
    path = tmp_path / "artifacts/research/granville-stock-sizing/unit/publication.json"
    write_json(path, value)
    with SessionLocal() as session:
        try:
            assert ingest_granville_stock_runs(session, tmp_path, subdirectory="granville-stock-sizing", sizing=True) == {"runs": 1}
            assert ingest_granville_stock_runs(session, tmp_path, subdirectory="granville-stock-sizing", sizing=True) == {"runs": 0}
            run = session.get(ResearchRun, value["id"])
            assert run.run_type == "granville_stock_us_sizing"
            assert run.details["market_results"]["CN"]["current_experiment_status"] == "not_rerun_historical_evidence"
            value["summary"] = "changed"
            write_json(path, value)
            with pytest.raises(ValueError, match="Immutable"):
                ingest_granville_stock_runs(session, tmp_path, subdirectory="granville-stock-sizing", sizing=True)
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


@pytest.fixture
def catalog_only_session():
    # Display regressions must not depend on whichever real publications happen
    # to exist in this node's ignored artifacts directory.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from deepstock.web.database import Base
    from deepstock.web.ingestion import ingest_catalog
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            ingest_catalog(session)
            yield session
    finally:
        engine.dispose()


def test_new_sizing_plan_keeps_completed_both_evidence_in_latest_display(catalog_only_session):
    from deepstock.web.app import _strategy_summary
    from deepstock.web.ingestion import ingest_catalog
    from deepstock.web.models import Strategy
    value = fixture_value()
    run_id = "granville-synthetic-latest-evidence"
    session = catalog_only_session
    session.add(ResearchRun(id=run_id, strategy_id="granville_stock_portfolio", run_type="granville_stock_entry_episodes",
                            status="completed_with_blocks", as_of_date="2026-10-06", details=value))
    session.commit()
    ingest_catalog(session)
    display = _strategy_summary(session, session.get(Strategy, "granville_stock_portfolio"))
    assert display["latest_run"]["id"] == run_id
    assert set(display["market_results"]) == {"US", "CN"}


def test_completed_sizing_publication_replaces_older_evidence_not_baseline_headline(catalog_only_session):
    from deepstock.web.app import _strategy_summary
    from deepstock.web.ingestion import ingest_catalog
    from deepstock.web.models import Strategy
    session = catalog_only_session
    old, new = fixture_value(), sizing_fixture()
    for run_id, as_of, value, run_type in [
        ("granville-synthetic-old-evidence", "2026-10-06", old, "granville_stock_entry_episodes"),
        ("granville-synthetic-completed-sizing", "2026-10-07", new, "granville_stock_us_sizing"),
    ]:
        session.add(ResearchRun(id=run_id, strategy_id="granville_stock_portfolio", run_type=run_type,
                                status="completed_with_blocks", as_of_date=as_of, details=value))
    session.commit()
    ingest_catalog(session)
    display = _strategy_summary(session, session.get(Strategy, "granville_stock_portfolio"))
    assert display["latest_run"]["id"] == "granville-synthetic-completed-sizing"
    assert display["market_results"]["US"]["sizing_experiment"] == new["market_results"]["US"]["sizing_experiment"]
    assert display["market_results"]["US"]["metrics"] == old["market_results"]["US"]["metrics"]
    assert display["market_results"]["CN"]["current_experiment_status"] == "not_rerun_historical_evidence"
