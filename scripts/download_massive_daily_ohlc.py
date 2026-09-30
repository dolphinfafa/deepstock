#!/usr/bin/env python3
"""Download split-adjusted daily OHLC bars for fixed indicator research."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from deepstock.massive import download_split_adjusted_daily_ohlc, load_env_value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="start", required=True)
    parser.add_argument("--to", dest="end", required=True)
    parser.add_argument("--symbols", default="SPY")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output", default="artifacts/data/massive_daily_ohlc.csv")
    parser.add_argument("--manifest", default="artifacts/data/massive_daily_ohlc.manifest.json")
    args = parser.parse_args()

    api_key = load_env_value(args.env_file, "MASSIVE_API_KEY")
    if not api_key:
        raise SystemExit("Missing MASSIVE_API_KEY in the local .env file.")
    symbols = tuple(item.strip().upper() for item in args.symbols.split(",") if item.strip())
    bars = download_split_adjusted_daily_ohlc(symbols, args.start, args.end, api_key)
    output = Path(args.output)
    manifest = Path(args.manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    bars.to_csv(output, index=False)
    manifest.write_text(
        json.dumps(
            {
                "provider": "Massive",
                "endpoint": "v2/aggs ticker range, 1 day",
                "adjustment": "split-adjusted OHLC; dividends are not back-adjusted",
                "symbols": symbols,
                "requested_from": args.start,
                "requested_to": args.end,
                "actual_from": bars["date"].min(),
                "actual_to": bars["date"].max(),
                "rows": len(bars),
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(f"Downloaded {len(bars)} daily OHLC bars for {len(symbols)} symbols.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
