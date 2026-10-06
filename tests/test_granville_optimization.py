from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from deepstock.data.store import write_json
from deepstock.strategies.both.granville_portfolio import run_stock_portfolio, signal_episodes
from deepstock.strategies.both.granville_diagnostics import ledger_diagnostics, cost_path_attribution, validate_optimization
from deepstock.web.database import SessionLocal
from deepstock.web.granville_optimization_runs import ingest_granville_optimization_runs
from deepstock.web.models import ResearchRun, ResearchReport
from test_granville_portfolio import panel_fixture
from test_granville_stock_publication import fixture_value
from test_web_platform import initialized_database


ROOT = Path(__file__).resolve().parents[1]


def test_episode_numbers_are_causal_and_rearm_only_after_a_false_close():
    a = np.array([[False], [True], [True], [False], [True], [True]])
    assert signal_episodes(a)[:, 0].tolist() == [0, 1, 1, 1, 2, 2]
    np.testing.assert_array_equal(signal_episodes(a)[:4], signal_episodes(a[:4]))


@pytest.mark.parametrize("market", ["US", "CN"])
def test_once_per_episode_reduces_reentry_without_changing_exits(market):
    p, cfg, rule = panel_fixture(market)
    a, ta, _ = run_stock_portfolio(p, cfg, rule, market)
    b, tb, _ = run_stock_portfolio(p, cfg, rule, market, entry_policy="once_per_episode")
    assert len(ta.loc[ta.action.eq("BUY")]) > len(tb.loc[tb.action.eq("BUY")])
    assert len(tb.loc[tb.action.eq("BUY")]) == len(p.symbols)
    assert not tb.loc[tb.action.eq("BUY")].duplicated(["symbol", "signal_episode"]).any()
    assert tb.loc[tb.action.eq("SELL")].held_sessions.eq(7).all()
    assert b.suppressed_same_episode_candidates.sum() > 0
    assert tb.signal_date.lt(tb.date).all()
    assert b.cash.min() >= 0 and b.position_count.max() == 5
    d = ledger_diagnostics(b, tb, cfg["initial_capital"])
    assert d["same_episode_repeat_buys"] == 0 and d["closed_positions"] == 8


def test_episode_rearms_without_changing_cooldown_or_allowing_same_open_reentry():
    p, cfg, rule = panel_fixture()
    p.signals["trend_pullback"][230, 0] = False
    d, t, _ = run_stock_portfolio(p, cfg, rule, "US", entry_policy="once_per_episode")
    buys = t.loc[t.action.eq("BUY") & t.symbol.eq("A")]
    assert buys.signal_episode.tolist() == [1, 2]
    assert buys.date.iloc[1] == str(p.dates[232].date())


def test_blocked_or_slot_unavailable_entry_does_not_consume_episode():
    p, cfg, rule = panel_fixture("CN")
    p.tradable[220:225, 0] = False
    d, t, _ = run_stock_portfolio(p, cfg, rule, "CN", entry_policy="once_per_episode")
    bought = t.loc[t.action.eq("BUY") & t.symbol.eq("A")]
    assert len(bought) == 1 and bought.date.iloc[0] > str(p.dates[224].date())


def test_portfolio_episode_rule_is_prefix_invariant():
    p, cfg, rule = panel_fixture()
    p.signals["trend_pullback"][230:232] = False
    full, trades, _ = run_stock_portfolio(p, cfg, rule, "US", entry_policy="once_per_episode")
    short = deepcopy(p)
    short.dates = p.dates[:245]
    short.values = {k: v[:245] for k, v in p.values.items()}
    short.signals = {k: v[:245] for k, v in p.signals.items()}
    short.eligible, short.tradable, short.documented_suspension = p.eligible[:245], p.tradable[:245], p.documented_suspension[:245]
    short_cfg = {**cfg, "evaluation_end": str(short.dates[-1].date())}
    prefix, prefix_trades, _ = run_stock_portfolio(short, short_cfg, rule, "US", entry_policy="once_per_episode")
    pd.testing.assert_frame_equal(prefix, full.loc[:short.dates[-1]])
    pd.testing.assert_frame_equal(prefix_trades, trades.loc[trades.date.le(short_cfg["evaluation_end"])].reset_index(drop=True))


def test_cost_cash_identity_does_not_treat_all_stress_difference_as_fees():
    b = {"cash_identity": {"net_return": .1, "direct_cost_initial_capital_ratio": .03, "gross_market_pnl_initial_capital_ratio": .13}}
    s = {"cash_identity": {"net_return": .04, "direct_cost_initial_capital_ratio": .05, "gross_market_pnl_initial_capital_ratio": .09}}
    a = cost_path_attribution(b, s)
    assert a["stress_minus_base_net_return"] == pytest.approx(-.06)
    assert a["additional_direct_cost_initial_capital_ratio"] == pytest.approx(.02)
    assert a["changed_position_path_gross_pnl_initial_capital_ratio"] == pytest.approx(-.04)


def test_partial_sells_count_one_closed_position_and_open_holdings_are_not_liquidated():
    p, cfg, rule = panel_fixture()
    p.values["volume"][225:229] = 10000
    daily, trades, _ = run_stock_portfolio(p, cfg, rule, "US", entry_policy="once_per_episode")
    diagnostics = ledger_diagnostics(daily, trades, cfg["initial_capital"])
    assert diagnostics["sell_fills_including_partial"] > diagnostics["closed_positions"]
    assert diagnostics["closed_positions"] + diagnostics["open_positions_at_end"] == diagnostics["buy_fills"]


def optimization_fixture():
    value = fixture_value()
    value["id"] = "granville-episodes-unit"
    value["experiment"] = json.loads((ROOT / "config/granville_portfolio_episodes_v2.json").read_text())
    for result in value["market_results"].values():
        metrics = result["metrics"]
        result["optimization"] = {"cases": [{"entry_policy": p, "cost_case": c, "status": "completed", "baseline_verified": p == "signal_level",
                                                "periods": {"full": {**metrics, "annualized_return": .9 if p == "once_per_episode" else metrics["annualized_return"]}}}
                                               for p in ["signal_level", "once_per_episode"] for c in ["base", "stress"]]}
    return value


def test_publication_retains_all_cases_and_cannot_promote_the_larger_return(tmp_path):
    value = optimization_fixture()
    validate_optimization(value)
    changed = deepcopy(value)
    changed["market_results"]["CN"]["metrics"]["annualized_return"] = .9
    with pytest.raises(ValueError, match="replace baseline"):
        validate_optimization(changed)
    changed = deepcopy(value)
    changed["market_results"]["US"]["optimization"]["cases"].pop()
    with pytest.raises(ValueError, match="All fixed"):
        validate_optimization(changed)
    changed = deepcopy(value)
    changed["market_results"]["US"]["optimization"]["cases"][0]["baseline_verified"] = False
    with pytest.raises(ValueError, match="independently"):
        validate_optimization(changed)


def test_optimization_ingestion_is_immutable_and_retains_original_principal(tmp_path):
    value = optimization_fixture()
    path = tmp_path / "artifacts/research/granville-stock-optimizations/unit/publication.json"
    write_json(path, value)
    with SessionLocal() as session:
        try:
            assert ingest_granville_optimization_runs(session, tmp_path) == {"runs": 1}
            assert ingest_granville_optimization_runs(session, tmp_path) == {"runs": 0}
            run = session.get(ResearchRun, value["id"])
            assert run.run_type == "granville_stock_entry_episodes"
            assert run.details["market_results"]["CN"]["metrics"]["annualized_return"] == .001
            value["summary"] = "changed"
            write_json(path, value)
            with pytest.raises(ValueError, match="Immutable"):
                ingest_granville_optimization_runs(session, tmp_path)
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
