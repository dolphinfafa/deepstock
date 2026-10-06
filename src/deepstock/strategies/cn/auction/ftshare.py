# -*- coding: utf-8 -*-
"""Small FTShare MCP adapter used by the short-term MVP."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime, time
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


from deepstock.massive import load_env_value
from deepstock.data.store import ROOT
from deepstock.data.capture import capture_response
DEFAULT_MCP_URL = os.getenv("FTSHARE_MCP_URL") or load_env_value(str(ROOT / ".env"), "FTSHARE_MCP_URL") or "https://market.ft.tech/gateway/mcp"
SHANGHAI = ZoneInfo("Asia/Shanghai")


class FTShareError(RuntimeError):
    pass


def _structured_payload(result: Any) -> dict:
    payload = (
        getattr(result, "structuredContent", None)
        or getattr(result, "structured_content", None)
    )
    if payload:
        return payload
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
    return {}


def _is_error(result: Any) -> bool:
    return bool(getattr(result, "isError", getattr(result, "is_error", False)))


def _error_text(result: Any) -> str:
    blocks = getattr(result, "content", []) or []
    return getattr(blocks[0], "text", "FTShare call failed") if blocks else "FTShare call failed"


async def _call(session: ClientSession, tool: str, arguments: dict) -> dict:
    result = await asyncio.wait_for(session.call_tool(tool, arguments), timeout=30)
    if _is_error(result):
        raise FTShareError(_error_text(result))
    payload = _structured_payload(result)
    if not isinstance(payload.get("data"), list):
        raise FTShareError(f"{tool} returned an invalid structured payload")
    capture_response("FTshare", tool, payload, "CN")
    return payload


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


async def _fetch_daily_candles(
    symbols: list[str],
    start_date: date,
    end_date: date,
    url: str,
) -> pd.DataFrame:
    since_ms = int(datetime.combine(start_date, time.min, SHANGHAI).timestamp() * 1000)
    until_ms = int(datetime.combine(end_date, time.max, SHANGHAI).timestamp() * 1000)
    rows: list[dict] = []
    async with streamable_http_client(url) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            for symbols_chunk in _chunks(symbols, 500):
                payload = await _call(session, "ft_stock_candlesticks_batch", {
                    "symbols": symbols_chunk,
                    "interval_unit": "Day",
                    "adjust_kind": "None",
                    "since_ts_millis": since_ms,
                    "until_ts_millis": until_ms,
                    "limit": min(500, (end_date - start_date).days + 20),
                })
                rows.extend(payload["data"])

    parsed: list[dict] = []
    for row in rows:
        opened_at = datetime.fromtimestamp(int(row["ts_millis_open"]) / 1000, SHANGHAI)
        parsed.append({
            "stock_code": str(row.get("symbol", ""))[:6],
            "trade_date": opened_at.date(),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
            "turnover": float(row["turnover"]),
        })
    returned = {item["stock_code"] for item in parsed}
    requested = {value[:6] for value in symbols}
    if len(requested) >= 10 and len(returned) < max(1, int(len(requested) * 0.8)):
        raise FTShareError(
            "FTShare bulk candles returned only "
            f"{len(returned)}/{len(requested)} symbols; refusing a partial snapshot"
        )
    return pd.DataFrame(parsed)


def fetch_daily_candles(
    symbols: list[str],
    start_date: date,
    end_date: date,
    url: str | None = None,
) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()
    endpoint = url or os.getenv("FTSHARE_MCP_URL", DEFAULT_MCP_URL)
    return asyncio.run(_fetch_daily_candles(symbols, start_date, end_date, endpoint))


def extract_0931_entries(candles: pd.DataFrame) -> pd.DataFrame:
    """Extract the first continuous-auction minute and calculate its true VWAP."""
    columns = (
        "stock_code", "trade_date", "entry_0931_open", "entry_0931_vwap",
        "entry_0931_volume", "entry_0931_close",
    )
    if candles.empty:
        return pd.DataFrame(columns=columns)
    required = {
        "symbol", "open", "close", "volume", "turnover",
        "ts_millis", "ts_millis_open",
    }
    missing = sorted(required - set(candles.columns))
    if missing:
        raise ValueError(f"minute candles missing columns: {', '.join(missing)}")

    frame = candles.copy()
    frame["closed_at"] = frame["ts_millis"].map(
        lambda value: datetime.fromtimestamp(int(value) / 1000, SHANGHAI)
    )
    frame["opened_at"] = frame["ts_millis_open"].map(
        lambda value: datetime.fromtimestamp(int(value) / 1000, SHANGHAI)
    )
    frame = frame[
        frame["closed_at"].map(lambda value: value.time()) == time(9, 31)
    ]
    frame = frame[
        frame["opened_at"].map(lambda value: value.time()) == time(9, 30)
    ].copy()
    for column in ("open", "close", "volume", "turnover"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame[frame["volume"].gt(0) & frame["turnover"].gt(0)]
    frame["stock_code"] = frame["symbol"].astype(str).str[:6].str.zfill(6)
    frame["trade_date"] = frame["opened_at"].map(lambda value: value.date())
    frame["entry_0931_open"] = frame["open"]
    frame["entry_0931_vwap"] = frame["turnover"] / frame["volume"]
    frame["entry_0931_volume"] = frame["volume"]
    frame["entry_0931_close"] = frame["close"]
    return (
        frame.loc[:, columns]
        .drop_duplicates(["stock_code", "trade_date"], keep="last")
        .sort_values(["trade_date", "stock_code"])
        .reset_index(drop=True)
    )


async def _fetch_minute_entries(
    symbols: list[str],
    start_date: date,
    end_date: date,
    url: str,
    symbol_batch_size: int,
) -> pd.DataFrame:
    if end_date < start_date or (end_date - start_date).days > 2:
        raise ValueError("FTShare minute request must cover one to three calendar days")
    since_ms = int(
        datetime.combine(start_date, time(9, 25), SHANGHAI).timestamp() * 1000
    )
    until_ms = int(
        datetime.combine(end_date, time(9, 32), SHANGHAI).timestamp() * 1000
    )
    rows: list[dict] = []
    async with streamable_http_client(url) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            async def fetch_chunk(symbols_chunk: list[str]) -> dict:
                return await _call(session, "ft_stock_candlesticks_batch", {
                    "symbols": symbols_chunk,
                    "interval_unit": "Minute",
                    "interval_value": 1,
                    "adjust_kind": "None",
                    "since_ts_millis": since_ms,
                    "until_ts_millis": until_ms,
                    "limit": 10 if start_date == end_date else 500,
                })
            payloads = await asyncio.gather(*(
                fetch_chunk(symbols_chunk)
                for symbols_chunk in _chunks(symbols, symbol_batch_size)
            ))
            for payload in payloads:
                rows.extend(payload["data"])
    return extract_0931_entries(pd.DataFrame(rows))


def fetch_minute_entries(
    symbols: list[str],
    start_date: date,
    end_date: date,
    url: str | None = None,
    symbol_batch_size: int = 100,
) -> pd.DataFrame:
    if not symbols:
        return extract_0931_entries(pd.DataFrame())
    if not 1 <= symbol_batch_size <= 500:
        raise ValueError("symbol_batch_size must be between 1 and 500")
    endpoint = url or os.getenv("FTSHARE_MCP_URL", DEFAULT_MCP_URL)
    return asyncio.run(_fetch_minute_entries(
        symbols, start_date, end_date, endpoint, symbol_batch_size
    ))


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.replace(tzinfo=SHANGHAI) if parsed.tzinfo is None else parsed.astimezone(SHANGHAI)


def _news_available_at(row: dict) -> datetime | None:
    published_at = _parse_time(row.get("publish_time"))
    fetched_at = _parse_time(row.get("fetch_time"))
    return fetched_at or published_at


def _news_in_window(row: dict, start_at: datetime, end_at: datetime) -> bool:
    available_at = _news_available_at(row)
    return available_at is not None and start_at <= available_at <= end_at


async def _fetch_news(
    queries: list[str],
    start_at: datetime,
    end_at: datetime,
    limit_per_query: int,
    url: str,
) -> list[dict]:
    found: dict[str, dict] = {}
    async with streamable_http_client(url) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            for query in queries:
                try:
                    payload = await _call(session, "ft_semantic_search_news_handler", {
                        "query": query,
                        "start_time": start_at.astimezone(SHANGHAI).isoformat(),
                        "end_time": end_at.astimezone(SHANGHAI).isoformat(),
                        "limit": limit_per_query,
                    })
                except FTShareError:
                    continue
                for row in payload["data"]:
                    if not _news_in_window(row, start_at, end_at):
                        continue
                    key = str(row.get("news_id") or row.get("article_url") or "")
                    if not key:
                        continue
                    item = dict(row)
                    item["matched_query"] = query
                    item["available_at"] = _news_available_at(row).isoformat()
                    found[f"{query}:{key}"] = item
    return list(found.values())


def fetch_news(
    queries: list[str],
    start_at: datetime,
    end_at: datetime,
    limit_per_query: int = 3,
    url: str | None = None,
) -> list[dict]:
    endpoint = url or os.getenv("FTSHARE_MCP_URL", DEFAULT_MCP_URL)
    if start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=SHANGHAI)
    if end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=SHANGHAI)
    if start_at > end_at:
        raise ValueError("news start_at must not be after end_at")
    return asyncio.run(_fetch_news(
        queries, start_at, end_at, limit_per_query, endpoint
    ))
