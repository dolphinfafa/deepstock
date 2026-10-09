"""Synthetic accounting and coverage; fixtures are not performance evidence."""
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from deepstock.data.granville_history_audit import attach_window_coverage, planned_windows, security_coverage
from deepstock.data.store import digest, write_json
from deepstock.strategies.both.granville_diagnostics import cost_path_attribution
from deepstock.strategies.us.granville_audit import audit_ledger, verify_metrics
from deepstock.web.granville_audit_reports import ingest_granville_audit_reports, validate_publication
from deepstock.web.models import Base, Metric, ProgressEvent, ResearchReport, ResearchRun, Strategy
from scripts.audit_granville_us_research import verify_artifact


@pytest.fixture
def ledger():
    dates = pd.to_datetime(["2025-12-30", "2025-12-31", "2026-01-02", "2026-01-05", "2026-01-06"])
    trades = pd.DataFrame({"date": dates, "symbol": "A", "action": ["BUY", "SELL", "SELL", "BUY", "SELL"],
                           "reason": ["trend_pullback", "time_exit", "time_exit", "trend_pullback", "signal_exit"],
                           "analytical_units": [10., 4., 6., 5., 1.], "analytical_execution_price": [10.1, 10.2, 10.2, 20.1, 20.4],
                           "charges": 1., "slippage": [1., .4, .6, .5, .1], "cash_after": [898., 937.8, 998., 896.5, 915.9],
                           "held_sessions": [0, 1, 2, 0, 1], "signal_episode": [1, 1, 1, 2, 2]})
    daily = pd.DataFrame({"nav": [998., 999.6, 998., 996.5, 999.9], "cash": trades.cash_after.to_numpy(),
                          "quantity": [10., 6., 0., 5., 4.], "position_count": [1, 1, 0, 1, 1],
                          "commission": 1., "slippage": trades.slippage.to_numpy(), "dividend_tax": 0.,
                          "entry_fill": [1, 0, 0, 1, 0], "exit_fill": [0, 1, 1, 0, 1]}, index=dates)
    prior = daily.nav.shift(1, fill_value=1000.)
    daily["portfolio_net_return"] = daily.nav / prior - 1
    daily["transaction_cost"] = (daily.commission + daily.slippage) / prior
    daily["turnover"] = 0.
    daily["exposure"] = (daily.nav - daily.cash) / daily.nav
    for name in ["receivable", "entry_signal", "blocked_entry", "delayed_exit", "suppressed_same_episode_candidates"]:
        daily[name] = 0
    marks = pd.DataFrame({"A": [10., 10.3, 10.3, 20., 21.]}, index=dates)
    return daily, trades, marks


def test_partial_sales_cycles_year_boundary_cost_flip_and_open_end(ledger):
    daily, trades, marks = ledger
    stats, cycles = audit_ledger(daily, trades, 1000., marks=marks)
    assert stats["closed_positions"] == 1 and stats["open_positions_at_end"] == 1
    assert stats["periods"]["2025"]["closed_positions"] == 0
    assert stats["periods"]["2026_ytd"]["closed_positions"] == 1
    exit = stats["periods"]["full"]["exit_reasons"]["time_exit"]
    assert exit["gross_realized_pnl"] == pytest.approx(3)
    assert exit["net_realized_pnl"] == pytest.approx(-2)
    assert exit["commission"] == pytest.approx(3) and exit["slippage"] == pytest.approx(2)
    assert exit["gross_profit_to_nonpositive_net_count"] == 1
    assert exit["mean_holding_sessions"] == 2
    assert stats["open_positions"]["net_realized_pnl"] == pytest.approx(-.9)
    assert stats["open_positions"]["gross_unrealized_pnl"] == pytest.approx(4)
    assert stats["open_positions"]["unallocated_entry_commission"] == pytest.approx(.8)
    assert stats["all_sells_net_realized_pnl"] == pytest.approx(-2.9)
    assert stats["periods"]["full"]["account_pnl"] == pytest.approx(-.1)
    assert stats["periods"]["2026_ytd"]["opening_nav"] == pytest.approx(999.6)
    assert len(cycles) == 2 and cycles[0]["sell_fills"] == 2


@pytest.mark.parametrize("field", ["cash_after", "daily_cash", "daily_fee", "nav", "units", "holding", "marks", "action", "pyramiding"])
def test_inconsistent_ledger_blocks_without_statistics(ledger, field):
    daily, trades, marks = ledger
    if field == "cash_after":
        trades.loc[0, "cash_after"] += .011
    elif field == "daily_cash":
        daily.iloc[1, daily.columns.get_loc("cash")] += .011
    elif field == "daily_fee":
        daily.iloc[1, daily.columns.get_loc("commission")] += .011
    elif field == "nav":
        daily.iloc[1, daily.columns.get_loc("nav")] += .011
    elif field == "units":
        trades.loc[2, "analytical_units"] += 1
    elif field == "holding":
        trades.loc[2, "held_sessions"] += 1
    elif field == "marks":
        marks.iloc[1, 0] = np.nan
    elif field == "action":
        trades.loc[1, "action"] = "DIVIDEND"
    else:
        trades.loc[1, "action"] = "BUY"
    with pytest.raises(ValueError):
        audit_ledger(daily, trades, 1000., marks=marks)


def test_one_cent_cash_tolerance_and_summary_verification(ledger):
    daily, trades, marks = ledger
    trades.loc[0, "cash_after"] += .009
    stats, _ = audit_ledger(daily, trades, 1000., marks=marks)
    expected = stats["periods"]["full"]["account"]
    verify_metrics(daily, expected)
    with pytest.raises(ValueError, match="metric differs"):
        verify_metrics(daily, {**expected, "total_return": .9})


def test_cost_attribution_retains_changed_real_position_path(ledger):
    daily, trades, marks = ledger
    base, _ = audit_ledger(daily, trades, 1000., marks=marks)
    # A second valid actual path that stays in cash after the first cycle;
    # different fills/positions/costs must all reconcile independently.
    stress_daily, stress_trades, stress_marks = daily.copy(), trades.copy(), marks.copy()
    stress_trades = stress_trades.iloc[:3].copy()
    for column, amount in {"nav": 998., "cash": 998., "quantity": 0., "position_count": 0,
                           "commission": 0., "slippage": 0., "entry_fill": 0, "exit_fill": 0}.items():
        stress_daily.loc[stress_daily.index[3:], column] = amount
    prior = stress_daily.nav.shift(1, fill_value=1000.)
    stress_daily["transaction_cost"] = (stress_daily.commission + stress_daily.slippage) / prior
    stress_daily["portfolio_net_return"] = stress_daily.nav / stress_daily.nav.shift(1, fill_value=1000.) - 1
    stress_daily["exposure"] = (stress_daily.nav - stress_daily.cash) / stress_daily.nav
    stress, _ = audit_ledger(stress_daily, stress_trades, 1000., marks=stress_marks)
    result = cost_path_attribution(base, stress)
    assert result["stress_minus_base_net_return"] == pytest.approx(-.0019)
    assert result["additional_direct_cost_initial_capital_ratio"] == pytest.approx(-.0026)
    assert result["changed_position_path_gross_pnl_initial_capital_ratio"] == pytest.approx(-.0045)
    assert result["stress_minus_base_net_return"] == pytest.approx(
        result["changed_position_path_gross_pnl_initial_capital_ratio"] - result["additional_direct_cost_initial_capital_ratio"])


def test_artifact_hash_and_missing_manifest_pin_block(tmp_path):
    path = tmp_path / "ledger.csv"
    path.write_text("retained synthetic ledger")
    hashes = {path.name: digest(path)}
    assert verify_artifact(tmp_path, path.name, hashes) == path
    path.write_text("changed")
    with pytest.raises(ValueError, match="hash"):
        verify_artifact(tmp_path, path.name, hashes)
    with pytest.raises(ValueError, match="hash"):
        verify_artifact(tmp_path, "unknown.csv", hashes)


def test_membership_gaps_lifetimes_warmup_and_internal_quotes():
    days = pd.bdate_range("2020-01-01", periods=800)
    meta = {"symbol": "A", "assetid": 1, "first_quote": str(days[20].date()), "last_quote": str(days[750].date())}
    member = pd.DataFrame({"date": days[20:751], "weight": 0})
    member.loc[member.date.between(days[350], days[600]), "weight"] = 1
    member = member.loc[member.date.ne(days[400])]
    quotes = days[20:751].difference(days[[100, 200, 450, 700]])
    row, masks, active = security_coverage(days, meta, member, quotes)
    assert row["outside_quote_lifetime_sessions"] == 69
    assert row["member_missing"] == 1
    assert row["pre_member_warmup_missing"] == 2
    assert row["other_internal_missing"] == 1
    assert row["unknown_membership"] == 1 and not active[400]
    assert sum(row[k] for k in ["member_missing", "pre_member_warmup_missing", "other_internal_missing"]) == 4
    assert masks["unknown_membership"][400]
    outside = {**meta, "first_quote": "2030-01-01", "last_quote": None}
    assert security_coverage(days, outside, None, [])[0]["quote_lifetime_outside_scope"]
    with pytest.raises(ValueError, match="outside verified"):
        security_coverage(days, meta, member, days[:30])


def test_fixed_warmup_windows_and_short_tail_never_returns():
    days = pd.bdate_range("2005-01-03", periods=1100)
    plan = planned_windows(days)
    assert plan["warmup"]["sessions"] == 252 and len(plan["windows"]) == 1
    assert plan["windows"][0]["history"]["start"] == str(days[252].date())
    assert plan["windows"][0]["test"]["start"] == str(days[756].date())
    assert plan["tail"]["test"]["sessions"] == 92
    counters = {k: np.zeros(len(days), dtype=int) for k in ["member_missing", "unknown_membership", "verification_failures"]}
    counters["member_missing"][800] = 1
    attach_window_coverage(plan, days, counters)
    assert plan["windows"][0]["status"] == "research_input_blocked"
    assert not plan["windows"][0]["terminal_holdings_verified"]
    assert not planned_windows(days[:500])["windows"]
    assert not planned_windows(days[:800])["windows"]
    assert planned_windows(days[:800])["tail"]["test"]["sessions"] == 44


def publication_fixture(ledger):
    cfg = json.loads((Path(__file__).resolve().parents[1] / "config/granville_portfolio_v1.json").read_text())
    stats, _ = audit_ledger(ledger[0], ledger[1], 1000., marks=ledger[2])
    cases = []
    for v in cfg["variants"]:
        for e in cfg["exit_policies"]:
            for c in ["base", "stress"]:
                blocked = v == "trend_pullback" and e == "trend_only"
                case = {"variant": v, "exit_policy": e, "cost_case": c, "source_status": "blocked" if blocked else "completed",
                        "status": "source_blocked" if blocked else "diagnosed"}
                if blocked:
                    case["blocking_reason"] = "synthetic WBA unknown proceeds"
                else:
                    case.update(diagnostics=deepcopy(stats), artifact_hashes={})
                cases.append(case)
    days = pd.bdate_range("2005-01-03", periods=1100)
    counters = {k: np.zeros(len(days), dtype=int) for k in ["member_missing", "pre_member_warmup_missing", "other_internal_missing", "unknown_membership", "verification_failures"]}
    history = {"start": "2005-01-01", "end": "2026-09-29", "historical_list_count": 1305, "sessions": len(days),
               "outside_quote_lifetime_count": 0, "strategy_run": False, "full_strategy_admitted": False, "prices_filled": False,
               "research_input_status": "blocked", "coverage_totals": {k: 0 for k in counters},
               "plan": attach_window_coverage(planned_windows(days), days, counters),
               "securities": [{"symbol": f"S{i}", "assetid": i, "first_quote": "2005-01-01", "last_quote": None, "status": "coverage_checked"} for i in range(1305)],
               "execution_evidence_gaps": ["synthetic evidence missing"], "terminal_evidence_gaps": [],
               **{k: "a" * 64 for k in ["membership_version", "inventory_version", "membership_manifest_sha256", "inventory_manifest_sha256", "calendar_sha256"]}}
    return {"id": "synthetic-granville-audit", "strategy_id": "granville_stock_portfolio", "report_type": "granville_us_ledger_history_audit",
            "as_of_date": "2026-10-09", "source_run_id": "synthetic-correction", "source_run_sha256": "b" * 64,
            "source_summary_sha256": "c" * 64, "cycles_sha256": "d" * 64, "config": cfg,
            "code_provenance": {"base_commit": "synthetic", "source_sha256": "e" * 64}, "captured_at_utc": "synthetic",
            "ledgers": {"cases": cases, "cost_path_pairs": [{"variant": v, "exit_policy": e, "status": "blocked"} for v in cfg["variants"] for e in cfg["exit_policies"]]},
            "history": history, "diagnostic_only": True, "new_backtest": False, "paper_authorized": False, "live_authorized": False}


@pytest.mark.parametrize("mutation", ["missing_case", "missing_security", "price_padding", "new_backtest", "cycles", "executed_window"])
def test_publication_rejects_dropped_cases_survivors_or_restricted_rows(ledger, mutation):
    value = publication_fixture(ledger)
    if mutation == "missing_case":
        value["ledgers"]["cases"].pop()
    elif mutation == "missing_security":
        value["history"]["securities"].pop()
    elif mutation == "price_padding":
        value["history"]["prices_filled"] = True
    elif mutation == "new_backtest":
        value["new_backtest"] = True
    elif mutation == "cycles":
        value["ledgers"]["cases"][0]["diagnostics"]["cycles"] = []
    else:
        value["history"]["plan"]["windows"][0]["strategy_run"] = True
    with pytest.raises(ValueError):
        validate_publication(value, value["config"])


def test_report_only_immutable_idempotent_import_preserves_performance(ledger, tmp_path):
    value = publication_fixture(ledger)
    write_json(tmp_path / "config/granville_portfolio_v1.json", value["config"])
    path = tmp_path / "artifacts/research/granville-us-audits/synthetic/publication.json"
    write_json(path, value)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    original = {"cases": [{**{k: c[k] for k in ["variant", "exit_policy", "cost_case"]}, "status": c["source_status"],
                           "blocking_reason": c.get("blocking_reason"), "periods": {"full": c["diagnostics"]["periods"]["full"]["account"]} if c["status"] == "diagnosed" else {}}
                          for c in value["ledgers"]["cases"]], "artifact_hashes": {},
                "fresh_membership_capture_version": "a" * 64, "price_inventory_version": "a" * 64, "original_summary_sha256": "c" * 64}
    with Session(engine) as session:
        session.add(Strategy(id=value["strategy_id"], code="UNIT", display_name="Synthetic", market="Both", asset_class="stock",
                             summary="Synthetic", thesis_md="Synthetic", status="research_only", execution_status="research_only", current_version="unit", spec_path="synthetic"))
        session.add(ResearchRun(id=value["source_run_id"], strategy_id=value["strategy_id"], run_type="granville_stock_us_effective_correction", status="completed_with_blocks", source_hash="b" * 64, details={"market_results": {"US": original}}))
        session.add(ResearchReport(id="old-cn", strategy_id=value["strategy_id"], title="Synthetic CN", report_type="history", as_of_date="2026-10-06", content="unchanged", content_hash="f" * 64))
        session.commit()
        before = deepcopy(session.get(ResearchRun, value["source_run_id"]).details)
        assert ingest_granville_audit_reports(session, tmp_path) == {"reports": 1}
        assert ingest_granville_audit_reports(session, tmp_path) == {"reports": 0}
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 1
        assert session.scalar(select(func.count()).select_from(Metric)) == 0
        assert session.scalar(select(func.count()).select_from(ProgressEvent)) == 1
        assert session.scalar(select(ProgressEvent)).status == "blocked"
        assert session.get(ResearchRun, value["source_run_id"]).details == before
        assert session.get(ResearchReport, "old-cn").content == "unchanged"
        report = session.get(ResearchReport, value["id"])
        assert report.run_id == value["source_run_id"] and "仅诊断，未做新回测" in report.content
        value["captured_at_utc"] = "mutated provenance not visible in tables"
        write_json(path, value)
        with pytest.raises(ValueError, match="Immutable"):
            ingest_granville_audit_reports(session, tmp_path)
