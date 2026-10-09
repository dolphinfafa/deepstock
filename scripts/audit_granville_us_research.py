"""Offline existing-ledger attribution and full-list history coverage only."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import exchange_calendars as xcals
import numpy as np
import pandas as pd

from deepstock.data.granville_history_audit import attach_window_coverage, planned_windows, security_coverage
from deepstock.data.store import ROOT, DataStore, digest, read_clean_csv, write_json
from deepstock.strategies.both.granville_diagnostics import cost_path_attribution
from deepstock.strategies.us.granville_audit import audit_ledger, verify_metrics
from deepstock.web.granville_audit_reports import render_report, validate_publication
from scripts.download_norgate_membership import load_capture
from scripts.prepare_granville_us_inventory import evidence_json, inventory_slice, load_inventory
from scripts.rerun_clean_research import capture_code_provenance
from scripts.run_granville_portfolio import dataset_csv, fixed_config

SOURCE_RUN = "granville-stocks-us-effective-20261008-v1-correction"
SOURCE_RUN_SHA = "2cd2e4a2d91baff746e7338cb3f30a0167b47457de3e3dd9e2109f85c17a68d5"
SOURCE_SUMMARY_SHA = "9d6ee6370dda294b05f872402e4de3ef38a800bbbf38dc130d544c7a19b8166b"


def verify_artifact(folder, name, hashes):
    if Path(name).name != name or name not in hashes or digest(folder / name) != hashes[name]:
        raise ValueError("Ledger artifact hash mismatch/missing: " + name)
    return folder / name


def existing_ledgers(folder, data_dir, cfg):
    summary_path = folder / "market_summary.json"
    if digest(summary_path) != SOURCE_SUMMARY_SHA:
        raise ValueError("Original corrected summary hash differs")
    source = json.loads(summary_path.read_text(encoding="utf-8"))
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    if (source["config"] != cfg or source["market"] != "US" or
            source["dataset_manifest_sha256"] != digest(data_dir / "manifest.json") or manifest["failures"] or
            source["config_hash"] != manifest["config_hash"]):
        raise ValueError("Original fixed config/dataset does not reconcile")
    calendar = pd.DatetimeIndex(pd.to_datetime(dataset_csv(data_dir, manifest, "calendar.csv").date))
    expected = calendar[(calendar >= cfg["evaluation_start"]) & (calendar <= cfg["evaluation_end"])]
    exchange = xcals.get_calendar("XNYS", start=cfg["source_start"], end=cfg["evaluation_end"]).sessions.tz_localize(None)
    if not calendar.equals(exchange):
        raise ValueError("Pinned source calendar differs from XNYS")
    wanted = {(v, e, c) for v in cfg["variants"] for e in cfg["exit_policies"] for c in ["base", "stress"]}
    keys = [(c["variant"], c["exit_policy"], c["cost_case"]) for c in source["cases"]]
    if set(keys) != wanted or len(keys) != 12:
        raise ValueError("Original twelve cases must be retained")
    results, details, prices = [], {}, {}
    for case in source["cases"]:
        key = (case["variant"], case["exit_policy"], case["cost_case"])
        row = {k: case[k] for k in ["variant", "exit_policy", "cost_case"]}
        row["source_status"] = case["status"]
        if case["status"] == "blocked":
            if case["periods"] or not case.get("blocking_reason"):
                raise ValueError("Original blocked case contains fabricated metrics")
            row.update(status="source_blocked", blocking_reason=case["blocking_reason"])
            results.append(row)
            continue
        try:
            stem = "-".join(key)
            daily = pd.read_csv(verify_artifact(folder, stem + "-daily.csv", source["artifact_hashes"]), index_col="date", parse_dates=["date"])
            trades = pd.read_csv(verify_artifact(folder, stem + "-trades.csv", source["artifact_hashes"]))
            if not daily.index.equals(expected):
                raise ValueError("Ledger trading calendar differs")
            for label, part in [("full", daily), ("pre_split", daily.loc[daily.index < cfg["diagnostic_split"]]),
                                ("retrospective_holdout", daily.loc[cfg["diagnostic_split"]:])]:
                verify_metrics(part, case["periods"][label])
            for symbol in trades.symbol.unique():
                if symbol not in manifest["symbols"]:
                    raise ValueError("Traded security omitted from fixed source")
                if symbol not in prices:
                    bars = dataset_csv(data_dir, manifest, "prices/" + symbol + ".csv.gz")
                    prices[symbol] = bars.set_index(pd.DatetimeIndex(pd.to_datetime(bars.date))).adjusted_close.reindex(expected)
            rule = {**cfg["markets"]["US"], "slippage_bps": cfg["markets"]["US"]["stress_slippage_bps"] if key[2] == "stress" else cfg["markets"]["US"]["slippage_bps"]}
            stats, cycles = audit_ledger(daily, trades, cfg["initial_capital"], marks=pd.DataFrame(prices), cost_rule=rule)
            row.update(status="diagnosed", diagnostics=stats,
                       artifact_hashes={n: source["artifact_hashes"][n] for n in [stem + "-daily.csv", stem + "-trades.csv"]})
            details[stem] = cycles
        except (ValueError, KeyError, OSError) as error:
            row.update(status="diagnostic_failed", blocking_reason=str(error)[:250])
        results.append(row)
        print(json.dumps({"case": "/".join(key), "diagnostic_status": row["status"]}), flush=True)
    pairs = []
    by_key = {(c["variant"], c["exit_policy"], c["cost_case"]): c for c in results}
    for variant in cfg["variants"]:
        for policy in cfg["exit_policies"]:
            base, stress = [by_key[(variant, policy, cost)] for cost in ["base", "stress"]]
            pair = {"variant": variant, "exit_policy": policy}
            if base["status"] == stress["status"] == "diagnosed":
                pair.update(status="diagnosed", **cost_path_attribution(base["diagnostics"], stress["diagnostics"]))
            else:
                pair.update(status="blocked", blocking_reason="Both real ledgers must independently reconcile")
            pairs.append(pair)
    return {"cases": results, "cost_path_pairs": pairs}, details, source


def long_history(member_dir, inventory_dir, inventory_version):
    store = DataStore()
    mapping, captured, member_version = load_capture(member_dir, evidence_only=True)
    if not captured.get("membership_contract"):
        raise ValueError("Reviewed effective member contract required")
    inventory, records = load_inventory(inventory_dir, inventory_version, captured, store)
    for source in [inventory, captured]:
        if source["source_start"] != "2005-01-01" or source["source_end"] != "2026-09-29" or source["symbol_count"] != 1305:
            raise ValueError("Fixed full historical scope/list differs; no 538-name substitution")
    for version in [captured["universe_version"], captured["end_universe_version"]]:
        universe, _ = evidence_json(store, version)
        if set(universe["symbols"]) != set(records) or len(universe["symbols"]) != 1305:
            raise ValueError("Full historical watchlist omitted/duplicated securities")
    if set(mapping) != set(records):
        raise ValueError("Historical member mapping omits securities")
    dates = xcals.get_calendar("XNYS", start="2005-01-01", end="2026-09-29").sessions.tz_localize(None)
    counters = {name: np.zeros(len(dates), dtype=int) for name in ["member_missing", "pre_member_warmup_missing", "other_internal_missing", "unknown_membership", "verification_failures", "effective_member_sessions"]}
    rows, failures, terminals = [], [], []
    refs = {r["symbol"]: r for r in captured["records"]}
    for index, symbol in enumerate(sorted(records)):
        meta, record = refs[symbol], records[symbol]
        row = {"symbol": symbol, "assetid": meta["assetid"], "first_quote": meta["first_quote"], "last_quote": meta["last_quote"]}
        try:
            member = read_clean_csv(member_dir / meta["daily_file"], version=meta["daily_version"]) if meta.get("daily_file") else None
            if member is not None and set(member.symbol) != {symbol}:
                raise ValueError("Effective daily member identity differs")
            if record["status"] == "outside_verified_quote_lifetime":
                bars = pd.DataFrame({"date": []})
            else:
                bars, _ = inventory_slice(inventory_dir, record, {"source_start": "2005-01-01", "evaluation_end": "2026-09-29"}, store)
                registered = store.get(record["version"])
                if registered["quality"].get("imputed_rows", 0):
                    raise ValueError("Price filling cannot produce executable history")
                native_dates = []
                for name in ["NONE_version", "TOTALRETURN_version"]:
                    path = store.verified_path(store.get(record[name]), "raw")
                    native_dates.append(pd.DatetimeIndex(pd.to_datetime(pd.read_csv(path, usecols=["Date"]).Date)))
                if not native_dates[0].equals(native_dates[1]) or not native_dates[0].equals(pd.DatetimeIndex(pd.to_datetime(bars.date))):
                    raise ValueError("Native/adjusted/clean date identity differs")
            row, masks, active = security_coverage(dates, meta, member, bars.date)
            if row["quote_lifetime_outside_scope"] != (record["status"] == "outside_verified_quote_lifetime"):
                raise ValueError("Unverified quote-lifetime exclusion")
            # Positive intervals must exactly match daily provider observations.
            interval_active = np.zeros(len(dates), dtype=bool)
            for span in mapping[symbol]:
                interval_active |= (dates >= span["start"]) & (dates <= span["end"])
            if not np.array_equal(active, interval_active):
                raise ValueError("Effective interval/daily identity differs")
            for name, mask in masks.items():
                counters[name] += mask
            counters["effective_member_sessions"] += active
            row.update(status="research_input_blocked" if row["member_missing"] or row["unknown_membership"] else "coverage_checked")
            if row["effective_member_sessions"] and meta["last_quote"] and meta["last_quote"] < "2026-09-29":
                terminals.append({"symbol": symbol, "last_quote": meta["last_quote"], "holdings_evaluated": False, "proceeds_verified": False})
            row.update(member_version=meta.get("daily_version"), price_version=record.get("version"),
                       metadata_version=record["metadata_version"],
                       price_raw_sha256=store.get(record["version"])["raw_sha256"] if record.get("version") else None,
                       price_clean_sha256=store.get(record["version"])["clean_sha256"] if record.get("version") else None,
                       member_raw_sha256=store.get(meta["daily_version"])["raw_sha256"] if meta.get("daily_version") else None,
                       member_clean_sha256=store.get(meta["daily_version"])["clean_sha256"] if meta.get("daily_version") else None)
        except (ValueError, KeyError, OSError) as error:
            row.update(status="verification_failed", blocking_reason=str(error)[:250])
            failures.append({"symbol": symbol, "reason": row["blocking_reason"]})
            counters["verification_failures"] += 1  # Unknown scope blocks every proposed window.
        rows.append(row)
        if (index + 1) % 100 == 0 or index + 1 == len(records):
            print(json.dumps({"history_securities_checked": index + 1, "verification_failures": len(failures)}), flush=True)
    totals = {name: int(values.sum()) for name, values in counters.items() if name != "verification_failures"}
    return {"start": "2005-01-01", "end": "2026-09-29", "calendar": "XNYS", "sessions": len(dates),
            "calendar_sha256": hashlib.sha256("\n".join(dates.strftime("%Y-%m-%d")).encode()).hexdigest(),
            "historical_list_count": len(records), "membership_version": member_version,
            "membership_manifest_sha256": digest(member_dir / "manifest.json"), "inventory_version": inventory_version,
            "inventory_manifest_sha256": digest(inventory_dir / "manifest.json"), "membership_contract": captured["membership_contract"],
            "coverage_totals": totals, "securities": rows, "verification_failures": failures,
            "outside_quote_lifetime_count": sum(r.get("quote_lifetime_outside_scope", False) for r in rows),
            "terminal_evidence_gaps": terminals,
            "plan": attach_window_coverage(planned_windows(dates), dates, counters),
            "research_input_status": "blocked" if failures or totals["member_missing"] or totals["unknown_membership"] else "coverage_checked",
            "execution_evidence_gaps": ["Terminal cash/contingent-right value and settlement by security", "Historical major-exchange/OTC listing eligibility", "Historical sector classifications", "Actual shares, corporate-action and tax settlement", "Announcement/first-known membership dates unavailable"],
            "full_strategy_admitted": False, "strategy_run": False, "prices_filled": False}


def run(args):
    code = capture_code_provenance()
    if code["tracked_dirty"]:
        raise ValueError("Committed clean source required")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    cfg = fixed_config(args.config)
    ledgers, cycles, source = existing_ledgers(args.ledger_dir, args.data_dir, cfg)
    history = long_history(args.membership_dir, args.inventory_dir, args.inventory_version)
    if source["fresh_membership_capture_version"] != history["membership_version"] or source["price_inventory_version"] != history["inventory_version"]:
        raise ValueError("History and original correction input identities differ")
    write_json(args.output_dir / "position_cycles-restricted.json", cycles)
    publication = {"id": "granville-us-ledger-history-20261009-v1", "strategy_id": cfg["strategy_id"],
                   "report_type": "granville_us_ledger_history_audit", "as_of_date": "2026-10-09",
                   "source_run_id": SOURCE_RUN, "source_run_sha256": SOURCE_RUN_SHA,
                   "source_summary_sha256": SOURCE_SUMMARY_SHA,
                   "config": cfg, "code_provenance": code,
                   "cycles_sha256": digest(args.output_dir / "position_cycles-restricted.json"),
                   "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                   "ledgers": ledgers, "history": history, "diagnostic_only": True,
                   "new_backtest": False, "paper_authorized": False, "live_authorized": False}
    validate_publication(publication, cfg)
    if digest(args.ledger_dir / "market_summary.json") != SOURCE_SUMMARY_SHA or capture_code_provenance()["source_sha256"] != code["source_sha256"]:
        raise ValueError("Source/code changed during audit")
    write_json(args.output_dir / "publication.json", publication)
    (args.output_dir / "report.md").write_text(render_report(publication), encoding="utf-8")
    print(json.dumps({"publication_sha256": digest(args.output_dir / "publication.json"),
                      "diagnosed": sum(c["status"] == "diagnosed" for c in ledgers["cases"]),
                      "history_status": history["research_input_status"]}), flush=True)
    return publication


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["ledger-dir", "data-dir", "membership-dir", "inventory-dir", "output-dir"]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--inventory-version", required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config/granville_portfolio_v1.json")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
