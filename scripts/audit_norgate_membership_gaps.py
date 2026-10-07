"""Evidence-only native-member diagnostics; padded responses never admit inputs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd

from deepstock.data.capture import capture_response
from deepstock.data.membership import native_membership_view
from deepstock.data.store import DataStore, digest, read_clean_csv, write_json
from scripts.download_norgate_membership import load_capture
from scripts.rerun_clean_research import capture_code_provenance


def compare_probe(native, padded, retained, missing):
    """Agreement on observed dates does not establish truth on missing dates."""
    series = []
    for frame in (native, padded, retained):
        if {"Date", "Index Constituent"}.difference(frame):
            raise ValueError("Probe Date and Index Constituent required")
        dates = pd.DatetimeIndex(pd.to_datetime(frame.Date, errors="coerce"))
        values = pd.to_numeric(frame["Index Constituent"], errors="coerce")
        if (dates.hasnans or dates.tz is not None or dates.has_duplicates
                or not dates.is_monotonic_increasing or not values.isin([0, 1]).all()):
            raise ValueError("Unique ordered probe dates and 0/1 values required")
        series.append(pd.Series(values.to_numpy(), index=dates))
    unpadded, filled, old = series
    common = unpadded.index.intersection(old.index)
    overlapping = unpadded.index.intersection(filled.index)
    supplied = filled.reindex(pd.DatetimeIndex(missing)).dropna()
    return {
        "canonical_native_rows": len(unpadded), "padded_rows": len(filled),
        "canonical_vs_retained_date_differences": len(unpadded.index.symmetric_difference(old.index)),
        "canonical_vs_retained_value_differences": int(unpadded.loc[common].ne(old.loc[common]).sum()),
        "padded_vs_native_observed_value_differences": int(unpadded.loc[overlapping].ne(filled.loc[overlapping]).sum()),
        "missing_required_dates_returned_with_padding": len(supplied),
        "padded_missing_value_counts": {str(int(k)): int(v) for k, v in supplied.value_counts().items()},
        "missing_day_truth_verified": False, "backtest_admitted": False,
        "policy": "Padding availability and observed-date agreement are diagnostic only, not independent membership evidence",
    }


def diagnose_record(record, frame, calendar, required_start):
    """Recompute with current boundary validation; never rewrite a prior capture."""
    native = frame.rename(columns={"date": "Date", "weight": "Index Constituent"})
    view, _, audit = native_membership_view(native, calendar, record["symbol"],
                                          record.get("first_quote"), record.get("last_quote"), required_start)
    if view.empty:
        missing = pd.DatetimeIndex([])
    else:
        first = pd.Timestamp(record["first_quote"])
        last = pd.Timestamp(record["last_quote"]) if record.get("last_quote") else calendar[-1]
        expected = calendar[(calendar >= first) & (calendar <= last) & (calendar >= pd.Timestamp(required_start))]
        missing = expected.difference(pd.DatetimeIndex(view.date))
    positive = view.loc[view.weight.eq(1), "date"] if not view.empty else pd.Series(dtype="datetime64[ns]")
    result = {"symbol": record["symbol"], "first_quote": record.get("first_quote"),
              "last_quote": record.get("last_quote"), "assetid": record.get("assetid"),
              "previous_error": record.get("error"), "recomputed_audit": audit,
              "last_observed_positive_date": str(positive.max().date()) if not positive.empty else None,
              "last_missing_required_date": str(missing[-1].date()) if len(missing) else None,
              "retained_native_version": record.get("native_version"),
              "retained_daily_version": record.get("daily_version")}
    return result, native, missing


def run(capture_dir, output_dir, probe_provider=False):
    if output_dir.exists():
        raise FileExistsError("Preserve prior diagnostic evidence")
    code = capture_code_provenance()
    if code["tracked_dirty"]:
        raise ValueError("Committed clean source required")
    _, manifest, version = load_capture(capture_dir, evidence_only=True)
    calendar = xcals.get_calendar("XNYS", start=manifest["source_start"],
                                 end=manifest["source_end"]).sessions.tz_localize(None)
    records, local_details, sources = [], [], []
    for record in manifest["records"]:
        if not record.get("error") and not record.get("audit", {}).get("missing_required_sessions"):
            continue
        frame = read_clean_csv(capture_dir / record["daily_file"]) if record.get("daily_file") else pd.DataFrame()
        result, native, missing = diagnose_record(record, frame, calendar, manifest["required_start"])
        local_details.append({"symbol": record["symbol"], "missing_required_dates": [str(d.date()) for d in missing]})
        if probe_provider and len(missing):
            import norgatedata as n
            import importlib.metadata
            frames, references = [], []
            try:
                for padding in (n.PaddingType.NONE, n.PaddingType.ALLMARKETDAYS):
                    probe = pd.DataFrame(n.index_constituent_timeseries(
                        record["symbol"], "S&P 500", padding_setting=padding,
                        start_date=manifest["required_start"], end_date=manifest["source_end"]))
                    ref = capture_response("Norgate", "membership_semantics_probe_" + padding.name,
                                           probe, "US", restricted=True)
                    sources.append(ref)
                    references.append({"padding": padding.name, "version": ref,
                                       "raw_sha256": DataStore().get(ref)["raw_sha256"]})
                    frames.append(probe)
                retained = native.loc[pd.to_datetime(native.Date).ge(pd.Timestamp(manifest["required_start"]))]
                result["provider_probe"] = {**compare_probe(*frames, retained, missing),
                                            "index_name": "S&P 500", "package_version": importlib.metadata.version("norgatedata")}
            except Exception as error:
                result["provider_probe"] = {"status": "failed_not_admitted", "error": str(error)[:250],
                                            "missing_day_truth_verified": False, "backtest_admitted": False}
            result["provider_probe"]["responses"] = references
        records.append(result)
    if capture_code_provenance()["source_sha256"] != code["source_sha256"]:
        raise ValueError("Source changed during gap diagnosis")
    output_dir.mkdir(parents=True, exist_ok=False)
    details_path = output_dir / "local-gap-dates.json"
    write_json(details_path, {"records": local_details, "restricted": True,
                              "policy": "Licensed derived date records remain on the quantitative computer"})
    details_version = DataStore().import_file(details_path, {
        "market": "US", "provider": "Norgate", "restricted": True, "origin": "derived_provider_export",
        "endpoint": "membership_gap_diagnostic", "upstream_versions": [version, *sources],
        "unknown_policy": "Evidence only; no imputation or strategy admission"})["id"]
    value = {"status": "membership_gap_diagnostic_not_backtest_input", "backtest_admitted": False,
             "captured_at_utc": datetime.now(timezone.utc).isoformat(), "code_provenance": code,
             "capture_manifest_version": version, "capture_manifest_sha256": digest(capture_dir / "manifest.json"),
             "original_capture_status": manifest["status"], "required_start": manifest["required_start"],
             "source_end": manifest["source_end"], "symbol_count": manifest["symbol_count"],
             "blocked_security_count": sum(bool(r["recomputed_audit"]["missing_required_sessions"]) for r in records),
             "missing_required_security_dates": sum(r["recomputed_audit"]["missing_required_sessions"] for r in records),
             "records": records, "local_details_version": details_version,
             "local_details_sha256": digest(details_path), "provider_probe_requested": probe_provider,
             "decision": "Retain the admission block. Ask the provider to document non-quote-day indicator semantics or supply independent daily membership evidence; do not use padded responses as truth."}
    write_json(output_dir / "summary.json", value)
    print(json.dumps(value, ensure_ascii=False, indent=2), flush=True)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--probe-provider", action="store_true", help="Diagnostic member API calls only; no OHLC or admission")
    args = parser.parse_args()
    run(args.capture_dir, args.output_dir, args.probe_provider)


if __name__ == "__main__":
    main()
