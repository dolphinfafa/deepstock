#!/usr/bin/env python3
"""Generate a review-only defensive ETF plan; never connects to a broker."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from deepstock.paper_plan import create_defensive_etf_plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", required=True)
    parser.add_argument("--output", default="artifacts/paper/defensive-etf/latest.json")
    parser.add_argument("--kill-switch", action="store_true")
    args = parser.parse_args()

    raw = pd.read_csv(args.prices)
    raw["date"] = pd.to_datetime(raw["date"])
    prices = (
        raw.pivot(index="date", columns="symbol", values="adjusted_close")
        .sort_index()
        .dropna()
    )
    plan = create_defensive_etf_plan(prices, kill_switch=args.kill_switch)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
