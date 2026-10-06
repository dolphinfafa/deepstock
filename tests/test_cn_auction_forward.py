# -*- coding: utf-8 -*-
from datetime import date, datetime

import pandas as pd
import pytest

from deepstock.strategies.cn.auction.forward import (
    SHANGHAI,
    apply_counterfactual_exit_prices,
    apply_entry_fills,
    apply_exit_prices,
    build_forward_report,
    build_forward_dashboard,
    classify_announcement,
    fetch_tushare_announcement_pages,
    fill_entries,
    expire_unfilled_entries,
    open_store,
    record_late_auction_counterfactual,
    record_signal_report,
    store_announcement_frame,
)
from scripts.install_short_term_forward_cron import (
    BEGIN_MARKER,
    END_MARKER,
    cron_block,
    replace_managed_block,
)


def _announcement_frame(rows):
    return pd.DataFrame([
        {
            "ts_code": f"{code}.SH",
            "name": f"company-{code}",
            "ann_date": "20260901",
            "title": title,
            "url": f"https://example.test/{code}/{index}.pdf",
        }
        for index, (code, title) in enumerate(rows)
    ])


def _report():
    return {
        "signal_date": "2026-09-01",
        "candidate_mode": "context_research_fallback",
        "actionable": False,
        "strategy_health": {"passed": False},
        "candidates": [
            {
                "rank": index,
                "model_rank": index,
                "stock_code": code,
                "name": f"company-{code}",
                "industry": "industry",
                "predicted_return": 0.001 * index,
                "predicted_excess_return": None,
                "up_probability": 0.5,
                "prediction_score": 1.0 / index,
            }
            for index, code in enumerate(("600001", "600002", "600003"), start=1)
        ],
    }


def test_announcement_classifier_uses_conservative_title_rules():
    assert classify_announcement("关于回购股份方案的公告").score == 1
    assert classify_announcement("收到中标通知书").event_type == "contract_win"
    assert classify_announcement("收到立案告知书").score == -1
    assert classify_announcement("股东减持计划公告").score == -1
    assert classify_announcement("股东减持期满暨未减持股份").score == 0
    assert classify_announcement("董事会会议决议公告").score == 0


def test_tushare_announcement_fetch_is_paginated():
    class FakePro:
        def anns_d(self, **kwargs):
            offset = kwargs["offset"]
            rows = [
                {
                    "ts_code": f"60000{index}.SH",
                    "name": "company",
                    "ann_date": "20260901",
                    "title": f"title-{index}",
                    "url": f"https://example.test/{index}.pdf",
                }
                for index in range(offset, min(offset + 2, 3))
            ]
            return pd.DataFrame(rows)

    frame = fetch_tushare_announcement_pages(
        date(2026, 9, 1), pro=FakePro(), page_size=2
    )
    assert frame["ts_code"].tolist() == ["600000.SH", "600001.SH", "600002.SH"]


def test_first_seen_is_immutable_and_backfill_is_not_a_live_event(tmp_path):
    connection = open_store(tmp_path)
    first = datetime(2026, 8, 31, 20, 0, tzinfo=SHANGHAI)
    later = datetime(2026, 8, 31, 21, 0, tzinfo=SHANGHAI)
    frame = _announcement_frame([("600001", "关于回购股份方案的公告")])
    store_announcement_frame(connection, frame, first, "live")
    store_announcement_frame(connection, frame, later, "live")
    backfill = _announcement_frame([("600004", "收到中标通知书")])
    store_announcement_frame(connection, backfill, first, "backfill")

    row = connection.execute(
        "SELECT first_seen_at, last_seen_at FROM announcement_event WHERE stock_code='600001'"
    ).fetchone()
    assert row["first_seen_at"] == first.isoformat(timespec="seconds")
    assert row["last_seen_at"] == first.isoformat(timespec="seconds")

    result = record_signal_report(
        connection,
        _report(),
        datetime(2026, 9, 1, 9, 27, tzinfo=SHANGHAI),
        date(2026, 8, 31),
        date(2026, 9, 2),
        ("600001", "600002", "600003", "600004"),
    )
    assert result["universe_event_stocks"] == 1
    assert result["strategies"]["event_positive"] == 1
    connection.close()


def test_signal_paper_fills_and_delayed_limit_down_exit(tmp_path):
    connection = open_store(tmp_path)
    observed = datetime(2026, 8, 31, 20, 0, tzinfo=SHANGHAI)
    frame = _announcement_frame([
        ("600001", "关于回购股份方案的公告"),
        ("600002", "收到立案告知书"),
        ("600004", "收到中标通知书"),
    ])
    store_announcement_frame(connection, frame, observed, "live")
    result = record_signal_report(
        connection,
        _report(),
        datetime(2026, 9, 1, 9, 27, tzinfo=SHANGHAI),
        date(2026, 8, 31),
        date(2026, 9, 2),
        ("600001", "600002", "600003", "600004"),
    )
    assert result["strategies"] == {
        "model_baseline": 3,
        "model_exclude_negative": 2,
        "model_positive_confirmation": 1,
        "event_positive": 2,
        "diagnostic_negative_events": 1,
    }
    duplicate = record_signal_report(
        connection,
        _report(),
        datetime(2026, 9, 1, 9, 28, tzinfo=SHANGHAI),
        date(2026, 8, 31),
        date(2026, 9, 2),
        ("600001", "600002", "600003", "600004"),
    )
    assert duplicate["status"] == "already_recorded"

    entries = pd.DataFrame([
        {
            "stock_code": code,
            "trade_date": date(2026, 9, 1),
            "entry_0931_vwap": 10.0,
            "entry_0931_volume": 1_000_000,
        }
        for code in ("600001", "600002", "600003", "600004")
    ])
    fill = apply_entry_fills(
        connection,
        date(2026, 9, 1),
        entries,
        datetime(2026, 9, 1, 9, 32, tzinfo=SHANGHAI),
    )
    assert fill["missing_price"] == 0
    assert fill["filled"] == sum(result["strategies"].values())
    largest = connection.execute(
        "SELECT MAX(entry_notional) AS value FROM paper_order"
    ).fetchone()["value"]
    assert largest <= 500_000

    day_one = pd.DataFrame([
        {
            "ts_code": f"{code}.SH",
            "pre_close": 10.0,
            "low": 9.0 if code == "600002" else 10.0,
            "close": 9.0 if code == "600002" else 10.5,
        }
        for code in ("600001", "600002", "600003", "600004")
    ])
    first_exit = apply_exit_prices(
        connection,
        {date(2026, 9, 2): day_one},
        date(2026, 9, 2),
        datetime(2026, 9, 2, 16, 0, tzinfo=SHANGHAI),
    )
    assert first_exit["blocked_observations"] > 0
    assert first_exit["still_open"] > 0
    open_dashboard = build_forward_dashboard(connection)
    assert open_dashboard["days"][0]["process"]["entry"] == "completed"
    assert open_dashboard["days"][0]["process"]["settlement"] == "partial"

    day_two = pd.DataFrame([{
        "ts_code": "600002.SH",
        "pre_close": 9.0,
        "low": 9.0,
        "close": 9.2,
    }])
    second_exit = apply_exit_prices(
        connection,
        {date(2026, 9, 3): day_two},
        date(2026, 9, 3),
        datetime(2026, 9, 3, 16, 0, tzinfo=SHANGHAI),
    )
    assert second_exit["still_open"] == 0
    order = connection.execute(
        """
        SELECT actual_exit_date, transaction_cost, net_return_on_order
        FROM paper_order WHERE stock_code='600002' LIMIT 1
        """
    ).fetchone()
    assert order["actual_exit_date"] == "2026-09-03"
    assert order["transaction_cost"] > 0
    assert order["net_return_on_order"] == pytest.approx(-0.082)

    report = build_forward_report(connection, date(2026, 9, 3))
    assert report["strategies"]["model_baseline"]["completed_cohorts"] == 1
    assert report["strategies"]["model_baseline"]["cumulative_return"] is not None
    assert not report["evaluation_gate"]["preliminary_ready"]
    dashboard = build_forward_dashboard(connection)
    assert dashboard["days"][0]["process"]["settlement"] == "completed"
    assert dashboard["days"][0]["candidates"][0]["baseline_order"]["status"] == "CLOSED"
    assert len(dashboard["equity_curve"]) == 1
    connection.close()


def test_cron_installer_preserves_unrelated_jobs_and_is_idempotent(tmp_path):
    original = "15 2 * * * /usr/local/bin/unrelated\n"
    block = cron_block(tmp_path, tmp_path / "python")
    installed = replace_managed_block(original, block)
    reinstalled = replace_managed_block(installed, block)

    assert installed == reinstalled
    assert "unrelated" in installed
    assert installed.count(BEGIN_MARKER) == 1
    assert installed.count(END_MARKER) == 1
    assert "27,29 9 * * 1-5" in installed
    assert "31 9 * * 1-5" in installed
    assert "fill --wait-seconds 90" in installed
    assert "33,38,43 9 * * 1-5" in installed
    assert "settle-counterfactual" in installed


def test_fill_uses_tushare_rt_min_and_persists_snapshot(tmp_path):
    connection = open_store(tmp_path)
    result = record_signal_report(
        connection,
        _report(),
        datetime(2026, 9, 1, 9, 27, tzinfo=SHANGHAI),
        date(2026, 8, 31),
        date(2026, 9, 2),
        ("600001", "600002", "600003"),
    )
    connection.close()

    class FakePro:
        def query(self, endpoint, **kwargs):
            assert endpoint == "rt_min"
            assert kwargs["freq"] == "1MIN"
            return pd.DataFrame([
                {
                    "ts_code": symbol,
                    "time": "2026-09-01 09:31:00",
                    "open": 10.0,
                    "high": 10.1,
                    "low": 9.9,
                    "close": 10.0,
                    "vol": 1_000_000,
                    "amount": 10_000_000,
                }
                for symbol in kwargs["ts_code"].split(",")
            ])

    fill = fill_entries(
        tmp_path,
        date(2026, 9, 1),
        wait_seconds=0.25,
        poll_seconds=0.001,
        pro=FakePro(),
        observed_at=datetime(2026, 9, 1, 9, 31, 5, tzinfo=SHANGHAI),
    )
    assert fill["status"] == "ok"
    assert fill["snapshot_rows"] == 3
    assert fill["filled"] == sum(result["strategies"].values())
    assert fill["missing_price"] == 0

    connection = open_store(tmp_path)
    assert connection.execute(
        "SELECT COUNT(*) FROM entry_minute_snapshot"
    ).fetchone()[0] == 3
    sources = connection.execute(
        "SELECT DISTINCT entry_source FROM paper_order"
    ).fetchall()
    assert [row[0] for row in sources] == ["tushare.rt_min"]
    connection.close()


def test_unfilled_entry_expires_to_cash(tmp_path):
    connection = open_store(tmp_path)
    record_signal_report(
        connection,
        _report(),
        datetime(2026, 9, 1, 9, 27, tzinfo=SHANGHAI),
        date(2026, 8, 31),
        date(2026, 9, 2),
        ("600001", "600002", "600003"),
    )
    expired = expire_unfilled_entries(connection, date(2026, 9, 2))
    assert expired > 0
    report = build_forward_report(connection, date(2026, 9, 2))
    baseline = next(
        item for item in report["daily"]
        if item["strategy"] == "model_baseline"
    )
    assert baseline["status"] == "closed"
    assert baseline["portfolio_return"] == 0
    connection.close()


def test_late_auction_counterfactual_is_isolated_and_settles(tmp_path):
    connection = open_store(tmp_path)
    observed = datetime(2026, 8, 31, 20, 0, tzinfo=SHANGHAI)
    frame = _announcement_frame([
        ("600001", "关于回购股份方案的公告"),
        ("600002", "收到立案告知书"),
        ("600004", "收到中标通知书"),
    ])
    store_announcement_frame(connection, frame, observed, "live")
    auction = pd.DataFrame([
        {
            "ts_code": f"{code}.SH",
            "price": 10.0,
            "vol": 1_000_000,
            "amount": 10_000_000.0,
        }
        for code in ("600001", "600002", "600003", "600004")
    ])
    result = record_late_auction_counterfactual(
        connection,
        _report(),
        auction,
        datetime(2026, 9, 1, 10, 30, tzinfo=SHANGHAI),
        date(2026, 8, 31),
        date(2026, 9, 2),
        ("600001", "600002", "600003", "600004"),
    )
    assert result["official_forward_sample"] is False
    assert result["strategies"] == {
        "model_baseline": 3,
        "model_exclude_negative": 2,
        "model_positive_confirmation": 1,
        "event_positive": 2,
        "diagnostic_negative_events": 1,
    }
    assert connection.execute("SELECT COUNT(*) FROM signal_run").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM paper_order").fetchone()[0] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM counterfactual_order"
    ).fetchone()[0] == 9

    exits = pd.DataFrame([
        {
            "ts_code": f"{code}.SH",
            "pre_close": 10.0,
            "low": 10.0,
            "close": 10.5,
        }
        for code in ("600001", "600002", "600003", "600004")
    ])
    settled = apply_counterfactual_exit_prices(
        connection,
        {date(2026, 9, 2): exits},
        date(2026, 9, 2),
        datetime(2026, 9, 2, 16, 0, tzinfo=SHANGHAI),
    )
    assert settled["closed"] == 9
    order = connection.execute(
        "SELECT net_return_on_order FROM counterfactual_order LIMIT 1"
    ).fetchone()
    assert order["net_return_on_order"] == pytest.approx(0.048)
    dashboard = build_forward_dashboard(connection)
    assert dashboard["days"] == []
    assert len(dashboard["counterfactual_runs"]) == 1
    assert dashboard["counterfactual_runs"][0]["process"]["settlement"] == "completed"
    connection.close()
