# -*- coding: utf-8 -*-
"""Probe Tushare opening-auction availability with FTShare as a cross-check."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from deepstock.strategies.cn.auction.providers import get_pro
from deepstock.strategies.cn.auction.ftshare import DEFAULT_MCP_URL, _call


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _now() -> datetime:
    return datetime.now(SHANGHAI)


def _json_default(value: Any):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if pd.isna(value):
        return None
    raise TypeError(f"cannot serialize {type(value).__name__}")


def fetch_tushare_auction(trade_date: str) -> tuple[pd.DataFrame | None, str | None, dict]:
    started = _now()
    try:
        frame = get_pro().stk_auction(trade_date=trade_date, ts_type="STK")
        error = None
    except Exception as exc:
        frame = None
        error = f"{type(exc).__name__}: {exc}"
    finished = _now()
    event = {
        "source": "tushare",
        "request_started": started.isoformat(),
        "request_finished": finished.isoformat(),
        "rows": 0 if frame is None else len(frame),
        "error": error,
    }
    return frame, error, event


async def _fetch_ft_page(
    session: ClientSession,
    trade_date: str,
    page: int,
    page_size: int,
) -> tuple[dict, dict]:
    started = _now()
    try:
        payload = await _call(session, "ft_auction_results", {
            "trade_date": trade_date,
            "page": page,
            "page_size": page_size,
        })
        error = None
    except Exception as exc:
        payload = {"data": [], "metadata": {}}
        error = f"{type(exc).__name__}: {exc}"
    finished = _now()
    metadata = payload.get("metadata") or {}
    event = {
        "source": "ftshare",
        "page": page,
        "request_started": started.isoformat(),
        "request_finished": finished.isoformat(),
        "rows": len(payload.get("data") or []),
        "total": metadata.get("total"),
        "error": error,
    }
    return payload, event


async def fetch_ftshare_full(
    session: ClientSession,
    trade_date: str,
    first_payload: dict,
    events: list[dict],
    concurrency: int = 5,
) -> pd.DataFrame:
    metadata = first_payload.get("metadata") or {}
    pagination = metadata.get("pagination") or {}
    page_size = int(pagination.get("page_size") or 200)
    total = int(metadata.get("total") or len(first_payload.get("data") or []))
    pages = int(pagination.get("pages") or math.ceil(total / page_size) or 1)
    rows = list(first_payload.get("data") or [])
    semaphore = asyncio.Semaphore(concurrency)

    async def fetch(page: int) -> dict:
        async with semaphore:
            payload, event = await _fetch_ft_page(
                session, trade_date, page, page_size
            )
            events.append(event)
            if event["error"]:
                raise RuntimeError(f"FTShare page {page}: {event['error']}")
            return payload

    if pages > 1:
        payloads = await asyncio.gather(*(fetch(page) for page in range(2, pages + 1)))
        for payload in payloads:
            rows.extend(payload.get("data") or [])
    frame = pd.DataFrame(rows)
    if not frame.empty and "symbol" in frame:
        frame = frame.drop_duplicates("symbol", keep="last")
    if total and len(frame) < total:
        raise RuntimeError(f"FTShare full snapshot incomplete: {len(frame)}/{total}")
    return frame.reset_index(drop=True)


def _normalize_ftshare(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["stock_code", "ft_price", "ft_volume", "ft_amount"])
    out = pd.DataFrame({
        "stock_code": frame["symbol"].astype(str).str[:6],
        "ft_price": pd.to_numeric(frame.get("open"), errors="coerce"),
        "ft_volume": pd.to_numeric(frame.get("volume"), errors="coerce"),
        "ft_amount": pd.to_numeric(frame.get("amount"), errors="coerce"),
    })
    return out.drop_duplicates("stock_code", keep="last")


def _normalize_tushare(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["stock_code", "ts_price", "ts_volume", "ts_amount"])
    required = {"ts_code", "price", "vol", "amount"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Tushare auction response missing columns: {', '.join(missing)}")
    out = pd.DataFrame({
        "stock_code": frame["ts_code"].astype(str).str[:6],
        "ts_price": pd.to_numeric(frame["price"], errors="coerce"),
        "ts_volume": pd.to_numeric(frame["vol"], errors="coerce"),
        "ts_amount": pd.to_numeric(frame["amount"], errors="coerce"),
    })
    return out.drop_duplicates("stock_code", keep="last")


def build_comparison(
    ftshare: pd.DataFrame,
    tushare: pd.DataFrame,
) -> tuple[pd.DataFrame, dict]:
    left = _normalize_ftshare(ftshare)
    right = _normalize_tushare(tushare)
    comparison = left.merge(right, on="stock_code", how="inner")
    if comparison.empty:
        return comparison, {"intersection": 0}

    comparison["price_diff"] = comparison["ft_price"] - comparison["ts_price"]
    comparison["volume_diff"] = comparison["ft_volume"] - comparison["ts_volume"]
    comparison["amount_diff"] = comparison["ft_amount"] - comparison["ts_amount"]
    for prefix in ("price", "volume", "amount"):
        denominator = comparison[f"ts_{prefix}"].abs().replace(0, np.nan)
        comparison[f"{prefix}_relative_error"] = (
            comparison[f"{prefix}_diff"].abs() / denominator
        )
    metrics = {
        "intersection": len(comparison),
        "valid_price_intersection": int(comparison["price_diff"].notna().sum()),
        "tushare_missing_price": int(comparison["ts_price"].isna().sum()),
        "price_exact_rate_0_001": float(
            (comparison["price_diff"].dropna().abs() <= 0.001).mean()
        ),
        "price_median_absolute_error": float(comparison["price_diff"].abs().median()),
        "volume_median_relative_error": float(comparison["volume_relative_error"].median()),
        "amount_median_relative_error": float(comparison["amount_relative_error"].median()),
    }
    return comparison, metrics


def _write_report(
    output_dir: Path,
    trade_date: str,
    ftshare: pd.DataFrame | None,
    tushare: pd.DataFrame | None,
    events: list[dict],
    tushare_error: str | None,
    minimum_coverage: int,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    if ftshare is not None:
        ftshare.to_csv(output_dir / "ftshare.csv", index=False)
    if tushare is not None:
        tushare.to_csv(output_dir / "tushare.csv", index=False)

    comparison_metrics: dict[str, Any] | None = None
    comparison_error: str | None = None
    if ftshare is not None and tushare is not None and not tushare.empty:
        try:
            comparison, comparison_metrics = build_comparison(ftshare, tushare)
            comparison.to_csv(output_dir / "comparison.csv", index=False)
        except Exception as exc:
            comparison_error = f"{type(exc).__name__}: {exc}"

    first_available: dict[str, str | None] = {}
    first_full_coverage: dict[str, str | None] = {}
    for source in ("ftshare", "tushare"):
        first_available[source] = next((
            event["request_finished"]
            for event in events
            if event["source"] == source and event["rows"] > 0
        ), None)
        first_full_coverage[source] = next((
            event["request_finished"]
            for event in events
            if event["source"] == source
            and int(
                event.get("total") or event.get("rows") or 0
            ) >= minimum_coverage
        ), None)
    report = {
        "trade_date": trade_date,
        "generated_at": _now().isoformat(),
        "primary_source": "tushare",
        "ftshare_rows": 0 if ftshare is None else len(ftshare),
        "tushare_rows": 0 if tushare is None else len(tushare),
        "minimum_coverage": minimum_coverage,
        "first_available_at": first_available,
        "first_full_coverage_at": first_full_coverage,
        "tushare_error": tushare_error,
        "comparison_error": comparison_error,
        "comparison": comparison_metrics,
    }
    (output_dir / "events.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    comparison_text = (
        json.dumps(comparison_metrics, ensure_ascii=False, default=_json_default)
        if comparison_metrics is not None
        else f"unavailable ({comparison_error or tushare_error or 'no Tushare data'})"
    )
    markdown = "\n".join([
        f"# Opening auction source probe: {trade_date}",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- FTShare: {report['ftshare_rows']} rows; first available `{first_available['ftshare']}`",
        f"- Tushare: {report['tushare_rows']} rows; first available `{first_available['tushare']}`",
        f"- First full coverage ({minimum_coverage}+): `{first_full_coverage}`",
        f"- Tushare error: `{tushare_error or 'none'}`",
        f"- Comparison: `{comparison_text}`",
        "",
        "Raw files: `ftshare.csv`, `tushare.csv` (when available), `comparison.csv` (when comparable), and `events.json`.",
        "",
    ])
    (output_dir / "report.md").write_text(markdown, encoding="utf-8")
    return report


async def run_probe(args: argparse.Namespace) -> dict:
    events: list[dict] = []
    deadline = time.monotonic() + args.duration

    async def poll_tushare() -> tuple[pd.DataFrame | None, str | None]:
        last_error: str | None = None
        while args.once or time.monotonic() < deadline:
            frame, error, event = await asyncio.to_thread(
                fetch_tushare_auction, args.trade_date
            )
            events.append(event)
            last_error = error
            if frame is not None and len(frame) >= args.minimum_coverage:
                return frame, None
            if error and ("没有接口" in error or "权限" in error):
                return None, error
            if args.once:
                break
            await asyncio.sleep(args.poll_interval)
        return None, last_error

    async def poll_ftshare() -> pd.DataFrame | None:
        total_previous: int | None = None
        stable_count = 0
        try:
            async with streamable_http_client(args.ftshare_url) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    while args.once or time.monotonic() < deadline:
                        payload, event = await _fetch_ft_page(
                            session, args.trade_date, 1, 200
                        )
                        events.append(event)
                        total = int(event.get("total") or 0)
                        if total >= args.minimum_coverage:
                            stable_count = stable_count + 1 if total == total_previous else 1
                        else:
                            stable_count = 0
                        total_previous = total
                        if total >= args.minimum_coverage and (
                            args.once or stable_count >= 2
                        ):
                            return await fetch_ftshare_full(
                                session, args.trade_date, payload, events
                            )
                        if args.once:
                            break
                        await asyncio.sleep(args.poll_interval)
        except Exception as exc:
            events.append({
                "source": "ftshare",
                "request_finished": _now().isoformat(),
                "rows": 0,
                "error": f"{type(exc).__name__}: {exc}",
            })
        return None

    (tushare_frame, tushare_error), ftshare_frame = await asyncio.gather(
        poll_tushare(), poll_ftshare()
    )

    output_dir = Path(args.output_root) / args.trade_date
    return _write_report(
        output_dir,
        args.trade_date,
        ftshare_frame,
        tushare_frame,
        events,
        tushare_error,
        args.minimum_coverage,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe Tushare opening-auction availability with FTShare comparison"
    )
    parser.add_argument(
        "--trade-date", default=_now().strftime("%Y%m%d"),
        help="Trading date in YYYYMMDD",
    )
    parser.add_argument("--duration", type=int, default=240)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--minimum-coverage", type=int, default=4000)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--ftshare-url", default=DEFAULT_MCP_URL)
    parser.add_argument("--output-root", default="artifacts/auction_probe")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = asyncio.run(run_probe(args))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default))
    if report["tushare_rows"] == 0:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
