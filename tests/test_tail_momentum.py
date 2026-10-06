from dataclasses import replace
import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from deepstock.tail_momentum import TailMomentumConfig, cost_cases, prepare_sessions, run_tail_momentum, walk_forward
from scripts.run_cn_etf_tail_momentum import run_research


def minute_data(days=5):
    dates = pd.bdate_range("2025-01-02", periods=days)
    records = []
    for day in dates:
        times = pd.date_range(f"{day.date()} 09:31", f"{day.date()} 11:30", freq="min").append(pd.date_range(f"{day.date()} 13:01", f"{day.date()} 15:00", freq="min"))
        for stamp in times:
            price = 10.0 if stamp.time() <= pd.Timestamp("13:30").time() else 10.1
            records.append({"symbol": "510300.SH", "timestamp": stamp, "open": price, "high": price, "low": price, "close": price, "volume": 100_000_000, "amount": price * 100_000_000})
    return pd.DataFrame(records), dates


def panel(days=5):
    return prepare_sessions(*minute_data(days))


def no_cost():
    return TailMomentumConfig(commission_bps=0, minimum_commission=0, slippage_bps=0)


def test_signal_and_execution_use_strictly_separate_times_and_obey_t1():
    result = run_tail_momentum(panel(), config=no_cost())
    assert result.summary["entry_fills"] == 5
    assert result.summary["completed_trades"] == 4
    assert (result.trades["entry_bar_end"] > result.trades["signal_time"]).all()
    assert (result.trades["exit_date"] > result.trades["entry_date"]).all()
    assert result.summary["unclosed_shares"] > 0
    assert result.daily["cash_cny"].ge(0).all()


def test_later_prices_do_not_change_r6_signal():
    bars, dates = minute_data()
    changed = bars.copy()
    later = changed["timestamp"].dt.time > pd.Timestamp("14:00").time()
    changed.loc[later, ["open", "high", "low", "close"]] = 20
    changed.loc[later, "amount"] = changed.loc[later, "volume"] * 20
    first, second = prepare_sessions(bars, dates), prepare_sessions(changed, dates)
    pd.testing.assert_series_equal(first["r6"], second["r6"])
    assert not first["r7"].equals(second["r7"])


def test_missing_signal_bar_does_not_get_filled_and_calendar_gap_is_rejected():
    bars, dates = minute_data()
    missing = bars[~bars["timestamp"].eq(pd.Timestamp(f"{dates[0].date()} 13:45"))]
    sessions = prepare_sessions(missing, dates)
    assert not sessions.iloc[0]["r6_valid"]
    result = run_tail_momentum(sessions)
    assert result.daily.iloc[0]["entry_fill"] == 0
    with pytest.raises(ValueError, match="Missing session"):
        prepare_sessions(bars[bars["timestamp"].dt.normalize().ne(dates[1])], dates)


def test_next_close_does_not_double_spend_capital_before_old_exit():
    result = run_tail_momentum(panel(4), "r6_next_close", no_cost())
    assert result.daily["entry_fill"].tolist() == [1, 0, 1, 0]
    assert result.daily["blocked_entry"].tolist() == [0, 1, 0, 1]
    assert result.summary["completed_trades"] == 2


def test_missing_or_partial_open_exit_carries_inventory():
    sessions = panel(4)
    sessions.loc[sessions.index[1], "open_exit_volume"] = 0
    result = run_tail_momentum(sessions, config=no_cost())
    assert result.daily.iloc[1]["delayed_exit"] == 1
    assert result.daily.iloc[1]["blocked_entry"] == 1
    assert result.trades.iloc[0]["holding_sessions"] == 2
    partial = panel(3)
    partial.loc[partial.index[1], "open_exit_volume"] = 50_000
    filled = run_tail_momentum(partial, config=no_cost())
    assert filled.trades.iloc[0]["shares"] == 500
    assert filled.daily.iloc[1]["delayed_exit"] == 1


def test_minimum_commission_lots_capacity_and_cost_sensitivity():
    sessions = panel()
    config = TailMomentumConfig(initial_capital=3000)
    result = run_tail_momentum(sessions, config=config)
    assert result.daily["shares"].mod(100).eq(0).all()
    assert result.daily.iloc[0]["commission_cny"] == 5
    assert result.daily["cash_cny"].ge(0).all()
    low = run_tail_momentum(sessions, config=cost_cases()["stress_6bp"])
    high = run_tail_momentum(sessions, config=cost_cases()["stress_20bp"])
    assert high.summary["total_return"] < low.summary["total_return"]
    assert high.summary["slippage_cny"] > low.summary["slippage_cny"]


def test_dividends_are_earned_only_by_overnight_holders_and_cash_waits_for_payment():
    sessions = panel(4)
    dividend = pd.DataFrame([{"ex_date": sessions.index[1], "pay_date": sessions.index[3], "cash_per_share": 0.1}])
    result = run_tail_momentum(sessions, "r6_next_close", no_cost(), dividends=dividend)
    shares = result.daily.iloc[0]["shares"]
    assert result.daily.iloc[1]["receivable_cny"] == pytest.approx(shares * 0.1)
    assert result.daily.iloc[2]["receivable_cny"] > 0
    assert result.daily.iloc[3]["receivable_cny"] == 0
    same_day = dividend.copy()
    same_day.loc[0, "ex_date"] = sessions.index[0]
    no_entitlement = run_tail_momentum(sessions, "r6_next_close", no_cost(), dividends=same_day)
    assert no_entitlement.daily["receivable_cny"].eq(0).all()


def test_benchmarks_keep_same_cash_lot_and_inventory_constraints():
    sessions = panel(4)
    strategy = run_tail_momentum(sessions, "r6_next_close", no_cost())
    benchmark = run_tail_momentum(sessions, "r6_next_close", no_cost(), always_enter=True)
    pd.testing.assert_frame_equal(strategy.daily, benchmark.daily)
    assert walk_forward(strategy.daily) == []
    assert len(walk_forward(strategy.daily, train=1, test=2, step=1)) == 2


def test_first_session_loss_is_included_in_drawdown():
    sessions = panel(2)
    sessions.loc[sessions.index[0], "close"] = 9
    result = run_tail_momentum(sessions, config=no_cost())
    assert result.summary["maximum_drawdown"] < -0.10


def test_invalid_data_and_config_are_rejected():
    with pytest.raises(ValueError):
        TailMomentumConfig(participation_rate=0)
    bars, dates = minute_data()
    with pytest.raises(ValueError, match="unique"):
        prepare_sessions(pd.concat([bars, bars.iloc[:1]]), dates)
    bars.loc[0, "amount"] *= 100
    with pytest.raises(ValueError, match="VWAP"):
        prepare_sessions(bars, dates)


def test_no_data_produces_blocked_report_not_synthetic_performance(tmp_path):
    report = run_research(tmp_path / "missing-data", tmp_path / "reports")
    assert report["status"] == "blocked_data"
    assert report["cases"] == []
    assert "真实行情回测结果" in (tmp_path / "reports/report.md").read_text()


def write_unit_inputs(root):
    """Ephemeral pipeline fixtures; never publish as market research."""
    root.mkdir(parents=True, exist_ok=True)
    bars, dates = minute_data()
    bars.to_csv(root / "minutes.csv.gz", index=False)
    pd.DataFrame({"date": dates}).to_csv(root / "calendar.csv", index=False)
    manifest = {"data_kind": "market", "provider": "unit_fixture_never_published",
                "interval_minutes": 1, "timestamp_convention": "end", "timezone": "Asia/Shanghai",
                "adjustment": "none", "volume_unit": "shares", "amount_unit": "CNY",
                "corporate_action_status": "price_only_unverified",
                "sha256": {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in ("minutes.csv.gz", "calendar.csv")}}
    (root / "manifest.json").write_text(json.dumps(manifest))
    from deepstock.data import DataStore
    store = DataStore(root.parent)
    store.import_file(root / "minutes.csv.gz", {"market": "CN", "provider": "unit_fixture_never_published", "timezone": "Asia/Shanghai"})
    store.import_file(root / "calendar.csv", {"market": "CN", "provider": "unit_fixture_never_published", "kind": "calendar"})
    return manifest


def test_runner_retains_all_fixed_cases_and_actual_coverage(tmp_path):
    data = tmp_path / "unit-data"
    write_unit_inputs(data)
    output = tmp_path / "unit-output"
    result = run_research(data, output)
    assert result["status"] == "complete"
    assert result["sessions"] == 5
    assert result["evidence_stage"] == "short_sample_pilot_not_validated"
    assert len(result["cases"]) == 12
    assert all(case["walk_forward"] == [] for case in result["cases"])
    assert len(list(output.glob("*-daily.csv"))) == 12
    assert len(list(output.glob("*-benchmark.csv"))) == 12
    report = (output / "report.md").read_text()
    assert "仅为短样本流程验证" in report
    assert "连续买入持有对照" in report
    assert "未平仓份额" in report


@pytest.mark.parametrize("field,value", [("data_kind", "synthetic"), ("provider", "synthetic market"),
                                         ("volume_unit", "lots"), ("corporate_action_status", "unknown")])
def test_runner_rejects_unverified_contract_not_fabricating_results(tmp_path, field, value):
    manifest = write_unit_inputs(tmp_path / "data")
    manifest[field] = value
    (tmp_path / "data/manifest.json").write_text(json.dumps(manifest))
    result = run_research(tmp_path / "data", tmp_path / "output")
    assert result["status"] == "blocked_data"
    assert result["cases"] == []


def test_runner_rejects_modified_input_checksum(tmp_path):
    manifest = write_unit_inputs(tmp_path / "data")
    manifest["sha256"]["calendar.csv"] = "bad"
    (tmp_path / "data/manifest.json").write_text(json.dumps(manifest))
    result = run_research(tmp_path / "data", tmp_path / "output")
    assert result["status"] == "blocked_data"
    assert "checksum" in result["reason"]
