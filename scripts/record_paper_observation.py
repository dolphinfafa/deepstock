#!/usr/bin/env python3
"""Record one review-only paper plan without broker interaction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from deepstock.observation import record_observation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--records", default="artifacts/paper/defensive-etf/observations.jsonl")
    parser.add_argument(
        "--skip-duplicate",
        action="store_true",
        help="Treat an existing plan ID as a successful, non-writing scheduler skip.",
    )
    args = parser.parse_args()

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    try:
        record = record_observation(plan, Path(args.records))
    except ValueError as error:
        if args.skip_duplicate and str(error).startswith("Observation already exists for plan_id="):
            record = {
                "status": "skipped_duplicate_plan",
                "plan_id": plan["plan_id"],
                "data_date": plan.get("data_date"),
            }
        else:
            raise
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
