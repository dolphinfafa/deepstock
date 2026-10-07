import pandas as pd
import pytest

from deepstock.data.membership import audit_us_membership, require_us_membership
from deepstock.data.store import DataQualityError
from deepstock.strategies.both.granville_portfolio import make_panel, PortfolioDataError
from test_granville_portfolio import inputs


def test_unknown_tail_is_blocked_not_cash_or_interval_extension():
    dates = pd.bdate_range("2026-08-20", "2026-09-01")
    members = pd.DataFrame([{"symbol": "A", "date": "2026-08-20", "end": "2026-08-21"}])
    original = members.copy(deep=True)
    value = audit_us_membership(dates, members, "2026-08-20", "2026-09-01")
    assert value["status"] == "blocked"
    assert value["first_zero_member_close"] == "2026-08-24"
    assert value["zero_member_evaluation_sessions"] == 7
    with pytest.raises(DataQualityError, match="membership coverage missing"):
        require_us_membership(dates, members, "2026-08-20", "2026-09-01")
    pd.testing.assert_frame_equal(members, original)


def test_internal_gap_and_previous_signal_close_are_required():
    dates = pd.bdate_range("2026-08-20", periods=5)
    members = pd.DataFrame([{"symbol": "A", "date": dates[1], "end": dates[1]},
                            {"symbol": "B", "date": dates[3], "end": dates[-1]}])
    value = audit_us_membership(dates, members, dates[1], dates[-1])
    assert value["zero_member_close_sessions"] == 2
    assert value["first_zero_member_close"] == "2026-08-20"


def test_membership_presence_is_not_proof_of_complete_pool_and_duplicates_do_not_count():
    dates = pd.bdate_range("2026-08-20", periods=5)
    members = pd.DataFrame([{"symbol": "A", "date": dates[0], "end": dates[-1]},
                            {"symbol": "A", "date": dates[1], "end": dates[-1]}])
    value = require_us_membership(dates, members, dates[1], dates[-1])
    assert value["status"] == "presence_check_passed_not_complete_universe_proof"
    assert value["members_at_evaluation_end"] == 1


def test_engine_rejects_stale_membership_even_with_complete_prices():
    bars, dates, members, div, cfg, rule = inputs()
    members["end"] = dates[250]
    with pytest.raises(PortfolioDataError, match="membership coverage missing"):
        make_panel(bars, dates, members, "US", rule, cfg)


def test_malformed_interval_and_unsorted_calendar_rejected():
    dates = pd.bdate_range("2026-08-20", periods=5)
    members = pd.DataFrame([{"symbol": "A", "date": dates[-1], "end": dates[0]}])
    with pytest.raises(DataQualityError, match="Invalid membership"):
        audit_us_membership(dates, members, dates[0], dates[-1])
    with pytest.raises(DataQualityError, match="Unique ordered"):
        audit_us_membership(dates[::-1], members, dates[0], dates[-1])


def test_collector_refuses_stale_mapping_before_price_collection(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from scripts import prepare_granville_stock_data as collector
    cfg = {"source_start": "2026-08-20", "evaluation_start": "2026-08-20", "evaluation_end": "2026-09-01"}
    base = tmp_path / "artifacts/research/norgate/stock-universe-sp500-liquidity"
    base.mkdir(parents=True)
    (base / "membership-0000.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(collector, "ROOT", tmp_path)
    monkeypatch.setattr(collector, "read_clean_json", lambda path: {"A": [{"start": "2026-08-20", "end": "2026-08-21"}]})
    monkeypatch.setitem(sys.modules, "norgatedata", SimpleNamespace())
    with pytest.raises(DataQualityError, match="membership coverage missing"):
        collector.collect_us(tmp_path / "new-data", cfg)
    assert not (tmp_path / "new-data").exists()


def test_history_failures_require_actual_terminal_date_and_unknown_remains_unknown(monkeypatch):
    import sys
    from types import SimpleNamespace
    from scripts import audit_granville_data_readiness as audit
    from deepstock.data import capture
    dates = {"OLD": "2004-12-03", "ACTIVE": None, "IN_RANGE": "2006-01-02"}
    monkeypatch.setitem(sys.modules, "norgatedata", SimpleNamespace(last_quoted_date=lambda symbol: dates[symbol],
                         watchlist_symbols=lambda name: ["OLD", "ACTIVE", "IN_RANGE", "NEW"]))
    monkeypatch.setattr(capture, "capture_response", lambda *args, **kwargs: "v" * 64)
    monkeypatch.setattr(audit, "DataStore", lambda: SimpleNamespace(get=lambda version: {"raw_sha256": "h" * 64}))
    value = audit.verify_history_metadata({"requested_from": "2005-01-01", "watchlist": "index",
                                           "failures": [{"symbol": s} for s in dates],
                                           "chunks": [{"symbols": list(dates)}]})
    assert value["terminal_before_requested_start"] == 1
    assert value["unresolved_overlap"] == 2
    assert value["new_watchlist_code_count"] == 1
