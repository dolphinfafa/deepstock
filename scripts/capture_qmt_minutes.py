"""Bounded read-only native QMT capture and registered minute normalization."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import subprocess

from deepstock.data.qmt import BOUNDARY, NORMALIZER, snapshot_window_activity
from deepstock.data.qmt_runtime import SDK_VERSION, capture, verify_receipt
from deepstock.data.store import DataStore, digest, read_clean_csv, write_json

ROOT = Path(__file__).resolve().parents[1]


def register_capture(root: Path, run: Path, result: dict) -> dict:
    if result["status"] != "complete" or not result.get("local_port_released"):
        return {"status": "capture_failed", "error_type": result.get("error_type"), "run": run.name}
    verify_receipt(run, digest(run / "request.json"))
    request = json.loads((run / "request.json").read_text("utf-8"))
    normalizer_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    metadata = {"provider": "QMT", "market": "CN", "origin": "provider_response", "restricted": True,
                "endpoint": "qmt_native_minutes", "normalizer": NORMALIZER, "boundary": BOUNDARY,
                "volume_unit": "lots_100_shares", "amount_unit": "CNY", "adjustment": "none", "fill_data": False,
                "timezone": "Asia/Shanghai", "sdk_version": SDK_VERSION, "request_sha256": digest(run / "request.json"),
                "code_commit": request["code_commit"],
                "normalizer_code_commit": normalizer_commit, "trade_date": request["date"],
                "retrieved_at_utc": result["receipt"]["finished_at_utc"], "strategy_input_approved": False}
    store = DataStore(root, node="quant-computer")
    manifest = store.import_file(run / "minute.csv.gz", metadata)
    target = store.import_file(run / "minute.csv.gz", {**metadata, "minute_window": "exact_0931_v1"})
    report = {"status": "captured_diagnostic_only" if target["status"] == "ready" else "input_blocked",
              "run": run.name, "minute_version": manifest["id"], "minute_rows": manifest["rows"],
              "all_observations_quality": manifest["status"], "all_observations_quarantined_rows": manifest["quality"].get("invalid_rows"),
              "target_minute_version": target["id"], "target_quality": target["status"],
              "cleanup": result["cleanup"], "worker_returncode": result["worker_returncode"],
              "local_port_released": result["local_port_released"], "strategy_input_approved": False,
              "operations": result["receipt"]["operations"], "boundary_diagnostics": []}
    # Registered input read revalidates source/raw/clean hashes, not an alias.
    if target["status"] == "ready":
        frame = read_clean_csv(run / "minute.csv.gz", version=target["id"])
        exact = frame.loc[frame.bar_kind.eq("continuous") & frame.timestamp.str.contains("09:31:00")
                          & frame.volume.gt(0) & frame.amount.gt(0)]
        report["exact_0931_stocks"] = int(exact.symbol.nunique())
    for name, endpoint in [("tick.json", "qmt_tick_snapshots"), ("quotes.json", "qmt_subscribed_snapshots")]:
        if (run / name).exists():
            raw_version = DataStore(root, node="quant-computer").import_file(run / name, {
                "provider": "QMT", "market": "CN", "origin": "provider_response", "restricted": True,
                "endpoint": endpoint, "request_sha256": digest(run / "request.json"), "code_commit": request["code_commit"],
                "strategy_input_approved": False, "retrieved_at_utc": result["receipt"]["finished_at_utc"]})
            report[name.removesuffix(".json") + "_raw_version"] = raw_version["id"]
    if (run / "tick.json").exists():
        import pandas as pd
        values = json.loads((run / "tick.json").read_text("utf-8"))
        private = {}
        for symbol, value in values.items():
            rows = pd.DataFrame(value["data"], columns=value["columns"])
            private[symbol] = {}
            for closed in [False, True]:
                key = "right_closed" if closed else "left_closed"
                try:
                    private[symbol][key] = snapshot_window_activity(rows, date.fromisoformat(request["date"]), right_closed=closed)
                except ValueError as exc:
                    private[symbol][key] = {"status": "blocked", "reason": str(exc)}
            report["boundary_diagnostics"].append({"symbol": symbol, "variants_available": sum("volume_shares" in v for v in private[symbol].values())})
        write_json(run / "boundary-activity-private.json", private)
    # Preserve earlier diagnostic reports when normalizing a saved capture.
    path = run / "report.json"
    if path.exists() and json.loads(path.read_text("utf-8")) != report:
        path = run / ("normalization-report-" + target["id"] + ".json")
    write_json(path, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--symbols-file", type=Path, required=True, help="JSON list; use a pinned PIT universe for historical comparisons")
    parser.add_argument("--tick-symbols-file", type=Path)
    parser.add_argument("--watch-seconds", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)
    symbols = json.loads(args.symbols_file.read_text("utf-8"))
    ticks = json.loads(args.tick_symbols_file.read_text("utf-8")) if args.tick_symbols_file else []
    run, result = capture(ROOT, symbols, args.date, tick_symbols=ticks, watch_seconds=args.watch_seconds, timeout=args.timeout)
    report = register_capture(ROOT, run, result)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "captured_diagnostic_only" else 1


if __name__ == "__main__":
    raise SystemExit(main())
