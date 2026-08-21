#!/usr/bin/env python3
"""Generate a deterministic paper target plan; never connects to IBKR."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from deepstock.paper_plan import create_paper_plan
from deepstock.turtle import TurtleConfig


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", required=True)
    parser.add_argument("--output", default="artifacts/paper/plans/latest.json")
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--benchmark", default=None)
    parser.add_argument("--safe-asset", default="SHY")
    parser.add_argument("--entry-days", type=int, default=55)
    parser.add_argument("--exit-days", type=int, default=20)
    parser.add_argument("--max-positions", type=int, default=5)
    parser.add_argument("--kill-switch", action="store_true")
    args = parser.parse_args()

    raw = pd.read_csv(args.prices)
    raw["date"] = pd.to_datetime(raw["date"])
    prices = raw.pivot(index="date", columns="symbol", values="adjusted_close").sort_index()
    config = TurtleConfig(
        risk_assets=tuple(args.symbols),
        benchmark=args.benchmark or args.symbols[0],
        safe_asset=args.safe_asset,
        entry_days=args.entry_days,
        exit_days=args.exit_days,
        max_positions=args.max_positions,
    )
    plan = create_paper_plan(prices, config, kill_switch=args.kill_switch)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
