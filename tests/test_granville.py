from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from deepstock.data.store import clean_frame
from deepstock.strategies.both import granville as g


def fixture_bars(n=280):
    dates = pd.bdate_range("2024-01-02", periods=n)
    close = np.linspace(10, 14, n)
    b = pd.DataFrame({"open": close, "high": close + .2, "low": close - .2, "close": close,
                      "pre_close": np.r_[close[0], close[:-1]], "volume": 1e8, "adj_factor": 1.}, index=dates)
    for name in ["open", "high", "low", "close"]:
        b["adjusted_" + name] = b[name]
    return b


def config(b):
    c = json.loads((Path(__file__).resolve().parents[1] / "config/granville_v1.json").read_text())
    c["evaluation_start"], c["evaluation_end"] = str(b.index[220].date()), str(b.index[-1].date())
    return c


def force_signals(monkeypatch, b, buy=True):
    s = g.generate_signals(b, config(b)["signal"])
    s["ready"] = True
    for name in g.VARIANTS:
        s[name] = buy
    s["trend_exit"] = False
    s["reversion_exit"] = False
    monkeypatch.setattr(g, "generate_signals", lambda bars, rule: s.loc[bars.index])
    return s


def test_signals_are_prefix_invariant():
    b = fixture_bars(400)
    b.loc[b.index[290:], "adjusted_close"] *= .85
    rule = config(b)["signal"]
    full = g.generate_signals(b, rule)
    pd.testing.assert_frame_equal(g.generate_signals(b.iloc[:300], rule), full.iloc[:300])
    assert not full.iloc[:199][list(g.VARIANTS)].any().any()


@pytest.mark.parametrize("market", ["US", "CN"])
def test_next_open_cash_lots_costs_and_time_exit(monkeypatch, market):
    b = fixture_bars()
    s = force_signals(monkeypatch, b)
    s.loc[b.index[219], list(g.VARIANTS)] = False
    result = g.run_granville(b, b.index, config(b), market)
    first = result.trades.iloc[0]
    assert first.signal_date == str(b.index[220].date())
    assert first.date == str(b.index[221].date())
    assert first.cash_after >= 0 and first.commission > 0 and first.slippage > 0
    assert result.daily.cash.min() >= 0
    sold = result.trades.query("action == 'SELL'").iloc[0]
    assert sold.reason == "time_exit" and sold.held_sessions == 7
    assert sold.signal_date < sold.date
    assert (result.trades.quantity % 100).eq(0).all() if market == "CN" else True
    assert g.summarize(result.daily)["annualized_return"] == pytest.approx(result.daily.portfolio_equity.iloc[-1] ** (252 / len(result.daily)) - 1)


def test_close_stop_ignores_minimum_hold(monkeypatch):
    b = fixture_bars()
    force_signals(monkeypatch, b)
    day = b.index[220]
    for prefix in ["", "adjusted_"]:
        b.loc[day, prefix + "close"] = b.loc[day, prefix + "open"] * .9
        b.loc[day, prefix + "low"] = b.loc[day, prefix + "close"] - .1
    result = g.run_granville(b, b.index, config(b), "CN")
    sale = result.trades.query("action == 'SELL'").iloc[0]
    assert sale.reason == "close_stop" and sale.date == str(b.index[221].date())


def test_benchmark_retries_after_limit_block(monkeypatch):
    b = fixture_bars()
    force_signals(monkeypatch, b, False)
    day = b.index[220]
    b.loc[day, "pre_close"] = b.loc[day, "open"] / 1.1
    r = g.run_granville(b, b.index, config(b), "CN", "buy_hold", benchmark_exposure=1)
    assert r.daily.iloc[0].blocked_entry == 1
    assert r.trades.iloc[0].date == str(b.index[221].date())
    assert r.trades.iloc[0].reason == "benchmark_initial"


def test_partial_exit_persists_and_uses_prior_volume(monkeypatch):
    b = fixture_bars()
    force_signals(monkeypatch, b)
    b.loc[b.index[225:232], "volume"] = 10000  # 100-share sell capacity next day.
    r = g.run_granville(b, b.index, config(b), "CN")
    sales = r.trades.query("action == 'SELL'")
    assert len(sales) > 1 and sales.iloc[0].quantity == 100
    assert r.daily.delayed_exit.sum() > 0
    assert r.daily.loc[b.index[227], "pending_action"] == "SELL"


def test_dividend_entitlement_cash_and_factor_audit(monkeypatch):
    b = fixture_bars()
    force_signals(monkeypatch, b, False)
    ex, pay = b.index[222], b.index[224]
    original = b.copy()
    for name in ["open", "high", "low", "close"]:
        b.loc[ex:, name] -= .1
    expected_factor = original.close.iloc[221] / (original.close.iloc[221] - .1)
    b.loc[ex:, "adj_factor"] = expected_factor
    d = pd.DataFrame([{"ex_date": ex, "record_date": b.index[221], "pay_date": pay, "cash_per_share": .1}])
    r = g.run_granville(b, b.index, config(b), "CN", "buy_hold", dividends=d)
    assert r.daily.loc[ex, "receivable"] == pytest.approx(r.daily.loc[ex, "quantity"] * .1)
    assert r.daily.loc[pay, "receivable"] == 0
    assert r.daily.loc[pay, "cash"] > r.daily.loc[ex, "cash"]
    # Raw ex-price loss is offset by the receivable, not counted twice.
    assert r.daily.loc[ex, "portfolio_net_return"] > 0
    bad = b.copy()
    bad.loc[ex:, "adj_factor"] *= 2
    with pytest.raises(ValueError, match="magnitude"):
        g.audit_cn_actions(bad, d)
    d.loc[0, "record_date"] = b.index[220]
    with pytest.raises(ValueError, match="entitlement"):
        g.audit_cn_actions(b, d)


def test_missing_sessions_unknown_actions_bad_ohlc_block():
    b = fixture_bars()
    with pytest.raises(ValueError, match="sessions"):
        g.run_granville(b.drop(b.index[230]), b.index, config(b), "US")
    b.loc[b.index[231]:, "adj_factor"] = 2
    with pytest.raises(ValueError, match="Unexplained"):
        g.run_granville(b, b.index, config(b), "CN")
    b.loc[b.index[230], "high"] = .1
    with pytest.raises(ValueError, match="OHLC"):
        g.run_granville(b, b.index, config(b), "US")


def test_walk_forward_slices_continuous_ledger(monkeypatch):
    b = fixture_bars()
    force_signals(monkeypatch, b)
    r = g.run_granville(b, b.index, config(b), "US")
    windows = g.walk_forward(r.daily, r.daily, {"history_sessions": 10, "test_sessions": 8, "step_sessions": 8})
    assert windows[0]["total_return"] == pytest.approx((1 + r.daily.portfolio_net_return.iloc[10:18]).prod() - 1)
    assert windows[0]["benchmark"]["annualized_return"] == windows[0]["annualized_return"]
    assert windows[0]["trading_days"] == 8


def test_provider_factors_and_dividend_economic_revisions():
    f, rejected, q, kind = clean_frame(pd.DataFrame([{"ts_code": "510300.SH", "trade_date": 20260119, "adj_factor": 1.2}]), {"market": "CN", "endpoint": "fund_adj"})
    assert kind == "adjustment_factors" and not q["blocking"]
    event = {"ts_code": "510300.SH", "ann_date": 20260112, "record_date": 20260116, "ex_date": 20260119, "pay_date": 20260127, "div_proc": "实施", "div_cash": .123, "net_ex_date": np.nan}
    df = pd.DataFrame([{**event, "base_unit": np.nan}, {**event, "base_unit": 100., "net_ex_date": 20260119.}])
    f, rejected, q, kind = clean_frame(df, {"market": "CN", "endpoint": "fund_div"})
    assert len(f) == 1 and not q["blocking"] and q["equivalent_disclosures"] == 1
    assert q["imputed_rows"] == 0 and f.iloc[0].div_cash == .123
    df.loc[1, "div_cash"] = .2
    f, rejected, q, kind = clean_frame(df, {"market": "CN", "endpoint": "fund_div"})
    assert q["blocking"] and q["conflict_rows"] == 2


@pytest.mark.parametrize("field,value", [("fast_days", 0), ("confirmation_days", 1.5), ("stop_loss", np.nan)])
def test_invalid_rule_contract(field, value):
    b = fixture_bars()
    c = config(b)
    c["signal"][field] = value
    with pytest.raises(ValueError):
        g.run_granville(b, b.index, c, "US")


@pytest.mark.parametrize("entry", [["scripts/run_granville_backtest.py"], ["-m", "scripts.run_granville_backtest"]])
def test_cli_help_supports_both_entrypoints(entry):
    process = subprocess.run([sys.executable, *entry, "--help"], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    assert "--cn-dir" in process.stdout and "--us-prices" in process.stdout
