#!/usr/bin/env python3
"""Inventory and clean existing market inputs without fetching or resuming strategies."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from deepstock.data.store import DataStore, write_json
from deepstock.data.inventory import metadata_for


def discover(root: Path):
    locations = ["artifacts/data", "artifacts/research/norgate", "artifacts/auction_history_tushare", "artifacts/auction_history", "artifacts/auction_history_smoke", "artifacts/auction_probe"]
    for location in locations:
        folder = root / location
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            if path.name.startswith("membership-") and path.suffix == ".json":
                yield path
            elif path.suffix in {".csv", ".gz"} and not any(word in path.name for word in ["backtest", "results", "features", "summary", "walkforward", "predictions", "candidates", "synthetic", "weights", "targets", "executed"]):
                yield path
    db = root / "artifacts/short_term_forward/forward.sqlite3"
    if db.exists():
        yield db


def run(root: Path) -> dict:
    store = DataStore(root)
    versions = []
    for path in discover(root):
        result = store.import_file(path, metadata_for(path))
        versions.append({"version": result["id"], "file": str(path.relative_to(root)), "status": result["status"], "rows": result.get("rows", 0), "quality": result["quality"]})
    report = {"node": store.node, "datasets": len(versions), "ready": sum(v["status"] == "ready" for v in versions), "blocked": sum(v["status"] != "ready" for v in versions), "versions": versions}
    write_json(store.root / "inventory.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    report = run(args.root)
    print(json.dumps({k: v for k, v in report.items() if k != "versions"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
