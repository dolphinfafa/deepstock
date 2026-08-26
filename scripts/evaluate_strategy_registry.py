#!/usr/bin/env python3
"""Evaluate frozen strategy snapshots; this script has no broker integration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from deepstock.strategy_governance import evaluate_snapshot, load_registry, record_governance_decision


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", required=True, help="JSON list or object with a snapshots list.")
    parser.add_argument("--registry", default="config/strategy_registry.json")
    parser.add_argument("--records", default="artifacts/research/strategy-governance/decisions.jsonl")
    parser.add_argument("--skip-duplicate", action="store_true")
    args = parser.parse_args()

    raw = json.loads(Path(args.snapshots).read_text(encoding="utf-8"))
    snapshots = raw.get("snapshots") if isinstance(raw, dict) else raw
    if not isinstance(snapshots, list):
        raise ValueError("Snapshots input must be a JSON list or contain a snapshots list.")
    registry = load_registry(Path(args.registry))
    records: list[dict[str, object]] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            raise ValueError("Every snapshot must be a JSON object.")
        decision = evaluate_snapshot(snapshot, registry)
        try:
            records.append(record_governance_decision(decision, Path(args.records)))
        except ValueError as error:
            if args.skip_duplicate and str(error).startswith("Governance decision already exists"):
                records.append(
                    {
                        "strategy_id": decision["strategy_id"],
                        "as_of_date": decision["as_of_date"],
                        "status": "skipped_duplicate_decision",
                    }
                )
            else:
                raise
    print(json.dumps(records, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
