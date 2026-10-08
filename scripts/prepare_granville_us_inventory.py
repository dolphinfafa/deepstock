"""Offline fixed-period input from effective members and pinned native prices.

No provider or broker calls. Inventory readiness is not portfolio admission.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd

from deepstock.data.membership import require_us_membership
from deepstock.data.store import ROOT, DataStore, digest, input_evidence, read_clean_csv, write_json
from scripts.download_norgate_membership import load_capture
from scripts.prepare_granville_stock_data import persist
from scripts.rerun_clean_research import capture_code_provenance
from scripts.run_granville_portfolio import fixed_config


def evidence_json(store, version):
    registered = store.get(version)
    if registered.get("provider") != "Norgate":
        raise ValueError("Norgate evidence required")
    return json.loads(store.verified_path(registered, "raw").read_text(encoding="utf-8")), registered


def unique_records(records):
    result = {r["symbol"]: r for r in records}
    if len(result) != len(records):
        raise ValueError("Duplicate security identity in frozen manifest")
    return result


def load_inventory(folder, version, captured, store):
    inventory, registered = evidence_json(store, version)
    if digest(folder / "manifest.json") != registered["raw_sha256"]:
        raise ValueError("Frozen inventory manifest changed")
    if (inventory.get("status") != "captured_inventory_not_backtest_input" or inventory.get("failures") or
            inventory.get("market") != "US" or inventory.get("provider") != "Norgate"):
        raise ValueError("Native price inventory blocked or different market")
    old, _ = evidence_json(store, inventory["native_membership_capture_version"])
    records, previous, current = [unique_records(v["records"]) for v in [inventory, old, captured]]
    if (set(records) != set(previous) or set(records) != set(current) or
            inventory["symbol_count"] != len(records)):
        raise ValueError("Entire historical-list identity changed; cannot substitute survivors")
    for symbol, record in records.items():
        meta, _ = evidence_json(store, record["metadata_version"])
        if record["metadata_version"] != previous[symbol]["metadata_version"]:
            raise ValueError("Inventory metadata does not belong to frozen native identity")
        for key in ["symbol", "assetid", "first_quote", "last_quote"]:
            if meta[key] != previous[symbol][key] or meta[key] != current[symbol][key]:
                raise ValueError("Native/effective security or quote lifetime changed: " + symbol)
    return inventory, records


def inventory_slice(folder, record, cfg, store):
    symbol = record["symbol"]
    if record["status"] != "ready":
        raise ValueError("Required constituent has no ready inventory; never download/drop a fallback")
    registered = store.get(record["version"])
    contract = registered["contract"]
    expected = {"market": "US", "provider": "Norgate", "restricted": True,
                "kind": "daily_ohlc", "padding": "NONE", "timezone": "America/New_York",
                "adjustment": "raw_NONE_and_TOTALRETURN_analytical_units", "volume_unit": "shares", "amount_unit": "USD"}
    if any(contract.get(k) != v for k, v in expected.items()):
        raise ValueError("Native raw/TOTALRETURN no-padding price contract differs")
    upstream = [record[k] for k in ["NONE_version", "TOTALRETURN_version", "metadata_version"]]
    if contract.get("upstream_versions") != upstream:
        raise ValueError("Native price response lineage differs")
    for version in upstream:
        store.verified_path(store.get(version), "raw")
    # Fixed safe filename, not a manifest-controlled arbitrary filesystem path.
    original = read_clean_csv(folder / "prices" / (symbol + ".csv.gz"), version=record["version"])
    if set(original.symbol) != {symbol}:
        raise ValueError("Native price security identity differs")
    dates = pd.to_datetime(original.date)
    selected = original.loc[dates.between(cfg["source_start"], cfg["evaluation_end"])].copy()
    if selected.empty:
        raise ValueError("Required constituent has no fixed-period prices")
    return selected, len(original) - len(selected)


def run(member_dir, inventory_dir, inventory_version, output, config_path):
    cfg = fixed_config(config_path)
    code = capture_code_provenance()
    if code["tracked_dirty"]:
        raise ValueError("Committed clean source required")
    output.mkdir(parents=True, exist_ok=False)
    mapping, captured, member_version = load_capture(member_dir)
    if not captured.get("membership_contract"):
        raise ValueError("Reviewed effective membership contract required")
    store = DataStore()
    inventory, records = load_inventory(inventory_dir, inventory_version, captured, store)
    for source in [captured, inventory]:
        if pd.Timestamp(source["source_start"]) > pd.Timestamp(cfg["source_start"]) or pd.Timestamp(source["source_end"]) < pd.Timestamp(cfg["evaluation_end"]):
            raise ValueError("Frozen evidence does not cover fixed source dates")
    calendar = xcals.get_calendar("XNYS", start=cfg["source_start"], end=cfg["evaluation_end"]).sessions.tz_localize(None)
    start, end = pd.Timestamp(cfg["evaluation_start"]), pd.Timestamp(cfg["evaluation_end"])
    signal_start = calendar[calendar < start][-1]
    if pd.Timestamp(captured["required_start"]) > signal_start:
        raise ValueError("Effective membership does not verify first signal close")
    wanted = {s: spans for s, spans in mapping.items() if any(pd.Timestamp(r["start"]) <= end and pd.Timestamp(r["end"]) >= signal_start for r in spans)}
    if not wanted or set(wanted).difference(records):
        raise ValueError("Required historical members missing from inventory")
    # Clip only outside-request intervals, never bridge unknown/inactive days.
    members = pd.DataFrame([{"date": max(r["start"], cfg["source_start"]), "end": min(r["end"], cfg["evaluation_end"]), "symbol": s, "weight": 1}
                            for s, spans in wanted.items() for r in spans
                            if r["start"] <= cfg["evaluation_end"] and r["end"] >= cfg["source_start"]])
    require_us_membership(calendar, members, cfg["evaluation_start"], cfg["evaluation_end"])
    pins = {}

    def save(frame, name, kind, upstream=(), **kwargs):
        cleaned = persist(frame, output / name, "US", kind, upstream, **kwargs)
        pins[name] = cleaned.attrs["data_version"]
        return cleaned

    save(members, "membership.csv.gz", "historical_membership", [member_version, captured["interval_version"]])
    save(pd.DataFrame({"date": calendar}), "calendar.csv", "calendar")
    failures, terminals, slices = [], {}, []
    references = unique_records(captured["records"])
    for index, symbol in enumerate(sorted(wanted)):
        try:
            bars, excluded = inventory_slice(inventory_dir, records[symbol], cfg, store)
            cleaned = save(bars, "prices/" + symbol + ".csv.gz", "daily_ohlc", [records[symbol]["version"], inventory_version, member_version],
                           adjustment="raw_NONE_and_provider_TOTALRETURN_analytical_units", padding="NONE", volume_unit="shares", amount_unit="USD",
                           reused_verified_price_only=True)
            slices.append({"symbol": symbol, "upstream_version": records[symbol]["version"], "version": cleaned.attrs["data_version"],
                           "rows": len(cleaned), "excluded_outside_declared_interval": excluded})
            last_quote = references[symbol]["last_quote"]
            if last_quote and pd.Timestamp(last_quote) < end:
                terminals[symbol] = {"last_quote_date": last_quote, "verified_proceeds": False}
        except ValueError as error:
            failures.append({"symbol": symbol, "error": str(error)[:250]})
        if (index + 1) % 25 == 0 or index + 1 == len(wanted):
            print(json.dumps({"processed": index + 1, "total": len(wanted), "failures": len(failures)}), flush=True)
    member_sha, inventory_sha, config_sha = digest(member_dir / "manifest.json"), digest(inventory_dir / "manifest.json"), digest(config_path)
    if (member_sha != store.get(member_version)["raw_sha256"] or inventory_sha != store.get(inventory_version)["raw_sha256"] or
            capture_code_provenance()["source_sha256"] != code["source_sha256"] or fixed_config(config_path) != cfg):
        raise ValueError("Frozen evidence/source/config changed during offline preparation")
    manifest = {"market": "US", "symbols": sorted(wanted), "symbol_count": len(wanted), "source_start": cfg["source_start"], "source_end": cfg["evaluation_end"],
                "membership_model": "provider_evaluated_effective_daily_index_intervals", "failures": failures, "terminal_securities": terminals,
                "fresh_membership_capture_version": member_version, "fresh_membership_manifest_sha256": member_sha,
                "price_inventory_version": inventory_version, "price_inventory_manifest_sha256": inventory_sha, "membership_contract": captured["membership_contract"],
                "full_historical_list_count": len(records), "reused_verified_price_count": len(slices), "new_price_count": 0,
                "config_hash": config_sha, "code_provenance": code, "input_versions": pins, "slice_records": slices,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "backtest_admitted": False,
                "policy": "Offline fixed-date slices; all effective members retained; no price fill or terminal proceeds; portfolio gate remains independent", **input_evidence()}
    write_json(output / "manifest.json", manifest)
    summary = {k: v for k, v in manifest.items() if k not in {"symbols", "terminal_securities", "input_versions", "slice_records", "data_versions", "input_exclusions"}}
    summary.update(dataset_manifest_sha256=digest(output / "manifest.json"), rows=sum(r["rows"] for r in slices),
                   terminal_security_count=len(terminals), failure_count=len(failures))
    write_json(output / "summary.json", summary)
    if failures:
        raise ValueError("Fixed-period evidence blocked; all failed securities retained")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--membership-dir", type=Path, required=True)
    parser.add_argument("--inventory-dir", type=Path, required=True)
    parser.add_argument("--inventory-version", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config/granville_portfolio_v1.json")
    args = parser.parse_args()
    print(json.dumps(run(args.membership_dir, args.inventory_dir, args.inventory_version, args.output_dir, args.config), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
