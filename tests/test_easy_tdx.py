import numpy as np
import pandas as pd
import pytest
from deepstock.data.store import DataStore, DataQualityError, clean_frame
from deepstock.data.easy_tdx import RequestBudget, compare_daily, minute_audit, daily_coverage


def raw_bars():
    return pd.DataFrame({"datetime": ["2026-09-28 09:30:00", "2026-09-28 09:31:00"], "open": [10., 10.],
                         "high": [10.1, 10.1], "low": [9.9, 9.9], "close": [10., 10.], "vol": [100., 200.], "amount": [1000., 2000.]})


def metadata(period="MIN_1"):
    return {"market": "CN", "provider": "easy-tdx", "endpoint": "easy_tdx_bars", "normalizer": "easy-tdx-cn-v1",
            "symbol": "000001.SZ", "period": period, "adjustment": "NONE", "bar_time": "start",
            "volume_unit": "shares", "amount_unit": "CNY", "restricted": True}


def test_normalizer_is_explicit_no_scaling_no_imputation_and_records_time_bounds(tmp_path):
    raw = raw_bars()
    path = tmp_path / "bars.csv"
    raw.to_csv(path, index=False)
    s = DataStore(tmp_path)
    m = s.import_file(path, metadata())
    f = pd.read_csv(s.verified_path(m))
    assert m["status"] == "ready" and m["preview_allowed"] is False and m["kind"] == "minute_bars"
    assert f.volume.tolist() == [100, 200]
    assert f.timestamp.iloc[0] == "2026-09-28 09:30:00+08:00"
    assert f.bar_end.iloc[0] == "2026-09-28 09:31:00+08:00"
    assert m["quality"]["imputed_rows"] == 0
    assert s.import_file(path, metadata())["id"] == m["id"]
    with pytest.raises(DataQualityError, match="Explicit"):
        clean_frame(raw, {**metadata(), "volume_unit": "unknown"})


def test_conflicting_duplicate_invalid_price_and_unsorted_input_are_audited():
    raw = raw_bars()
    other = raw.iloc[[0]].copy()
    other["close"] = 10.05
    _, rejected, q, _ = clean_frame(pd.concat([raw.iloc[::-1], other], ignore_index=True), metadata())
    assert q["blocking"] and q["conflict_rows"] == 2 and not q["raw_ordered"]
    bad = raw.copy()
    bad.loc[0, "open"] = np.nan
    _, rejected, q, _ = clean_frame(bad, metadata())
    assert len(rejected) == 1 and q["imputed_rows"] == 0


def test_calendar_handles_holiday_and_minute_gaps_not_weekday_staleness():
    sessions = pd.DatetimeIndex(["2026-09-28", "2026-09-29", "2026-09-30"])
    d = pd.DataFrame({"date": ["2026-09-28", "2026-09-30"]})
    q = daily_coverage(d, sessions)
    assert q["missing_sessions"] == ["2026-09-29"] and q["current_through_latest_complete_session"]
    f, _, _, _ = clean_frame(raw_bars(), metadata())
    q = minute_audit(f, sessions[:1])
    assert q["expected_minutes"] == 240 and len(q["missing_minutes"]) == 238
    assert q["non_session_labels"] == []
    f.loc[0, "timestamp"] = "2026-09-28 12:00:00+08:00"
    assert len(minute_audit(f, sessions[:1])["non_session_labels"]) == 1


def test_cross_source_tolerance_never_fits_a_volume_ratio():
    ref = pd.DataFrame({"date": ["2026-09-28"], "open": [10.], "high": [10.], "low": [10.], "close": [10.], "volume": [1000.], "amount": [10000.]})
    actual = ref.copy()
    actual["volume"] *= 100
    actual["close"] += .011
    result, diffs = compare_daily(actual, ref, "000001.SZ")
    assert result["threshold_exceedances"] == 2
    assert {x["field"] for x in diffs} == {"close", "volume"}
    assert actual.volume.iloc[0] == 100000


def test_stock_reference_selects_documented_cny_turnover_not_auxiliary_provider_amount():
    actual = pd.DataFrame({"date": ["2026-09-28"], "open": [10.], "high": [10.], "low": [10.], "close": [10.], "volume": [1000.], "amount": [10000.]})
    reference = actual.copy()
    reference["turnover"] = reference.amount
    reference["amount"] = reference.amount / 1000
    result, diffs = compare_daily(actual, reference, "000001.SZ")
    assert result["threshold_exceedances"] == 0 and not diffs
    assert reference.amount.iloc[0] == 10


def test_budget_counts_setup_pages_retries_and_hard_stops():
    now = [0.]
    slept = []
    def sleep(n):
        slept.append(n)
        now[0] += n
    b = RequestBudget(limit=3, interval=1, clock=lambda: now[0], sleep=sleep)
    for kind in ["setup", "page", "retry"]:
        b.consume("host", kind)
    assert len(b.events) == 3 and slept == [1., 1.]
    with pytest.raises(DataQualityError, match="budget"):
        b.consume("host", "hidden_retry")


def test_offline_recovery_uses_retained_versions_and_never_calls_network(tmp_path, monkeypatch):
    from scripts import audit_easy_tdx as cli
    from deepstock.data.store import write_json
    store = DataStore(tmp_path)
    source = tmp_path / "capture"
    source.mkdir()
    bars_path = source / "bars.csv"
    raw = raw_bars().iloc[:1].copy()
    raw["datetime"] = "2026-09-30 00:00:00"
    raw.to_csv(bars_path, index=False)
    bars = store.import_file(bars_path, metadata("DAILY"))
    cal_path = source / "calendar.csv"
    pd.DataFrame({"cal_date": ["2023-01-01", "2026-09-30", "2026-10-07"], "is_open": [0, 1, 0]}).to_csv(cal_path, index=False)
    cal = store.import_file(cal_path, {"market": "CN", "provider": "Tushare", "kind": "calendar"})
    write_json(source / "publication.json", {"scope": "independent_CN_data_audit_no_strategy", "code_provenance": {},
               "as_of_date": "2026-10-07", "latest_complete_session": "2026-09-30", "sdk_version": "1.20.8",
               "actual_requests": 70, "artifact_hashes": {}, "data_versions": [bars["id"], cal["id"]],
               "items": [{"symbol": "000001.SZ", "period": "DAILY", "adjustment": "NONE", "status": "ready", "version": bars["id"], "attempts": []}]})
    monkeypatch.setattr(cli, "DataStore", lambda: store)
    monkeypatch.setattr(cli, "read_clean_csv", lambda version: pd.read_csv(store.verified_path(store.get(version))))
    monkeypatch.setattr(cli, "reference", lambda symbol: (pd.read_csv(store.verified_path(bars)), bars["id"]))
    monkeypatch.setattr(cli, "capture_code_provenance", lambda: {"tracked_dirty": False, "source_sha256": "test"})
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline recovery cannot connect/download")
    monkeypatch.setattr(cli, "make_client", forbidden)
    monkeypatch.setattr(cli, "calendar", forbidden)
    result = cli.analyse_existing(source, tmp_path / "recovery")
    assert result["new_provider_requests"] == 0 and result["actual_requests"] == 70
    assert result["threshold_exceedances"] == 0 and result["calendar_version"] == cal["id"]
    assert result["items"][0]["cross_source"]["matched_sessions"] == 1
