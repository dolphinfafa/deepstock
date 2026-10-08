"""Price-free reconciliation of immutable native and effective member captures."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd

from deepstock.data.membership import native_membership_view
from deepstock.data.store import digest, read_clean_csv, write_json
from scripts.download_norgate_membership import load_capture
from scripts.rerun_clean_research import capture_code_provenance


def compare_daily(old, new, calendar, record, required_start):
    """Report provider-supplied missing states; never modify either frame."""
    original = old.rename(columns={"date": "Date", "weight": "Index Constituent"})
    view, _, audit = native_membership_view(original, calendar, record["symbol"],
                                          record["first_quote"], record["last_quote"], required_start)
    first = pd.Timestamp(record["first_quote"])
    last = pd.Timestamp(record["last_quote"]) if record["last_quote"] else calendar[-1]
    expected = calendar[(calendar >= first) & (calendar <= last) & (calendar >= pd.Timestamp(required_start))]
    previous = pd.Series(view.weight.to_numpy(), index=pd.DatetimeIndex(view.date))
    fresh = pd.Series(new.weight.to_numpy(), index=pd.DatetimeIndex(pd.to_datetime(new.date))) if not new.empty else pd.Series(dtype=int)
    missing = expected.difference(previous.index)
    supplied = fresh.reindex(missing).dropna()
    common = previous.index.intersection(fresh.index)
    return {"symbol": record["symbol"], "old_missing_required_security_dates": len(missing),
            "provider_supplied_required_security_dates": len(supplied),
            "remaining_required_security_dates": len(missing) - len(supplied),
            "supplied_value_counts": {str(int(k)): int(v) for k, v in supplied.value_counts().items()},
            "old_observed_dates_absent_in_new": len(previous.index.difference(fresh.index)),
            "observed_value_differences": int(previous.loc[common].ne(fresh.loc[common]).sum()),
            "old_recomputed_status": audit["status"]}


def run(native_dir, effective_dir, output):
    if output.exists():
        raise FileExistsError("Preserve earlier comparison")
    code = capture_code_provenance()
    if code["tracked_dirty"]:
        raise ValueError("Committed clean source required")
    _, old, old_version = load_capture(native_dir, evidence_only=True)
    _, fresh, fresh_version = load_capture(effective_dir)
    if not fresh.get("membership_contract") or old.get("membership_contract"):
        raise ValueError("Compare a native NONE capture to a reviewed effective v1 capture")
    for field in ("source_start", "source_end", "required_start"):
        if old[field] != fresh[field]:
            raise ValueError("Comparison dates differ; no sample trimming")
    previous = {r["symbol"]: r for r in old["records"]}
    updated = {r["symbol"]: r for r in fresh["records"]}
    if set(previous) != set(updated):
        raise ValueError("Historical watchlist changed; no silently reduced comparison")
    calendar = xcals.get_calendar("XNYS", start=fresh["source_start"], end=fresh["source_end"]).sessions.tz_localize(None)
    records = []
    for symbol, record in updated.items():
        prior = previous[symbol]
        if record["assetid"] != prior["assetid"]:
            raise ValueError("Security asset identity changed")
        old_frame = read_clean_csv(native_dir / prior["daily_file"], version=prior["daily_version"]) if prior.get("daily_file") else pd.DataFrame()
        new_frame = read_clean_csv(effective_dir / record["daily_file"], version=record["daily_version"]) if record.get("daily_file") else pd.DataFrame()
        records.append(compare_daily(old_frame, new_frame, calendar, record, fresh["required_start"]))
    changed = [r for r in records if r["old_missing_required_security_dates"] or r["observed_value_differences"] or r["old_observed_dates_absent_in_new"]]
    conflicts = sum(r["observed_value_differences"] + r["old_observed_dates_absent_in_new"] + r["remaining_required_security_dates"] for r in records)
    if capture_code_provenance()["source_sha256"] != code["source_sha256"]:
        raise ValueError("Source changed during comparison")
    result = {"status": "blocked_comparison" if conflicts else "effective_membership_reconciled",
              "full_strategy_admitted": False, "captured_at_utc": datetime.now(timezone.utc).isoformat(),
              "code_provenance": code, "native_manifest_version": old_version,
              "native_manifest_sha256": digest(native_dir / "manifest.json"),
              "effective_manifest_version": fresh_version,
              "effective_manifest_sha256": digest(effective_dir / "manifest.json"),
              "provider_reply_version": fresh["provider_reply_version"], "symbol_count": len(records),
              "old_missing_required_security_dates": sum(r["old_missing_required_security_dates"] for r in records),
              "provider_supplied_required_security_dates": sum(r["provider_supplied_required_security_dates"] for r in records),
              "remaining_required_security_dates": sum(r["remaining_required_security_dates"] for r in records),
              "observed_value_differences": sum(r["observed_value_differences"] for r in records),
              "old_observed_dates_absent_in_new": sum(r["old_observed_dates_absent_in_new"] for r in records),
              "changed_records": changed,
              "policy": "Provider-confirmed effective member semantics only; native bytes unchanged, no price fill or restored old performance"}
    write_json(output, result)
    return result


def main():
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--effective-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.native_dir, args.effective_dir, args.output)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if result["status"] != "effective_membership_reconciled":
        raise SystemExit("Reconciliation blocked; no strategy run")


if __name__ == "__main__":
    main()
