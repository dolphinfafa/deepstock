"""Synthetic membership tests only; not historical market evidence."""
from types import SimpleNamespace
import json
import sys

import pandas as pd
import pytest

from deepstock.data.membership import native_membership_view
from deepstock.data.store import DataQualityError


def native(days, values):
    return pd.DataFrame({"Date": days, "Index Constituent": values})


def test_native_absence_is_not_zero_and_does_not_extend_a_positive_span():
    days = pd.bdate_range("2026-08-20", periods=5)
    source = native(days.delete(2), [1, 1, 1, 0])
    original = source.copy(deep=True)
    view, spans, audit = native_membership_view(source, days, "A", "1990-01-01", None, days[1])
    assert len(view) == 4 and days[2] not in set(view.date)
    assert spans == [{"start": str(days[0].date()), "end": str(days[1].date())},
                     {"start": str(days[3].date()), "end": str(days[3].date())}]
    assert audit["status"] == "blocked" and audit["missing_required_sessions"] == 1
    pd.testing.assert_frame_equal(source, original)


def test_pre_evaluation_unknown_is_retained_without_claiming_long_history_complete():
    days = pd.bdate_range("2026-08-20", periods=5)
    _, _, audit = native_membership_view(native(days[2:], [0, 1, 1]), days, "A", days[0], None, days[2])
    assert audit["status"] == "required_scope_observed"
    assert audit["missing_history_sessions"] == 2
    assert audit["missing_required_sessions"] == 0


def test_verified_terminal_boundary_and_ipo_exclude_only_outside_lifetime():
    days = pd.bdate_range("2026-08-20", periods=5)
    _, _, audit = native_membership_view(pd.DataFrame(), days, "OLD", "1990-01-01", "2004-12-03", days[0])
    assert audit["status"] == "outside_requested_quote_lifetime"
    _, _, audit = native_membership_view(native(days[2:4], [0, 1]), days, "IPO", days[2], days[3], days[0])
    assert audit["missing_history_sessions"] == 0
    with pytest.raises(DataQualityError, match="Empty membership"):
        native_membership_view(pd.DataFrame(), days, "UNKNOWN", "1990-01-01", None, days[0])
    with pytest.raises(DataQualityError, match="boundaries"):
        native_membership_view(pd.DataFrame(), days, "UNKNOWN", None, None, days[0])


@pytest.mark.parametrize("case", ["invalid_value", "duplicate", "unsorted", "outside_lifetime", "null_date"])
def test_native_schema_failure_never_cast_to_boolean(case):
    days = pd.bdate_range("2026-08-20", periods=3)
    frame = native(days, [0, 1, 0])
    first = days[0]
    if case == "invalid_value":
        frame.loc[1, "Index Constituent"] = 2
    elif case == "duplicate":
        frame.loc[1, "Date"] = days[0]
    elif case == "unsorted":
        frame = frame.iloc[::-1]
    elif case == "outside_lifetime":
        first = days[1]
    else:
        frame.loc[1, "Date"] = pd.NaT
    with pytest.raises(DataQualityError, match="Invalid native"):
        native_membership_view(frame, days, "A", first, None, days[0])


def test_fresh_collector_uses_new_full_pool_not_535_and_retains_price_version(tmp_path, monkeypatch):
    from scripts import prepare_granville_stock_data as collector
    from scripts import download_norgate_membership as capture
    from deepstock.data.store import digest
    days = pd.bdate_range("2026-08-20", "2026-08-25")
    folder, previous, output = tmp_path / "capture", tmp_path / "previous", tmp_path / "new"
    folder.mkdir()
    (folder / "manifest.json").write_text("{}")
    previous.mkdir()
    (previous / "prices").mkdir()
    (previous / "prices/A.csv.gz").write_bytes(b"synthetic placeholder")
    cfg = {"source_start": "2026-08-20", "evaluation_start": "2026-08-21", "evaluation_end": "2026-08-25"}
    (previous / "manifest.json").write_text(json.dumps({"market": "US", "source_start": cfg["source_start"], "source_end": cfg["evaluation_end"]}))
    monkeypatch.setattr(collector, "ROOT", tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config/granville_portfolio_v1.json").write_text("{}")
    mapping = {s: [{"start": cfg["source_start"], "end": cfg["evaluation_end"]}] for s in ["A", "NEW"]}
    manifest = {"source_start": cfg["source_start"], "source_end": cfg["evaluation_end"], "required_start": cfg["source_start"],
                "interval_version": "i" * 64, "records": [{"symbol": s, "last_quote": None} for s in mapping]}
    monkeypatch.setattr(capture, "load_capture", lambda path: (mapping, manifest, "c" * 64))
    called = []
    def get_prices(symbol, **kwargs):
        called.append((symbol, kwargs["stock_price_adjustment_setting"], kwargs["padding_setting"]))
        return pd.DataFrame({"Date": days, "Open": 10, "High": 11, "Low": 9, "Close": 10, "Volume": 10000, "Turnover": 100000})
    monkeypatch.setitem(sys.modules, "norgatedata", SimpleNamespace(price_timeseries=get_prices,
                         StockPriceAdjustmentType=SimpleNamespace(NONE="NONE", TOTALRETURN="TOTALRETURN"),
                         PaddingType=SimpleNamespace(NONE="NONE")))
    cached = pd.DataFrame({"date": days, "symbol": "A"})
    cached.attrs["data_version"] = "p" * 64
    monkeypatch.setattr(collector, "read_clean_csv", lambda path: cached)
    saved = []
    monkeypatch.setattr(collector, "persist", lambda frame, path, market, kind, upstream=(), **kw: saved.append((path, list(upstream), kw)) or frame)
    monkeypatch.setattr(collector, "capture_response", lambda *a, **kw: "r" * 64)
    result = collector.collect_us(output, cfg, folder, previous)
    assert result["symbols"] == ["A", "NEW"] and result["symbol_count"] == 2
    assert result["reused_verified_price_count"] == 1 and result["new_price_count"] == 1
    assert len(called) == 2 and all(s == "NEW" and pad == "NONE" for s, _, pad in called)
    assert saved[0][1] == ["c" * 64, "i" * 64]
    assert next(s for s in saved if s[0].name == "A.csv.gz")[1] == ["p" * 64]
    assert result["fresh_membership_manifest_sha256"] == digest(folder / "manifest.json")


def test_raw_ohlc_inventory_requires_same_native_dates_and_preserves_both_layers():
    from scripts.download_norgate_stock_ohlc_inventory import stock_frame
    days = pd.bdate_range("2026-08-20", periods=3)
    raw = pd.DataFrame({"Date": days, "Open": 10, "High": 12, "Low": 9, "Close": 11, "Volume": 100, "Turnover": 1100})
    adjusted = raw.copy(deep=True)
    adjusted[["Open", "High", "Low", "Close"]] *= 2
    result = stock_frame(raw, adjusted, "A")
    assert result.close.tolist() == [11] * 3
    assert result.adjusted_close.tolist() == [22] * 3
    assert result.volume.tolist() == [100] * 3
    with pytest.raises(ValueError, match="date mismatch"):
        stock_frame(raw, adjusted.iloc[:-1], "A")
    with pytest.raises(ValueError, match="ordered native"):
        stock_frame(raw.iloc[::-1], adjusted.iloc[::-1], "A")
    with pytest.raises(ValueError, match="Complete raw"):
        stock_frame(raw, adjusted.drop(columns="High"), "A")


def test_evidence_only_loading_does_not_admit_blocked_membership(tmp_path, monkeypatch):
    from scripts import download_norgate_membership as capture
    from deepstock.data.store import digest
    manifest = {"status": "blocked", "failures": [{"symbol": "A", "error": "native gap"}], "records": [], "interval_version": "i" * 64}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "manifest-version.txt").write_text("v" * 64)
    monkeypatch.setattr(capture, "DataStore", lambda: SimpleNamespace(
        get=lambda version: {"raw_sha256": digest(tmp_path / "manifest.json")},
        verified_path=lambda *args: tmp_path / "manifest.json", resolve=lambda path: {"id": "i" * 64}))
    monkeypatch.setattr(capture, "read_clean_json", lambda path: {})
    with pytest.raises(ValueError, match="required membership scope blocked"):
        capture.load_capture(tmp_path)
    _, retained, _ = capture.load_capture(tmp_path, evidence_only=True)
    assert retained["status"] == "blocked"
