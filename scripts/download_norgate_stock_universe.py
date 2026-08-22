#!/usr/bin/env python3
"""Download a point-in-time Norgate index universe in resumable chunks."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def membership_intervals(series) -> list[dict[str, str]]:
    """Convert daily 0/1 constituent values into inclusive date intervals."""

    if series is None or len(series) == 0:
        return []
    dates = pd.to_datetime(series["Date"])
    values = pd.Series(series["Index Constituent"].astype(bool), index=dates)
    intervals: list[dict[str, str]] = []
    active_start: pd.Timestamp | None = None
    previous: pd.Timestamp | None = None
    for date, active in values.items():
        if active and active_start is None:
            active_start = date
        if not active and active_start is not None:
            intervals.append({"start": active_start.date().isoformat(), "end": previous.date().isoformat()})
            active_start = None
        previous = date
    if active_start is not None and previous is not None:
        intervals.append({"start": active_start.date().isoformat(), "end": previous.date().isoformat()})
    return intervals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watchlist", default="S&P 500 Current & Past")
    parser.add_argument("--index-name", default="S&P 500 Current & Past")
    parser.add_argument("--start", default="2005-01-01")
    parser.add_argument("--end", default="2999-01-01")
    parser.add_argument("--output-dir", default="artifacts/research/norgate/stock-universe")
    parser.add_argument("--chunk-size", type=int, default=100)
    parser.add_argument("--limit", type=int, default=0, help="Limit symbols for a pilot; zero means all.")
    args = parser.parse_args()
    if args.chunk_size < 1:
        raise ValueError("chunk-size must be positive")

    try:
        import norgatedata
    except ImportError as error:
        raise SystemExit("norgatedata is required on the Windows Norgate node.") from error

    symbols = norgatedata.watchlist_symbols(args.watchlist)
    if not symbols:
        raise ValueError(f"Norgate watchlist returned no symbols: {args.watchlist}")
    symbols = symbols[: args.limit] if args.limit else symbols
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    failures: list[dict[str, str]] = []
    chunks: list[dict[str, object]] = []

    for chunk_index in range(0, len(symbols), args.chunk_size):
        chunk_symbols = symbols[chunk_index : chunk_index + args.chunk_size]
        chunk_id = chunk_index // args.chunk_size
        prices_path = output / f"prices-{chunk_id:04d}.csv"
        membership_path = output / f"membership-{chunk_id:04d}.json"
        if prices_path.exists() and membership_path.exists():
            chunks.append({"chunk": chunk_id, "symbols": chunk_symbols, "status": "resumed"})
            continue

        price_frames: list[pd.DataFrame] = []
        membership: dict[str, list[dict[str, str]]] = {}
        for symbol in chunk_symbols:
            try:
                price_series = norgatedata.price_timeseries(
                    symbol,
                    stock_price_adjustment_setting=norgatedata.StockPriceAdjustmentType.TOTALRETURN,
                    start_date=args.start,
                    end_date=args.end,
                )
                constituent_series = norgatedata.index_constituent_timeseries(
                    symbol, args.index_name, start_date=args.start, end_date=args.end
                )
                if price_series is None or len(price_series) == 0:
                    raise ValueError("no price history")
                price_frames.append(
                    pd.DataFrame(
                        {
                            "date": price_series["Date"],
                            "symbol": symbol,
                            "adjusted_close": price_series["Close"],
                        }
                    )
                )
                membership[symbol] = membership_intervals(constituent_series)
            except Exception as error:  # Norgate can reject individual symbols.
                failures.append({"symbol": symbol, "error": str(error)[:240]})

        if not price_frames:
            raise RuntimeError(f"No usable symbols in chunk {chunk_id}.")
        pd.concat(price_frames, ignore_index=True).sort_values(["date", "symbol"]).to_csv(
            prices_path, index=False
        )
        membership_path.write_text(json.dumps(membership, indent=2, sort_keys=True), encoding="utf-8")
        chunks.append({"chunk": chunk_id, "symbols": chunk_symbols, "status": "downloaded"})
        print(json.dumps(chunks[-1], sort_keys=True))

    manifest = {
        "provider": "Norgate Data",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "watchlist": args.watchlist,
        "index_name": args.index_name,
        "requested_from": args.start,
        "requested_to": args.end,
        "symbol_count": len(symbols),
        "chunk_size": args.chunk_size,
        "chunks": chunks,
        "failures": failures,
        "adjustment": "Norgate TOTALRETURN Close",
        "license_note": "Licensed research data; do not commit or redistribute raw prices.",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
