"""Windows-local readiness audit; optional metadata only, no bars or backtests."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd

from deepstock.data.membership import audit_us_membership
from deepstock.data.store import ROOT, DataStore, digest, input_evidence, read_clean_csv, read_clean_json, write_json
from scripts.rerun_clean_research import capture_code_provenance


def verify_history_metadata(history_manifest):
    """Explicit local provider metadata calls; never fetch price bars."""
    import norgatedata as n
    from deepstock.data.capture import capture_response
    records = []
    start = history_manifest["requested_from"]
    for failure in history_manifest["failures"]:
        try:
            quoted = n.last_quoted_date(failure["symbol"])
            parsed = pd.Timestamp(quoted) if quoted is not None else pd.NaT
            last = str(parsed.date()) if not pd.isna(parsed) else None
            classification = "terminal_before_requested_start" if last and last < start else "unresolved_overlap"
            records.append({"symbol": failure["symbol"], "last_quoted_date": last, "classification": classification})
        except Exception as error:
            records.append({"symbol": failure["symbol"], "last_quoted_date": None,
                            "classification": "unresolved_overlap", "error": str(error)[:200]})
    watchlist = n.watchlist_symbols(history_manifest["watchlist"])
    if not watchlist:
        raise ValueError("Current historical watchlist missing")
    old = {symbol for chunk in history_manifest["chunks"] for symbol in chunk["symbols"]}
    raw = {"retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "requested_from": start,
           "security_metadata": records, "watchlist_symbols": watchlist}
    version = capture_response("Norgate", "history_readiness_security_metadata", raw, "US", restricted=True)
    source = DataStore().get(version)
    before = [r["last_quoted_date"] for r in records if r["classification"] == "terminal_before_requested_start"]
    return {"terminal_before_requested_start": len(before), "latest_verified_prior_terminal": max(before, default=None),
            "unresolved_overlap": len(records) - len(before), "current_watchlist_count": len(watchlist),
            "new_watchlist_code_count": len(set(watchlist) - old), "retired_watchlist_code_count": len(old - set(watchlist)),
            "warning": "Code changes may include aliases and index changes; full historical mapping must be refreshed, not assumed to be new constituents",
            "provider_response_version": version, "provider_response_raw_sha256": source["raw_sha256"]}


def run(data_dir: Path, history_dir: Path, sizing_dir: Path, output: Path, verify_provider_metadata=False):
    if output.exists():
        raise FileExistsError("Do not overwrite readiness evidence")
    code = capture_code_provenance()
    if code["tracked_dirty"]:
        raise ValueError("Committed clean source required for readiness audit")
    manifest_path = data_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["market"] != "US":
        raise ValueError("This audit covers the fixed US stock experiment only")
    cfg = json.loads((ROOT / "config/granville_portfolio_v1.json").read_text(encoding="utf-8"))
    if manifest["config_hash"] != digest(ROOT / "config/granville_portfolio_v1.json"):
        raise ValueError("Original source/config identity differs")
    membership = read_clean_csv(data_dir / "membership.csv.gz")
    calendar = pd.to_datetime(read_clean_csv(data_dir / "calendar.csv").date)
    coverage = audit_us_membership(calendar, membership, cfg["evaluation_start"], cfg["evaluation_end"])
    old = json.loads((sizing_dir / "market_summary.json").read_text(encoding="utf-8"))
    impacts = []
    for case in old["sizing_experiment"]["cases"]:
        stem = f"{case['sizing_policy']}-{case['cost_case']}"
        path = sizing_dir / f"{stem}-daily.csv"
        if digest(path) != old["sizing_experiment"]["artifact_hashes"][path.name]:
            raise ValueError("Immutable daily evidence differs")
        daily = pd.read_csv(path, parse_dates=["date"])
        after = daily.loc[daily.date.gt(pd.Timestamp(coverage["last_positive_interval_end"]))]
        impacts.append({"policy": case["sizing_policy"], "cost": case["cost_case"],
                        "tail_sessions": len(after), "zero_position_sessions": int(after.position_count.eq(0).sum()),
                        "first_zero_position_date": str(after.loc[after.position_count.eq(0), "date"].iloc[0].date()) if after.position_count.eq(0).any() else None,
                        "tail_buy_fills": int(after.entry_fill.sum()), "daily_sha256": digest(path)})
    history_manifest = json.loads((history_dir / "manifest.json").read_text(encoding="utf-8"))
    reference = verify_history_metadata(history_manifest) if verify_provider_metadata else {"status": "not_requested"}
    chunks = []
    union = set()
    for path in sorted(history_dir.glob("membership-*.json")):
        mapping = read_clean_json(path)
        union.update(mapping)
        chunks.append({"name": path.name, "version": DataStore().resolve(path)["id"],
                       "symbols": len(mapping), "last_interval_end": max((span["end"] for spans in mapping.values() for span in spans), default=None)})
    value = {"id": "granville-readiness-" + output.stem, "strategy_id": "granville_stock_portfolio",
             "as_of_date": datetime.now(timezone.utc).date().isoformat(), "status": coverage["status"],
             "scope": "US_existing_inputs_no_download_no_backtest", "membership": coverage,
             "ledger_impact": impacts, "historical_inventory": {
                 "watchlist_count_declared": history_manifest["symbol_count"], "captured_mapping_symbols": len(union),
                 "failure_count": len(history_manifest["failures"]), "declared_fields": history_manifest.get("fields"),
                 "request_start": history_manifest["requested_from"], "membership_chunks": chunks,
                 "provider_metadata_verification": reference,
                 "ohlc_ready": False, "reason": "Long-history chunks retain adjusted close/activity only; complete NONE/TOTALRETURN OHLC and raw daily indicator coverage are not established"},
             "dataset_manifest_sha256": digest(manifest_path), "history_manifest_sha256": digest(history_dir / "manifest.json"),
             "sizing_summary_sha256": digest(sizing_dir / "market_summary.json"),
             "data_versions": input_evidence()["data_versions"], "input_exclusions": input_evidence()["input_exclusions"],
             "code_provenance": code,
             "decision": "Retain old reproducible ledger numbers as impaired historical diagnostics, not valid full-period US performance. Do not promote or silently trim/extend inputs. Create new verified membership/universe/OHLC evidence before reruns."}
    if "provider_response_version" in reference:
        value["data_versions"].append({"version": reference["provider_response_version"],
                                       "raw_sha256": reference["provider_response_raw_sha256"], "node": "quant-computer"})
    if capture_code_provenance()["source_sha256"] != code["source_sha256"]:
        raise ValueError("Source changed during readiness audit")
    write_json(output, value)
    print(json.dumps({"status": value["status"], "membership": coverage, "ledger_impact": impacts,
                      "history_mapping_symbols": len(union), "output": str(output)}, ensure_ascii=False, indent=2))
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--history-dir", type=Path, required=True)
    parser.add_argument("--sizing-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-provider-metadata", action="store_true", help="Explicit Windows-local security/watchlist metadata capture; no price downloads")
    args = parser.parse_args()
    run(args.data_dir, args.history_dir, args.sizing_dir, args.output, args.verify_provider_metadata)


if __name__ == "__main__":
    main()
