#!/usr/bin/env python3
"""Download Massive adjusted ETF daily prices to a local research artifact."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from deepstock.massive import download_total_return_daily_prices, load_env_value


DEFAULT_SYMBOLS = "SPY,QQQ,IWM,TLT,IEF,GLD,SHY"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="start", required=True, help="Inclusive ISO date, YYYY-MM-DD.")
    parser.add_argument("--to", dest="end", required=True, help="Inclusive ISO date, YYYY-MM-DD.")
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output", default="artifacts/data/massive_adjusted_daily.csv")
    parser.add_argument("--manifest", default="artifacts/data/massive_adjusted_daily.manifest.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_key = load_env_value(args.env_file, "MASSIVE_API_KEY")
    if not api_key:
        raise SystemExit("Missing MASSIVE_API_KEY in the local .env file.")
    symbols = tuple(item.strip().upper() for item in args.symbols.split(",") if item.strip())
    prices = download_total_return_daily_prices(symbols, args.start, args.end, api_key)

    output = Path(args.output)
    manifest = Path(args.manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    prices.to_csv(output, index=False)
    manifest.write_text(
        json.dumps(
            {
                "provider": "Massive",
                "price_endpoint": "v2/aggs ticker range, 1 day",
                "price_adjustment": "splits",
                "dividend_endpoint": "stocks/v1/dividends",
                "total_return_adjustment": "sequential ex-dividend cash return",
                "response_status_policy": "OK or DELAYED accepted for historical research only",
                "symbols": symbols,
                "from": args.start,
                "to": args.end,
                "rows": len(prices),
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(f"Downloaded {len(prices)} adjusted daily bars for {len(symbols)} symbols.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
