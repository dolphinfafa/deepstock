#!/usr/bin/env python3
"""Export licensed Norgate total-return ETF prices on the Windows data node."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


SYMBOLS = ("SPY", "QQQ", "IWM", "TLT", "IEF", "GLD", "SHY")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="1990-01-01")
    parser.add_argument("--end", default="2999-01-01")
    parser.add_argument("--output", default="artifacts/research/norgate/defensive_etf_prices.csv")
    args = parser.parse_args()

    try:
        import norgatedata
    except ImportError as error:
        raise SystemExit("norgatedata is required on the Windows Norgate node.") from error

    frames: list[pd.DataFrame] = []
    coverage: dict[str, dict[str, object]] = {}
    for symbol in SYMBOLS:
        series = norgatedata.price_timeseries(
            symbol,
            stock_price_adjustment_setting=norgatedata.StockPriceAdjustmentType.TOTALRETURN,
            start_date=args.start,
            end_date=args.end,
        )
        if series is None or len(series) == 0:
            raise ValueError(f"Norgate returned no data for {symbol}.")
        frame = pd.DataFrame(
            {"date": series["Date"], "symbol": symbol, "adjusted_close": series["Close"]}
        )
        frames.append(frame)
        coverage[symbol] = {
            "rows": len(frame),
            "actual_from": str(frame["date"].iloc[0])[:10],
            "actual_to": str(frame["date"].iloc[-1])[:10],
        }

    prices = pd.concat(frames, ignore_index=True).sort_values(["date", "symbol"])
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    prices.to_csv(temporary, index=False)
    temporary.replace(output)
    manifest = {
        "provider": "Norgate Data",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "symbols": list(SYMBOLS),
        "requested_from": args.start,
        "requested_to": args.end,
        "adjustment": "Norgate TOTALRETURN Close",
        "row_count": len(prices),
        "coverage": coverage,
        "license_note": "Licensed research data; do not commit or redistribute raw prices.",
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
