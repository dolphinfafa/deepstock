"""Fresh full-watchlist membership capture; default native, explicit effective v1."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd

from deepstock.data.capture import capture_response
from deepstock.data.membership import audit_us_membership, native_membership_view
from deepstock.data.membership_contract import load_effective_membership_contract, verify_effective_evidence
from deepstock.data.store import ROOT, DataStore, digest, read_clean_csv, read_clean_json, write_json
from scripts.rerun_clean_research import capture_code_provenance


def load_capture(folder, *, evidence_only=False):
    """Hash-verified registered manifest and derived/native inputs, never globs."""
    store = DataStore()
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    version = (folder / "manifest-version.txt").read_text(encoding="utf-8").strip()
    registered = store.get(version)
    if digest(folder / "manifest.json") != registered["raw_sha256"]:
        raise ValueError("Membership capture manifest changed")
    store.verified_path(registered, "raw")
    if manifest.get("membership_contract"):
        contract, _ = load_effective_membership_contract()
        if manifest["membership_contract"] != contract:
            raise ValueError("Effective membership capture contract changed")
        verify_effective_evidence(store, manifest["provider_reply_version"], contract)
    if not evidence_only and (manifest["status"] != "required_scope_observed" or manifest["failures"]):
        raise ValueError("Full-watchlist required membership scope blocked")
    mapping = read_clean_json(folder / "membership-native.json")
    if store.resolve(folder / "membership-native.json")["id"] != manifest["interval_version"]:
        raise ValueError("Captured membership interval version changed")
    for record in manifest["records"]:
        for key in ["metadata_version", "native_version"]:
            if record.get(key):
                source = store.get(record[key])
                store.verified_path(source, "raw")
        if record.get("daily_file"):
            frame = read_clean_csv(folder / record["daily_file"])
            if frame.attrs["data_version"] != record["daily_version"]:
                raise ValueError("Captured daily indicator version changed")
    return mapping, manifest, version


def run(output, start, end, required_start, *, effective_membership=False):
    if output.exists():
        raise FileExistsError("Preserve prior membership capture; choose a new output directory")
    import norgatedata as n
    code = capture_code_provenance()
    if code["tracked_dirty"]:
        raise ValueError("Committed clean source required")
    contract, reply_version = None, None
    if effective_membership:
        contract, reply_text = load_effective_membership_contract()
        if importlib.metadata.version("norgatedata") != contract["package_version"]:
            raise ValueError("Effective membership v1 requires reviewed Norgate package version")
        reply_version = capture_response("Norgate", "user_forwarded_membership_support_reply", {
            "contract": contract, "text": reply_text,
            "provenance": "User-pasted reply, not original email MIME or independently verified headers"}, "US", restricted=True)
    padding_name = contract["padding"] if contract else "NONE"
    index_name = contract["index_name"] if contract else "S&P 500 Current & Past"
    output.mkdir(parents=True, exist_ok=False)
    calendar = xcals.get_calendar("XNYS", start=start, end=end).sessions.tz_localize(None)
    if calendar.empty or pd.Timestamp(required_start) < calendar[0] or pd.Timestamp(required_start) > calendar[-1]:
        raise ValueError("Required scope outside capture calendar")
    watchlist = "S&P 500 Current & Past"
    symbols = n.watchlist_symbols(watchlist)
    if not symbols or len(symbols) != len(set(symbols)):
        raise ValueError("Unique complete historical watchlist required")
    universe_version = capture_response("Norgate", "complete_historical_watchlist", {
        "watchlist": watchlist, "symbols": symbols, "start": start, "end": end,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat()}, "US", restricted=True)
    records, failures, mapping = [], [], {}
    store = DataStore()
    for index, symbol in enumerate(sorted(symbols)):
        record = {"symbol": symbol}
        try:
            first, last = n.first_quoted_date(symbol), n.last_quoted_date(symbol)
            record.update(first_quote=first, last_quote=last, assetid=n.assetid(symbol))
            record["metadata_version"] = capture_response("Norgate", "quote_lifetime", record.copy(), "US", restricted=True)
            # Still query each in-range symbol, including zero/removed names.
            outside = (last is not None and pd.Timestamp(last) < calendar[0]) or (first is not None and pd.Timestamp(first) > calendar[-1])
            native = pd.DataFrame() if outside else pd.DataFrame(n.index_constituent_timeseries(
                symbol, index_name, padding_setting=getattr(n.PaddingType, padding_name), start_date=start, end_date=end))
            if not outside:
                record["native_version"] = capture_response("Norgate", "index_constituent_timeseries_" + padding_name, native, "US", restricted=True)
            view, intervals, audit = native_membership_view(native, calendar, symbol, first, last, required_start)
            if contract:
                audit["policy"] = "Provider-evaluated effective status on ALLMARKETDAYS; missing responses stay unknown; no client fill or executable prices"
                audit["membership_contract_id"] = contract["contract_id"]
            record.update(audit=audit)
            mapping[symbol] = intervals
            if not view.empty:
                file = "daily/" + symbol + ".csv.gz"
                path = output / file
                path.parent.mkdir(parents=True, exist_ok=True)
                view.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
                registered = store.import_file(path, {
                    "market": "US", "provider": "Norgate", "restricted": True,
                    "origin": "derived_provider_export", "kind": "historical_membership_daily",
                    "upstream_versions": [record["native_version"], record["metadata_version"]] + ([reply_version] if reply_version else []),
                    "index_name": index_name, "padding": padding_name, "timezone": "America/New_York"})
                if registered["status"] != "ready":
                    raise ValueError("Native daily membership cleaning blocked")
                record.update(daily_file=file, daily_version=registered["id"])
                if audit["missing_required_sessions"]:
                    raise ValueError("Membership response has missing required sessions")
        except Exception as error:
            record["error"] = str(error)[:250]
            failures.append({"symbol": symbol, "error": record["error"]})
        records.append(record)
        if (index + 1) % 25 == 0 or index + 1 == len(symbols):
            print(json.dumps({"processed": index + 1, "total": len(symbols), "failures": len(failures)}), flush=True)
    after = n.watchlist_symbols(watchlist)
    end_universe_version = capture_response("Norgate", "complete_historical_watchlist_end", {"watchlist": watchlist, "symbols": after}, "US", restricted=True)
    if set(after) != set(symbols):
        failures.append({"symbol": None, "error": "Historical watchlist changed during capture"})
    intervals_path = output / "membership-native.json"
    write_json(intervals_path, mapping)
    interval_version = store.import_file(intervals_path, {
        "market": "US", "provider": "Norgate", "restricted": True, "origin": "derived_provider_export",
        "upstream_versions": [r["daily_version"] for r in records if r.get("daily_version")],
        "unknown_policy": "Split positive spans at every unobserved exchange session; no imputation"})["id"]
    rows = [{"symbol": s, "date": r["start"], "end": r["end"]} for s, spans in mapping.items() for r in spans]
    coverage = audit_us_membership(calendar, pd.DataFrame(rows), required_start, end) if rows else {"status": "blocked"}
    if coverage["status"] == "blocked":
        failures.append({"symbol": None, "error": "Aggregate required membership coverage missing"})
    if capture_code_provenance()["source_sha256"] != code["source_sha256"]:
        failures.append({"symbol": None, "error": "Source changed during capture"})
    missing_history = sum(r.get("audit", {}).get("missing_history_sessions", 0) for r in records)
    manifest = {
        "status": "blocked" if failures else "required_scope_observed", "market": "US", "watchlist": watchlist,
        "source_start": start, "source_end": end, "required_start": required_start,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "code_provenance": code,
        "universe_version": universe_version, "end_universe_version": end_universe_version,
        "interval_version": interval_version, "symbol_count": len(symbols), "records": records, "failures": failures,
        "coverage": coverage, "missing_history_sessions": missing_history,
        "complete_long_history_indicator_coverage": not missing_history and not failures,
        "policy": "All historical codes retained; native unpadded observations and quote boundaries; no present-survivor substitution or long-history completeness claim with gaps"}
    if contract:
        manifest.update(membership_contract=contract, provider_reply_version=reply_version,
                        index_name=index_name, padding=padding_name,
                        full_strategy_admitted=False,
                        policy="All historical codes retained; provider-evaluated effective membership; no price padding, announcement-date, OTC-execution or terminal-accounting claim")
    write_json(output / "manifest.json", manifest)
    registered = store.import_file(output / "manifest.json", {"market": "US", "provider": "Norgate", "restricted": True,
        "origin": "provider_response", "endpoint": "membership_capture_manifest"})
    (output / "manifest-version.txt").write_text(registered["id"] + "\n", encoding="utf-8")
    summary = {k: v for k, v in manifest.items() if k not in {"records", "failures"}}
    summary.update(failure_count=len(failures), failures=failures, manifest_version=registered["id"],
                   manifest_sha256=digest(output / "manifest.json"),
                   outside_lifetime_count=sum(r.get("audit", {}).get("status") == "outside_requested_quote_lifetime" for r in records))
    write_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start", default="2005-01-01")
    parser.add_argument("--end", default="2026-09-29")
    parser.add_argument("--required-start", default="2024-12-31", help="Fixed evaluation's preceding signal close")
    parser.add_argument("--effective-membership", action="store_true", help="Explicit provider-confirmed v1 ALLMARKETDAYS member contract only; no price padding")
    args = parser.parse_args()
    result = run(args.output_dir, args.start, args.end, args.required_start, effective_membership=args.effective_membership)
    if result["status"] == "blocked":
        raise SystemExit("Capture retained, required membership scope blocked; do not collect a silently reduced pool")


if __name__ == "__main__":
    main()
