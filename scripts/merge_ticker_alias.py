#!/usr/bin/env python3
"""Merge a historical ticker alias into its canonical symbol at a switch date."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def merge_alias(
    canonical_prices: pd.DataFrame,
    alias_prices: pd.DataFrame,
    canonical: str,
    alias: str,
    switch_date: str,
) -> pd.DataFrame:
    """Use alias rows before the switch and canonical rows on/after it."""

    canonical = canonical.upper()
    alias = alias.upper()
    switch = pd.Timestamp(switch_date)
    frame = pd.concat([canonical_prices, alias_prices], ignore_index=True)
    frame["date"] = pd.to_datetime(frame["date"])
    frame["symbol"] = frame["symbol"].str.upper()
    untouched = frame[~frame["symbol"].isin({alias, canonical})].copy()
    selected = frame[
        ((frame["symbol"] == alias) & (frame["date"] < switch))
        | ((frame["symbol"] == canonical) & (frame["date"] >= switch))
    ].copy()
    selected.loc[selected["symbol"] == alias, "symbol"] = canonical
    selected = pd.concat([untouched, selected], ignore_index=True)
    if selected.duplicated(["date", "symbol"]).any():
        raise ValueError("Ticker alias merge produced duplicate dates.")
    selected["date"] = selected["date"].dt.date.astype(str)
    return selected.sort_values(["date", "symbol"], ignore_index=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="CSV containing the canonical ticker.")
    parser.add_argument("--alias", required=True, help="CSV containing the historical alias.")
    parser.add_argument("--canonical", required=True)
    parser.add_argument("--alias-symbol", required=True)
    parser.add_argument("--switch-date", required=True, help="Canonical ticker's first date.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()

    base = pd.read_csv(args.base)
    alias = pd.read_csv(args.alias)
    required = {"date", "symbol", "adjusted_close"}
    for name, frame in (("base", base), ("alias", alias)):
        if required.difference(frame.columns):
            raise ValueError(f"{name} CSV missing required columns.")
    merged = merge_alias(base, alias, args.canonical, args.alias_symbol, args.switch_date)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output, index=False)
    manifest = {
        "operation": "historical_ticker_alias_merge",
        "canonical": args.canonical.upper(),
        "alias": args.alias_symbol.upper(),
        "canonical_first_date": args.switch_date,
        "base": args.base,
        "alias_source": args.alias,
        "actual_from": merged["date"].min(),
        "actual_to": merged["date"].max(),
        "rows": len(merged),
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = Path(args.manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
