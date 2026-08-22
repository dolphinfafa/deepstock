#!/usr/bin/env python3
"""Create an in-sample-only ranking report for fixed Bull candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    table = pd.read_csv(args.results)
    required = {"candidate", "standalone_in_sample_sharpe_ratio", "standalone_in_sample_maximum_drawdown"}
    missing = required.difference(table.columns)
    if missing:
        raise ValueError(f"Results missing columns: {sorted(missing)}")
    ranked = table.sort_values(
        ["standalone_in_sample_sharpe_ratio", "standalone_in_sample_maximum_drawdown"],
        ascending=[False, False],
    ).reset_index(drop=True)
    ranked.insert(0, "in_sample_rank", ranked.index + 1)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    ranked.to_csv(output / "in_sample_ranking.csv", index=False)
    manifest = {
        "selection_policy": "Ranking uses standalone in-sample Sharpe, then in-sample maximum drawdown only.",
        "out_of_sample_policy": "OOS columns are reported but prohibited from ranking or parameter selection.",
        "candidate_count": len(ranked),
        "selected_by_in_sample": ranked.iloc[0]["candidate"],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(ranked[["in_sample_rank", "candidate", "standalone_in_sample_sharpe_ratio", "standalone_in_sample_maximum_drawdown"]].to_csv(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
