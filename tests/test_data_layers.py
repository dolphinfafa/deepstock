from pathlib import Path
import json
import numpy as np
import pandas as pd
import pytest
from deepstock.data import DataStore, DataQualityError, read_clean_csv, complete_panel, input_evidence
from deepstock.data.store import clean_frame
from deepstock.data.performance import annualize_net_returns


def fixture_file(root, rows):
    path = root / "prices.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_raw_bytes_immutable_dedupe_trace_and_idempotence(tmp_path):
    source = fixture_file(tmp_path, [{"date": "20240102", "symbol": " spy ", "adjusted_close": 100}] * 2)
    before = source.read_bytes()
    store = DataStore(tmp_path)
    first = store.import_file(source, {"market": "US"})
    second = store.import_file(source, {"market": "US"})
    assert first == second
    assert source.read_bytes() == store.verified_path(first, "raw").read_bytes() == before
    clean = pd.read_csv(store.verified_path(first))
    assert len(clean) == 1 and clean.symbol.iloc[0] == "SPY"
    assert clean._raw_row.iloc[0] == 0
    assert "normalized" in clean._quality_flags.iloc[0]
    assert first["quality"]["duplicate_rows"] == 1


@pytest.mark.parametrize("layer", ["raw", "clean"])
def test_idempotent_import_does_not_accept_tampered_retained_evidence(tmp_path, layer):
    source = fixture_file(tmp_path, [{"date": "2024-01-02", "symbol": "SPY", "adjusted_close": 100}])
    store = DataStore(tmp_path)
    first = store.import_file(source, {"market": "US"})
    store.verified_path(first, layer).write_bytes(b"tampered")
    with pytest.raises(DataQualityError, match="checksum"):
        store.import_file(source, {"market": "US"})


def test_source_race_does_not_publish_a_mismatched_snapshot(tmp_path, monkeypatch):
    source = fixture_file(tmp_path, [{"date": "2024-01-02", "symbol": "SPY", "adjusted_close": 100}])
    import shutil
    original_copy = shutil.copyfile

    def change_during_copy(origin, target):
        original_copy(origin, target)
        source.write_bytes(b"updated")

    monkeypatch.setattr("deepstock.data.store.shutil.copyfile", change_during_copy)
    store = DataStore(tmp_path)
    with pytest.raises(DataQualityError, match="changed during"):
        store.import_file(source, {"market": "US"})
    assert not store.manifests() and not store._alias(source).exists()


def test_entry_rejects_raw_unregistered_changed_source_and_tampered_clean(tmp_path):
    source = fixture_file(tmp_path, [{"date": "2024-01-02", "symbol": "SPY", "adjusted_close": 100}])
    with pytest.raises(DataQualityError, match="not registered"):
        read_clean_csv(source)
    store = DataStore(tmp_path)
    first = store.import_file(source, {"market": "US"})
    assert len(read_clean_csv(source)) == 1
    store.verified_path(first).write_bytes(b"altered")
    with pytest.raises(DataQualityError, match="checksum"):
        read_clean_csv(source)
    source.write_text("changed")
    with pytest.raises(DataQualityError, match="changed"):
        read_clean_csv(source)


def test_unknown_prices_not_interpolated_conflicts_quarantined(tmp_path):
    source = fixture_file(tmp_path, [{"date": "2024-01-02", "symbol": "SPY", "adjusted_close": 100},
                                     {"date": "2024-01-02", "symbol": "SPY", "adjusted_close": 101},
                                     {"date": "2024-01-03", "symbol": "SPY", "adjusted_close": None}])
    m = DataStore(tmp_path).import_file(source, {"market": "US"})
    assert m["status"] == "blocked" and m["quality"]["imputed_rows"] == 0
    assert m["quality"]["conflict_rows"] == 2
    with pytest.raises(DataQualityError, match="blocked"):
        read_clean_csv(source)


def test_inception_trim_logged_but_internal_missing_sessions_rejected():
    frame = pd.DataFrame({"A": [1, 2, 3], "B": [np.nan, 2, 3]}, index=pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]))
    assert len(complete_panel(frame)) == 2
    assert input_evidence()["input_exclusions"][-1]["reason"] == "common_inception"
    with pytest.raises(DataQualityError, match="Missing entire"):
        complete_panel(frame.loc[["2024-01-02", "2024-01-04"], ["A"]])
    frame.loc["2024-01-03", "A"] = np.nan
    with pytest.raises(DataQualityError, match="Missing required"):
        complete_panel(frame[["A"]])


@pytest.mark.parametrize("field,value", [("high", 9), ("low", 11), ("volume", -1), ("close", float("inf"))])
def test_ohlc_and_volume_rejected(field, value):
    row = dict(date="2024-01-02", symbol="SPY", open=10, high=11, low=9, close=10, volume=100)
    row[field] = value
    _, rejected, quality, _ = clean_frame(pd.DataFrame([row]), {"market": "US"})
    assert len(rejected) == 1 and quality["blocking"]


def test_membership_and_adjustment_provenance_are_not_invented(tmp_path):
    source = tmp_path / "membership-0000.json"
    source.write_text(json.dumps({"DELISTED": [{"start": "2005-01-03", "end": "2008-01-04"}]}))
    m = DataStore(tmp_path).import_file(source, {"market": "US", "adjustment": "Norgate TOTALRETURN", "restricted": True})
    assert m["origin"] == "legacy_import" and not m["preview_allowed"]
    assert m["contract"]["adjustment"] == "Norgate TOTALRETURN"


def test_auction_quarantine_is_explicit_and_conflicting_values_never_accepted(tmp_path):
    source = fixture_file(tmp_path, [{"trade_date": "20240102", "ts_code": "000001.SZ", "price": 10},
                                     {"trade_date": "20240103", "ts_code": "000001.SZ", "price": None}])
    m = DataStore(tmp_path).import_file(source, {"market": "CN"})
    with pytest.raises(DataQualityError):
        read_clean_csv(source)
    frame = read_clean_csv(source, allow_quarantine=True)
    assert len(frame) == 1
    assert any(x.get("version") == m["id"] for x in input_evidence()["input_exclusions"])


def test_annualization_includes_cash_sessions_and_tags_short_samples():
    dates = pd.date_range("2024-01-02", periods=252, freq="B")
    returns = pd.Series([.01] + [0] * 251, index=dates)
    m = annualize_net_returns(returns, "oos", "5bp")
    assert m["value"] == pytest.approx(.01) and m["sessions"] == 252 and not m["short_sample"]
    assert annualize_net_returns(returns.iloc[:10], "oos", "5bp")["short_sample"]
    with pytest.raises(ValueError):
        annualize_net_returns(pd.Series([np.nan], index=dates[:1]), "oos", "5bp")
