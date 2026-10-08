"""Synthetic guarantees/transport only, not historical market evidence."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from deepstock.data.membership_contract import (
    CONTRACT_PATH, REPLY_PATH, load_effective_membership_contract, verify_effective_evidence,
)
from deepstock.data.store import DataQualityError, DataStore, digest, write_json

ROOT = Path(__file__).resolve().parents[1]


def copy_contract(tmp_path):
    for relative in (CONTRACT_PATH, REPLY_PATH):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, path)


def test_forwarded_text_records_timezone_and_no_price_or_announcement_authority(tmp_path):
    copy_contract(tmp_path)
    contract, text = load_effective_membership_contract(tmp_path)
    assert contract["reply_at_utc"] == "2026-10-07T23:00:00+00:00"
    assert contract["executable_price_padding_allowed"] is False
    assert contract["announcement_dates_available"] is False
    assert contract["major_exchange_listing_acquired"] is False
    assert "original MIME and headers not supplied" in contract["reply_source"]
    assert "ALLMARKETDAYS evaluates historical constituent status" in text
    # Windows checkout newlines must not be mistaken for different email text.
    (tmp_path / REPLY_PATH).write_bytes(text.replace("\n", "\r\n").encode())
    assert load_effective_membership_contract(tmp_path) == (contract, text)


@pytest.mark.parametrize("change", ["text", "hash", "path", "padding", "price_permission", "announcement", "extra"])
def test_semantics_contract_is_pinned_and_cannot_expand_authority(tmp_path, change):
    copy_contract(tmp_path)
    path = tmp_path / CONTRACT_PATH
    contract = json.loads(path.read_text())
    if change == "text":
        with (tmp_path / REPLY_PATH).open("a") as file:
            file.write("Changed evidence\n")
    elif change == "hash":
        contract["reply_text_sha256_lf"] = "0" * 64
    elif change == "path":
        contract["reply_path"] = "../../unrelated"
    elif change == "padding":
        contract["padding"] = "NONE"
    elif change == "price_permission":
        contract["executable_price_padding_allowed"] = True
    elif change == "announcement":
        contract["announcement_dates_available"] = True
    else:
        contract["unreviewed_field"] = True
    write_json(path, contract)
    with pytest.raises(DataQualityError, match="contract differs|reply text changed"):
        load_effective_membership_contract(tmp_path)


@pytest.mark.parametrize("change", ["contract", "text"])
def test_registered_reply_requires_exact_contract_and_text(tmp_path, change):
    contract, text = load_effective_membership_contract()
    value = {"contract": deepcopy(contract), "text": text}
    if change == "contract":
        value["contract"]["padding"] = "NONE"
    else:
        value["text"] += "changed"
    path = tmp_path / "reply.json"
    write_json(path, value)
    store = DataStore(tmp_path)
    version = store.import_file(path, {"origin": "provider_response", "restricted": True})["id"]
    with pytest.raises(DataQualityError, match="evidence .* mismatch"):
        verify_effective_evidence(store, version, contract)


def setup_capture(tmp_path, monkeypatch, *, remaining_gap=False):
    from scripts import download_norgate_membership as capture
    store = DataStore(tmp_path)
    monkeypatch.setattr(capture, "DataStore", lambda: store)
    monkeypatch.setattr(capture, "capture_code_provenance", lambda: {"tracked_dirty": False, "source_sha256": "0" * 64})
    monkeypatch.setattr(capture.importlib.metadata, "version", lambda name: "1.0.77")
    responses = []
    def save_response(provider, endpoint, value, market, restricted):
        responses.append((endpoint, value))
        path = tmp_path / "responses" / (str(len(responses)) + (".csv.gz" if isinstance(value, pd.DataFrame) else ".json"))
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, pd.DataFrame):
            value.to_csv(path, index=False)
        else:
            write_json(path, value)
        return store.import_file(path, {"origin": "provider_response", "market": market,
                                        "restricted": restricted, "endpoint": endpoint})["id"]
    monkeypatch.setattr(capture, "capture_response", save_response)
    calls = []
    days = pd.bdate_range("2026-08-20", "2026-08-25")
    def member(symbol, index_name, **kwargs):
        calls.append((symbol, index_name, kwargs["padding_setting"]))
        if symbol == "B":
            return pd.DataFrame({"Date": days, "Index Constituent": [1] * len(days)})
        if kwargs["padding_setting"] == "NONE":
            return pd.DataFrame({"Date": days.delete(2), "Index Constituent": [1, 1, 1]})
        # A non-trading effective removal is NOT client forward-fill of the 1.
        view = pd.DataFrame({"Date": days, "Index Constituent": [1, 1, 0, 1]})
        return view.drop(index=2) if remaining_gap else view
    monkeypatch.setitem(sys.modules, "norgatedata", SimpleNamespace(
        watchlist_symbols=lambda _: ["A", "B", "FUTURE"],
        first_quoted_date=lambda s: "2026-10-01" if s == "FUTURE" else "1990-01-01",
        last_quoted_date=lambda _: None, assetid=lambda s: 1 if s == "A" else 2,
        index_constituent_timeseries=member,
        PaddingType=SimpleNamespace(NONE="NONE", ALLMARKETDAYS="ALLMARKETDAYS")))
    return capture, store, calls


def test_effective_capture_is_new_evidence_native_remains_blocked(tmp_path, monkeypatch):
    capture, store, calls = setup_capture(tmp_path, monkeypatch)
    native_dir, effective_dir = tmp_path / "native", tmp_path / "effective"
    original = capture.run(native_dir, "2026-08-20", "2026-08-25", "2026-08-20")
    original_hash = digest(native_dir / "manifest.json")
    assert original["status"] == "blocked"
    assert original["records"][0]["audit"]["missing_required_sessions"] == 1
    fresh = capture.run(effective_dir, "2026-08-20", "2026-08-25", "2026-08-20", effective_membership=True)
    assert fresh["status"] == "required_scope_observed" and fresh["failures"] == []
    assert fresh["full_strategy_admitted"] is False
    assert fresh["missing_history_sessions"] == 0
    assert fresh["symbol_count"] == 3 and len(fresh["records"]) == 3
    assert calls == [(s, "S&P 500 Current & Past", "NONE") for s in ("A", "B")] + [
        (s, "S&P 500", "ALLMARKETDAYS") for s in ("A", "B")]
    mapping, retained, _ = capture.load_capture(effective_dir)
    assert mapping["A"] == [{"start": "2026-08-20", "end": "2026-08-21"},
                            {"start": "2026-08-25", "end": "2026-08-25"}]
    assert mapping["FUTURE"] == [] and retained["membership_contract"]["padding"] == "ALLMARKETDAYS"
    source = store.get(fresh["records"][0]["daily_version"])
    assert fresh["provider_reply_version"] in source["contract"]["upstream_versions"]
    assert source["preview_allowed"] is False
    assert digest(native_dir / "manifest.json") == original_hash
    with pytest.raises(ValueError, match="required membership scope blocked"):
        capture.load_capture(native_dir)


def test_provider_guarantee_does_not_make_remaining_response_gaps_known(tmp_path, monkeypatch):
    capture, _, _ = setup_capture(tmp_path, monkeypatch, remaining_gap=True)
    folder = tmp_path / "effective"
    result = capture.run(folder, "2026-08-20", "2026-08-25", "2026-08-20", effective_membership=True)
    assert result["status"] == "blocked" and result["records"][0]["audit"]["missing_required_sessions"] == 1
    with pytest.raises(ValueError, match="required membership scope blocked"):
        capture.load_capture(folder)


def test_package_revision_requires_new_review_before_capture(tmp_path, monkeypatch):
    capture, _, calls = setup_capture(tmp_path, monkeypatch)
    monkeypatch.setattr(capture.importlib.metadata, "version", lambda name: "1.0.78")
    with pytest.raises(ValueError, match="package version"):
        capture.run(tmp_path / "effective", "2026-08-20", "2026-08-25", "2026-08-20", effective_membership=True)
    assert calls == [] and not (tmp_path / "effective").exists()


def test_offline_comparison_preserves_old_gap_and_catches_conflicting_observations():
    from scripts.audit_effective_membership_update import compare_daily
    days = pd.bdate_range("2026-08-20", "2026-08-25")
    old = pd.DataFrame({"date": days.delete(2), "weight": [1, 1, 1]})
    fresh = pd.DataFrame({"date": days, "weight": [1, 1, 0, 1]})
    prior = old.copy(deep=True)
    record = {"symbol": "A", "first_quote": "1990-01-01", "last_quote": None}
    result = compare_daily(old, fresh, days, record, days[0])
    assert result["old_missing_required_security_dates"] == 1
    assert result["provider_supplied_required_security_dates"] == 1
    assert result["supplied_value_counts"] == {"0": 1}
    assert result["observed_value_differences"] == result["remaining_required_security_dates"] == 0
    pd.testing.assert_frame_equal(old, prior)
    fresh.loc[0, "weight"] = 0
    assert compare_daily(old, fresh, days, record, days[0])["observed_value_differences"] == 1


def test_offline_full_comparison_verifies_inputs_and_never_admits_strategy(tmp_path, monkeypatch):
    from scripts import audit_effective_membership_update as audit
    capture, _, _ = setup_capture(tmp_path, monkeypatch)
    monkeypatch.setattr(audit, "capture_code_provenance", capture.capture_code_provenance)
    native_dir, effective_dir = tmp_path / "native", tmp_path / "effective"
    capture.run(native_dir, "2026-08-20", "2026-08-25", "2026-08-20")
    capture.run(effective_dir, "2026-08-20", "2026-08-25", "2026-08-20", effective_membership=True)
    checksum = digest(native_dir / "manifest.json")
    output = tmp_path / "comparison.json"
    result = audit.run(native_dir, effective_dir, output)
    assert result["status"] == "effective_membership_reconciled"
    assert result["old_missing_required_security_dates"] == result["provider_supplied_required_security_dates"] == 1
    assert result["full_strategy_admitted"] is False and result["symbol_count"] == 3
    assert digest(native_dir / "manifest.json") == checksum
    with pytest.raises(FileExistsError, match="earlier comparison"):
        audit.run(native_dir, effective_dir, output)
