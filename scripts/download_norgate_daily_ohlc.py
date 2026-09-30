#!/usr/bin/env python3
"""Export licensed Norgate total-return daily OHLC on the Windows data node."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def build_ohlc_frame(series: object, symbol: str) -> pd.DataFrame:
    """Normalize and validate a Norgate daily OHLC response."""

    source = pd.DataFrame(series)
    required = {"Date", "Open", "High", "Low", "Close"}
    missing = required.difference(source.columns)
    if missing:
        raise ValueError(f"Norgate OHLC response missing columns: {sorted(missing)}")
    normalized = symbol.strip().upper()
    if not normalized:
        raise ValueError("Symbol cannot be empty.")
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(source["Date"]).dt.date.astype(str),
            "symbol": normalized,
            "adjusted_open": pd.to_numeric(source["Open"]),
            "adjusted_high": pd.to_numeric(source["High"]),
            "adjusted_low": pd.to_numeric(source["Low"]),
            "adjusted_close": pd.to_numeric(source["Close"]),
        }
    ).sort_values("date", ignore_index=True)
    price_columns = [
        "adjusted_open",
        "adjusted_high",
        "adjusted_low",
        "adjusted_close",
    ]
    if frame.empty or frame[price_columns].isna().any().any():
        raise ValueError("Norgate returned empty or incomplete OHLC data.")
    if frame["date"].duplicated().any() or (frame[price_columns] <= 0).any().any():
        raise ValueError("Norgate OHLC dates must be unique and prices must be positive.")
    if (
        frame["adjusted_high"]
        < frame[["adjusted_open", "adjusted_low", "adjusted_close"]].max(axis=1)
    ).any():
        raise ValueError("Norgate adjusted high is below another OHLC field.")
    if (
        frame["adjusted_low"]
        > frame[["adjusted_open", "adjusted_high", "adjusted_close"]].min(axis=1)
    ).any():
        raise ValueError("Norgate adjusted low is above another OHLC field.")
    return frame


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--start", default="1990-01-01")
    parser.add_argument("--end", default="2999-01-01")
    parser.add_argument(
        "--output", default="artifacts/research/norgate/spy_daily_ohlc.csv"
    )
    args = parser.parse_args()

    try:
        import norgatedata
    except ImportError as error:
        raise SystemExit("norgatedata is required on the Windows Norgate node.") from error

    symbol = args.symbol.strip().upper()
    series = norgatedata.price_timeseries(
        symbol,
        stock_price_adjustment_setting=norgatedata.StockPriceAdjustmentType.TOTALRETURN,
        start_date=args.start,
        end_date=args.end,
    )
    frame = build_ohlc_frame(series, symbol)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(output)

    manifest = {
        "provider": "Norgate Data",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "requested_from": args.start,
        "requested_to": args.end,
        "actual_from": frame["date"].iloc[0],
        "actual_to": frame["date"].iloc[-1],
        "row_count": len(frame),
        "adjustment": "Norgate TOTALRETURN OHLC",
        "fields": frame.columns.tolist(),
        "license_note": "Licensed research data; do not commit or redistribute raw prices.",
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_temporary = manifest_path.with_suffix(".tmp")
    manifest_temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    manifest_temporary.replace(manifest_path)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
