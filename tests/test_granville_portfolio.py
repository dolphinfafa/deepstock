from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from deepstock.data.store import clean_frame
from deepstock.data.stock_actions import reconcile_stock_actions
from deepstock.strategies.both.granville_portfolio import make_panel, run_stock_portfolio, PortfolioDataError


ROOT = Path(__file__).resolve().parents[1]


def inputs(market="US", n=300):
    cfg = json.loads((ROOT / "config/granville_portfolio_v1.json").read_text())
    rule = json.loads((ROOT / "config/granville_v1.json").read_text())["signal"]
    dates = pd.bdate_range("2024-01-02", periods=n)
    cfg["evaluation_start"], cfg["evaluation_end"] = str(dates[220].date()), str(dates[269].date())
    cfg["markets"][market]["minimum_average_turnover"] = 100
    rows = []
    for symbol in list("ABCDEFGH"):
        close = np.linspace(10., 15., n)
        for i, day in enumerate(dates):
            rows.append({"date": day, "symbol": symbol, "open": close[i], "close": close[i], "high": close[i] + .1,
                         "low": close[i] - .1, "adjusted_open": close[i], "adjusted_high": close[i] + .1,
                         "adjusted_low": close[i] - .1, "adjusted_close": close[i], "volume": 1e8, "turnover": 1e9,
                         "up_limit": close[i] * 1.1, "down_limit": close[i] * .9, "adj_factor": 1.})
    members = pd.DataFrame([{"symbol": s, "date": dates[0], "end": dates[-1], "weight": 1} for s in list("ABCDEFGH")])
    dividends = pd.DataFrame(columns=["ts_code", "ann_date", "div_proc", "ex_date", "cash_div_tax"])
    return pd.DataFrame(rows), dates, members, dividends, cfg, rule


def panel_fixture(market="US"):
    bars, dates, members, dividends, cfg, rule = inputs(market)
    p = make_panel(bars, dates, members, market, rule, cfg, dividends=dividends)
    for name in ["ma_cross", "trend_pullback", "deviation_reversal", "ready"]:
        p.signals[name][:] = True
    p.signals["trend_exit"][:] = False
    p.signals["reversion_exit"][:] = False
    p.values["strength"][:] = 1.
    return p, cfg, rule


@pytest.mark.parametrize("market", ["US", "CN"])
def test_five_positions_lots_caps_and_no_daily_rank_churn(market):
    p, cfg, rule = panel_fixture(market)
    daily, trades, summary = run_stock_portfolio(p, cfg, rule, market, exit_policy="trend_only")
    assert daily.position_count.eq(5).all()
    assert len(trades) == 5 and trades.action.eq("BUY").all()
    assert trades.symbol.tolist() == list("ABCDE")
    assert (trades.entry_raw_shares_reference % cfg["markets"][market]["lot_size"]).eq(0).all()
    assert trades.signal_date.lt(trades.date).all()
    assert daily.cash.min() >= 0 and daily.exposure.iloc[0] <= .8
    assert summary["maximum_holding_sessions"] > 7
    assert summary["quantity_unit"] == "analytical_total_return_units_not_broker_shares"


def test_only_actual_sale_frees_slot_for_prior_ranked_replacement():
    p, cfg, rule = panel_fixture("CN")
    j = p.symbols.index("A")
    p.signals["trend_exit"][222, j] = True
    p.values["down_limit"][223, j] = p.values["open"][223, j]
    daily, trades, summary = run_stock_portfolio(p, cfg, rule, "CN", exit_policy="trend_only")
    assert daily.iloc[3].delayed_exit == 1
    assert not trades.date.eq(str(p.dates[223].date())).any()
    sold = trades.query("action == 'SELL'").iloc[0]
    assert sold.symbol == "A" and sold.date == str(p.dates[224].date())
    bought = trades.query("action == 'BUY'").iloc[-1]
    assert bought.symbol == "F" and bought.date == sold.date
    assert daily.position_count.max() == 5


def test_close_risk_stop_has_priority_over_minimum_hold_and_cn_t_plus_one():
    p, cfg, rule = panel_fixture("CN")
    p.values["adjusted_close"][220, 0] *= .8
    daily, trades, _ = run_stock_portfolio(p, cfg, rule, "CN", exit_policy="trend_only")
    sale = trades.query("action == 'SELL'").iloc[0]
    assert sale.reason == "close_stop" and sale.held_sessions == 1
    assert sale.date == str(p.dates[221].date())


def test_two_fixed_exits_and_cost_stress_never_choose_winner():
    p, cfg, rule = panel_fixture()
    seven, t7, _ = run_stock_portfolio(p, cfg, rule, "US", exit_policy="time_7")
    trend, tt, _ = run_stock_portfolio(p, cfg, rule, "US", exit_policy="trend_only")
    stress, _, _ = run_stock_portfolio(p, cfg, rule, "US", exit_policy="trend_only", stress=True)
    assert t7.query("action == 'SELL'").held_sessions.iloc[0] == 7
    assert len(tt) == 5 and len(t7) > len(tt)
    assert stress.portfolio_equity.iloc[-1] < trend.portfolio_equity.iloc[-1]
    assert cfg["principal_exit_policy"] == "time_7"


def test_prefix_invariance_of_signals_and_portfolio():
    bars, dates, members, dividend, cfg, rule = inputs()
    full = make_panel(bars, dates, members, "US", rule, cfg)
    short = make_panel(bars.loc[bars.date.le(dates[269])], dates[:270], members, "US", rule, cfg)
    for key in full.signals:
        np.testing.assert_array_equal(full.signals[key][:270], short.signals[key])
    a, ta, _ = run_stock_portfolio(full, cfg, rule, "US")
    b, tb, _ = run_stock_portfolio(short, cfg, rule, "US")
    pd.testing.assert_frame_equal(a, b)
    pd.testing.assert_frame_equal(ta, tb)


def test_unexplained_gap_and_terminal_proceeds_cannot_be_silently_filled():
    bars, dates, members, dividend, cfg, rule = inputs()
    with pytest.raises(PortfolioDataError, match="Unexplained"):
        make_panel(bars.loc[~(bars.symbol.eq("A") & bars.date.eq(dates[225]))], dates, members, "US", rule, cfg)
    p, cfg, rule = panel_fixture()
    p.values["adjusted_close"][228:, 0] = np.nan
    p.values["adjusted_open"][228:, 0] = np.nan
    p.terminal["A"] = str(dates[227].date())
    with pytest.raises(PortfolioDataError, match="verified proceeds"):
        run_stock_portfolio(p, cfg, rule, "US", exit_policy="trend_only")


def test_dividend_tax_only_for_pre_ex_date_holdings_not_explicit_double_cash():
    p, cfg, rule = panel_fixture("CN")
    p.values["cash_dividend_tax_per_raw_share"][221, 0] = .2
    p.values["cash_dividend_tax_per_raw_share"][220, 7] = .2  # Unheld stock.
    d, t, s = run_stock_portfolio(p, cfg, rule, "CN", exit_policy="trend_only")
    shares = t.iloc[0].entry_raw_shares_reference
    assert d.loc[p.dates[221], "dividend_tax"] == pytest.approx(shares * .2)
    assert d.iloc[0].dividend_tax == 0
    assert d.receivable.eq(0).all()  # Economic-unit model, not a payout ledger.


def test_provider_null_unimplemented_plans_are_evidence_not_fake_events():
    rows = [{"ts_code": "000963.SZ", "ann_date": np.nan, "div_proc": "预披露", "ex_date": np.nan, "cash_div_tax": 0., "stk_div": 0.},
            {"ts_code": "000963.SZ", "ann_date": 20250501., "div_proc": "实施", "ex_date": 20250602., "cash_div_tax": .35, "stk_div": 0.}]
    clean, rejected, q, kind = clean_frame(pd.DataFrame(rows), {"endpoint": "dividend", "market": "CN"})
    assert not q["blocking"] and len(clean) == 2 and q["imputed_rows"] == 0
    assert pd.isna(clean.iloc[0].ann_date)
    rows[1]["ann_date"] = np.nan
    assert clean_frame(pd.DataFrame(rows), {"endpoint": "dividend", "market": "CN"})[2]["blocking"]
    rows[1]["imp_ann_date"] = 20250510.
    clean, _, q, _ = clean_frame(pd.DataFrame(rows), {"endpoint": "dividend", "market": "CN"})
    assert not q["blocking"] and pd.isna(clean.iloc[1].ann_date)


def test_actual_limit_schema_and_verified_empty_suspension_response():
    limits = pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20260102", "up_limit": 11, "down_limit": 9}])
    clean, rejected, q, kind = clean_frame(limits, {"endpoint": "stk_limit", "market": "CN"})
    assert not q["blocking"] and kind == "daily_limits"
    empty = pd.DataFrame(columns=["ts_code", "trade_date", "suspend_type", "suspend_timing"])
    assert not clean_frame(empty, {"endpoint": "suspend_d", "empty_response_verified": True})[2]["blocking"]
    assert clean_frame(empty, {"endpoint": "suspend_d"})[2]["blocking"]


@pytest.mark.parametrize("amounts,actual", [([1., 1.4], 1.4), ([1.25, .31], 1.56), ([.5, .5], .5)])
def test_reported_aggregate_or_multiple_periods_must_match_independent_factor(amounts, actual):
    bars = pd.DataFrame({"symbol": ["A", "A"], "date": pd.to_datetime(["2025-01-02", "2025-01-03"]),
                         "close": [10., 10.], "adj_factor": [1., 10 / (10 - actual)]})
    declarations = pd.DataFrame([{"ts_code": "A", "ann_date": "2024-12-25", "ex_date": "2025-01-03", "div_proc": "实施",
                                  "cash_div_tax": amount, "stk_div": 0.} for amount in amounts])
    clean, audit = reconcile_stock_actions(bars, declarations, pd.Timestamp("2025-01-02"), pd.Timestamp("2025-01-03"))
    assert len(clean) == 1 and clean.cash_div_tax.iloc[0] == pytest.approx(actual)
    assert audit[0]["disclosure_count"] == len(amounts)
    declarations.loc[0, "cash_div_tax"] = np.nan
    with pytest.raises(ValueError, match="Unresolved dividend"):
        reconcile_stock_actions(bars, declarations, pd.Timestamp("2025-01-02"), pd.Timestamp("2025-01-03"))


def test_declared_fiscal_revision_not_unrelated_amount_or_same_day_conflict():
    rows = [{"ts_code": "A", "end_date": "2024-06-30", "ann_date": "2024-08-29", "ex_date": "2024-11-07", "div_proc": "实施", "cash_div_tax": .4, "stk_div": 0.},
            {"ts_code": "A", "end_date": "2024-06-30", "ann_date": "2024-10-30", "ex_date": "2024-11-07", "div_proc": "实施", "cash_div_tax": 1.4, "stk_div": 0.}]
    clean, _, q, _ = clean_frame(pd.DataFrame(rows), {"endpoint": "dividend"})
    assert not q["blocking"] and len(clean) == 2
    assert q["alternative_entitlement_rows"] == 2
    rows[1]["ann_date"] = rows[0]["ann_date"]
    assert clean_frame(pd.DataFrame(rows), {"endpoint": "dividend"})[2]["blocking"]
    rows[1]["ann_date"] = "2024-10-30"
    del rows[0]["end_date"]
    del rows[1]["end_date"]
    assert clean_frame(pd.DataFrame(rows), {"endpoint": "dividend"})[2]["alternative_entitlement_rows"] == 2


def test_open_ended_documented_suspension_is_not_a_fictitious_terminal_sale():
    p, cfg, rule = panel_fixture()
    p.values["adjusted_close"][260:, 0] = np.nan
    p.values["adjusted_open"][260:, 0] = np.nan
    p.tradable[260:, 0] = False
    p.documented_suspension[260:, 0] = True
    daily, trades, summary = run_stock_portfolio(p, cfg, rule, "US", exit_policy="trend_only")
    assert daily.iloc[-1].stale_position_marks == 1
    assert not trades.query("action == 'SELL'").symbol.eq("A").any()
    assert summary["stale_position_marks"] == 10


def test_unknown_cash_entitlement_blocks_only_a_genuinely_held_strategy():
    p, cfg, rule = panel_fixture("CN")
    p.values["cash_dividend_tax_per_raw_share"][225, 7] = np.nan
    daily, _, _ = run_stock_portfolio(p, cfg, rule, "CN", exit_policy="trend_only")
    assert np.isfinite(daily.portfolio_equity).all()
    p.values["cash_dividend_tax_per_raw_share"][225, 0] = np.nan
    with pytest.raises(PortfolioDataError, match="entitlement unspecified"):
        run_stock_portfolio(p, cfg, rule, "CN", exit_policy="trend_only")


def test_nominal_amount_not_inferred_from_rounded_or_diluted_reference():
    bars = pd.DataFrame({"symbol": ["A", "A"], "date": pd.to_datetime(["2025-01-02", "2025-01-03"]),
                         "close": [41.77, 40.], "adj_factor": [198.944, 210.697], "pre_close": [41.77, 39.44]})
    declarations = pd.DataFrame([{"ts_code": "A", "ann_date": "2024-12-25", "ex_date": "2025-01-03", "div_proc": "实施", "cash_div_tax": 2.38, "stk_div": 0.}])
    clean, audit = reconcile_stock_actions(bars, declarations, bars.date.min(), bars.date.max())
    assert clean.cash_div_tax.iloc[0] == 2.38
    assert audit[0]["nominal_ex_reference_difference"] is True
    declarations.loc[0, "cash_div_tax"] = 4
    with pytest.raises(ValueError, match="Large nominal"):
        reconcile_stock_actions(bars, declarations, bars.date.min(), bars.date.max())


def test_stock_only_unspecified_cash_remains_nan_not_a_zero_imputation():
    bars = pd.DataFrame({"symbol": ["A", "A"], "date": pd.to_datetime(["2025-01-02", "2025-01-03"]),
                         "close": [10., 7.], "adj_factor": [1., 1.45], "pre_close": [10., 10 / 1.45]})
    declarations = pd.DataFrame([{"ts_code": "A", "ann_date": "2024-12-25", "ex_date": "2025-01-03", "div_proc": "实施", "cash_div_tax": np.nan, "stk_div": .45}])
    clean, audit = reconcile_stock_actions(bars, declarations, bars.date.min(), bars.date.max())
    assert pd.isna(clean.cash_div_tax.iloc[0])
    assert audit[0]["verified_cash_per_share"] is None
    assert audit[0]["unspecified_cash_entitlement_blocks_held_strategy"] is True
