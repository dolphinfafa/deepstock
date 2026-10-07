"""Synthetic diagnostic fixtures, not evidence of historical membership."""
import pandas as pd
import pytest

from scripts.audit_norgate_membership_gaps import compare_probe, diagnose_record


def frame(days, values):
    return pd.DataFrame({"Date": days, "Index Constituent": values})


def test_padding_never_certifies_missing_day_truth_even_if_all_values_agree():
    days = pd.bdate_range("2026-08-20", periods=4)
    native = frame(days.delete(1), [0, 0, 0])
    original = native.copy(deep=True)
    result = compare_probe(native, frame(days, [0, 0, 0, 0]), native, days[1:2])
    assert result["missing_required_dates_returned_with_padding"] == 1
    assert result["padded_missing_value_counts"] == {"0": 1}
    assert result["missing_day_truth_verified"] is False and result["backtest_admitted"] is False
    pd.testing.assert_frame_equal(native, original)


def test_probe_conflicts_and_partial_gap_response_are_explicit():
    days = pd.bdate_range("2026-08-20", periods=4)
    native = frame(days[:2], [1, 0])
    old = frame(days[:1], [0])
    result = compare_probe(native, frame(days[:3], [0, 0, 1]), old, days[2:])
    assert result["canonical_vs_retained_date_differences"] == 1
    assert result["canonical_vs_retained_value_differences"] == 1
    assert result["padded_vs_native_observed_value_differences"] == 1
    assert result["missing_required_dates_returned_with_padding"] == 1
    assert result["padded_missing_value_counts"] == {"1": 1}
    assert result["backtest_admitted"] is False


@pytest.mark.parametrize("bad", ["duplicate", "invalid_value", "unsorted"])
def test_invalid_probe_is_never_cast_or_deduplicated(bad):
    days = pd.bdate_range("2026-08-20", periods=3)
    native = frame(days, [0, 1, 0])
    broken = native.copy(deep=True)
    if bad == "duplicate":
        broken.loc[1, "Date"] = days[0]
    elif bad == "invalid_value":
        broken.loc[1, "Index Constituent"] = 2
    else:
        broken = broken.iloc[::-1]
    with pytest.raises(ValueError, match="Unique ordered"):
        compare_probe(native, broken, native, days[:0])


def test_current_boundary_diagnosis_keeps_old_error_and_real_gaps():
    days = pd.bdate_range("2026-08-20", periods=4)
    record = {"symbol": "FUTURE", "first_quote": "2026-10-01", "last_quote": None,
              "error": "old boundary failure"}
    result, _, missing = diagnose_record(record, pd.DataFrame(), days, days[0])
    assert result["previous_error"] == "old boundary failure"
    assert result["recomputed_audit"]["status"] == "outside_requested_quote_lifetime"
    assert len(missing) == 0 and record["error"] == "old boundary failure"
    record = {"symbol": "A", "first_quote": "1990-01-01", "last_quote": None}
    retained = pd.DataFrame({"date": days.delete(1), "weight": [0, 0, 0]})
    result, _, missing = diagnose_record(record, retained, days, days[0])
    assert result["recomputed_audit"]["status"] == "blocked" and len(missing) == 1
    assert result["last_observed_positive_date"] is None


def test_summary_remains_evidence_only_and_never_updates_the_capture(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from scripts import audit_norgate_membership_gaps as audit
    days = pd.bdate_range("2026-08-20", periods=4)
    capture = tmp_path / "capture"
    capture.mkdir()
    original = b'{"status":"blocked","original":"untouched"}'
    (capture / "manifest.json").write_bytes(original)
    manifest = {"status": "blocked", "source_start": str(days[0].date()), "source_end": str(days[-1].date()),
                "required_start": str(days[0].date()), "symbol_count": 1,
                "records": [{"symbol": "A", "first_quote": "1990-01-01", "last_quote": None,
                             "daily_file": "A.csv.gz", "error": "native gap"}]}
    monkeypatch.setattr(audit, "capture_code_provenance", lambda: {"tracked_dirty": False, "source_sha256": "same"})
    monkeypatch.setattr(audit, "load_capture", lambda *a, **k: ({}, manifest, "v" * 64))
    monkeypatch.setattr(audit.xcals, "get_calendar", lambda *a, **k: SimpleNamespace(sessions=days.tz_localize("UTC")))
    monkeypatch.setattr(audit, "read_clean_csv", lambda *a: pd.DataFrame({"date": days.delete(1), "weight": [0, 0, 0]}))
    monkeypatch.setattr(audit, "DataStore", lambda: SimpleNamespace(import_file=lambda *a, **k: {"id": "d" * 64}))
    output = tmp_path / "diagnostic"
    result = audit.run(capture, output)
    assert result["status"] == "membership_gap_diagnostic_not_backtest_input"
    assert result["backtest_admitted"] is False and result["missing_required_security_dates"] == 1
    assert (capture / "manifest.json").read_bytes() == original
    assert "missing_required_dates" not in json.dumps(result)
    assert json.loads((output / "local-gap-dates.json").read_text())["restricted"] is True
    with pytest.raises(FileExistsError, match="Preserve"):
        audit.run(capture, output)
