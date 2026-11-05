"""Offline reconciliation of a supervised capture with a pinned reference export."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from deepstock.data.qmt import reconcile_snapshot_windows
from deepstock.data.qmt_runtime import verify_receipt
from deepstock.data.store import DataQualityError, digest, write_json


def audit(run: Path, reference: Path):
    supervisor = json.loads((run / "supervisor.json").read_text("utf-8"))
    if supervisor.get("status") != "complete" or not supervisor.get("local_port_released"):
        raise DataQualityError("Capture lifecycle has not passed")
    verify_receipt(run, digest(run / "request.json"))
    chain = json.loads((reference.parent / "comparison-manifest.json").read_text("utf-8"))
    entry = next(item for item in chain["files"] if item["path"] == reference.name)
    if reference.stat().st_size != entry["bytes"] or digest(reference) != entry["sha256"]:
        raise DataQualityError("Reference export checksum mismatch")
    value = json.loads(reference.read_text("utf-8"))
    if value["version"] != chain["inputs"]["tushare_version"]:
        raise DataQualityError("Reference version differs from pinned handoff")
    request = json.loads((run / "request.json").read_text("utf-8"))
    trade_date = date.fromisoformat(request["date"])
    ref = pd.DataFrame(value["rows"])
    times = pd.to_datetime(ref.trade_time, utc=True).dt.tz_convert("Asia/Shanghai")
    target = pd.Timestamp(str(trade_date) + " 09:31:00", tz="Asia/Shanghai")
    if not times.eq(target).all():
        raise DataQualityError("Reference does not describe the exact requested window")
    frames = {code: pd.DataFrame(v["data"], columns=v["columns"])
              for code, v in json.loads((run / "tick.json").read_text("utf-8")).items()}
    report, private = reconcile_snapshot_windows(frames, ref, trade_date)
    report.update(tushare_version=value["version"], request_sha256=digest(run / "request.json"),
                  reference_export_sha256=digest(reference))
    write_json(run / "boundary-comparison-private.json", private)
    write_json(run / "boundary-comparison-summary.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--reference-json", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(audit(args.capture, args.reference_json), ensure_ascii=False))


if __name__ == "__main__":
    main()
