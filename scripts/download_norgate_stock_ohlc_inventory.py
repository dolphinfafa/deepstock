"""Full historical-list raw/total-return OHLC inventory; never a trading input."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd

from deepstock.data.capture import capture_response
from deepstock.data.store import DataStore, digest, write_json
from scripts.download_norgate_membership import load_capture
from scripts.rerun_clean_research import capture_code_provenance


def stock_frame(raw, adjusted, symbol):
    """Two native unpadded responses; never pad or merge differing dates."""
    required = {"Date", "Open", "High", "Low", "Close", "Volume", "Turnover"}
    if raw.empty or adjusted.empty or required.difference(raw) or required.difference(adjusted):
        raise ValueError("Complete raw and TOTALRETURN OHLC/activity responses required")
    if not np.array_equal(raw.Date, adjusted.Date):
        raise ValueError("Raw/TOTALRETURN date mismatch; no join or imputation")
    dates = pd.DatetimeIndex(pd.to_datetime(raw.Date, errors="coerce"))
    if dates.hasnans or dates.tz is not None or dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError("Unique ordered native price dates required")
    result = pd.DataFrame({"date": dates, "symbol": symbol,
                           "volume": raw.Volume.to_numpy(), "turnover": raw.Turnover.to_numpy()})
    for field in ["open", "high", "low", "close"]:
        result[field] = raw[field.title()].to_numpy()
        result["adjusted_" + field] = adjusted[field.title()].to_numpy()
    return result


def run(capture_dir, output):
    import norgatedata as n
    code = capture_code_provenance()
    if code["tracked_dirty"]:
        raise ValueError("Committed clean source required")
    # Evidence-only loading permits collection, never admission to a backtest.
    # No code is removed merely because its membership has an unknown date.
    _, captured, capture_version = load_capture(capture_dir, evidence_only=True)
    output.mkdir(parents=True, exist_ok=False)
    store = DataStore()
    records, failures = [], []
    for index, reference in enumerate(captured["records"]):
        symbol = reference["symbol"]
        record = {"symbol": symbol, "metadata_version": reference.get("metadata_version")}
        try:
            first, last = reference.get("first_quote"), reference.get("last_quote")
            if (reference.get("metadata_version") and first is not None and
                    (pd.Timestamp(first) > pd.Timestamp(captured["source_end"]) or
                     (last is not None and pd.Timestamp(last) < pd.Timestamp(captured["source_start"])))):
                record["status"] = "outside_verified_quote_lifetime"
            else:
                if not reference.get("metadata_version") or not reference.get("native_version"):
                    raise ValueError("Frozen provider reference missing; no silent exclusion")
                frames = []
                for mode in [n.StockPriceAdjustmentType.NONE, n.StockPriceAdjustmentType.TOTALRETURN]:
                    frame = pd.DataFrame(n.price_timeseries(symbol, stock_price_adjustment_setting=mode,
                        padding_setting=n.PaddingType.NONE, start_date=captured["source_start"], end_date=captured["source_end"]))
                    version = capture_response("Norgate", symbol + "-inventory-" + mode.name, frame, "US", restricted=True)
                    record[mode.name + "_version"] = version
                    frames.append(frame)
                bars = stock_frame(*frames, symbol)
                dates = pd.to_datetime(bars.date)
                if dates.min() < pd.Timestamp(captured["source_start"]) or dates.max() > pd.Timestamp(captured["source_end"]):
                    raise ValueError("Native price dates outside frozen request")
                path = output / "prices" / (symbol + ".csv.gz")
                path.parent.mkdir(parents=True, exist_ok=True)
                bars.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
                registered = store.import_file(path, {"market": "US", "provider": "Norgate", "restricted": True,
                    "origin": "derived_provider_export", "kind": "daily_ohlc", "timezone": "America/New_York",
                    "adjustment": "raw_NONE_and_TOTALRETURN_analytical_units", "padding": "NONE",
                    "volume_unit": "shares", "amount_unit": "USD",
                    "upstream_versions": [record["NONE_version"], record["TOTALRETURN_version"], record["metadata_version"]]})
                record.update(status=registered["status"], version=registered["id"], rows=registered["rows"],
                    file=str(path.relative_to(output)), quality=registered["quality"], first_date=registered.get("data_start"), last_date=registered.get("data_end"))
                if record["status"] != "ready":
                    raise ValueError("OHLC cleaning blocked; raw evidence retained")
        except Exception as error:
            record.update(status="blocked", error=str(error)[:250])
            failures.append({"symbol": symbol, "error": record["error"]})
        records.append(record)
        if (index + 1) % 25 == 0 or index + 1 == len(captured["records"]):
            print(json.dumps({"processed": index + 1, "total": len(captured["records"]), "failures": len(failures)}), flush=True)
    if digest(capture_dir / "manifest.json") != store.get(capture_version)["raw_sha256"] or capture_code_provenance()["source_sha256"] != code["source_sha256"]:
        raise ValueError("Frozen membership/source changed during inventory capture")
    manifest = {"status": "blocked" if failures else "captured_inventory_not_backtest_input", "market": "US", "provider": "Norgate",
        "source_start": captured["source_start"], "source_end": captured["source_end"], "symbol_count": len(records),
        "native_membership_capture_version": capture_version, "native_membership_status": captured["status"],
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "code_provenance": code, "records": records, "failures": failures,
        "policy": "Frozen entire historical list; no eligibility/survivor substitution; no price padding; unknown membership/terminal proceeds still block strategy admission"}
    write_json(output / "manifest.json", manifest)
    version = store.import_file(output / "manifest.json", {"market": "US", "provider": "Norgate", "restricted": True,
        "origin": "provider_response", "endpoint": "full_stock_ohlc_inventory"})["id"]
    summary = {k: v for k, v in manifest.items() if k not in {"records", "failures"}}
    summary.update(manifest_version=version, manifest_sha256=digest(output / "manifest.json"),
        ready_security_count=sum(r["status"] == "ready" for r in records),
        verified_outside_lifetime_count=sum(r["status"] == "outside_verified_quote_lifetime" for r in records),
        rows=sum(r.get("rows", 0) for r in records),
        missing_price_sessions=sum(r.get("quality", {}).get("missing_sessions", 0) for r in records),
        failures=failures, failure_count=len(failures), backtest_admitted=False)
    write_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--membership-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.membership_dir, args.output_dir)
    if result["failures"]:
        raise SystemExit("Native OHLC inventory retained with blocking issues")


if __name__ == "__main__":
    main()
