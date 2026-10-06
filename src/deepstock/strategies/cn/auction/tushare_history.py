# -*- coding: utf-8 -*-
"""Historical Tushare minute labels for the opening-auction model."""
from __future__ import annotations

from datetime import date

import pandas as pd

from deepstock.strategies.cn.auction.providers import get_pro


ENTRY_COLUMNS = (
    "stock_code",
    "trade_date",
    "entry_0931_open",
    "entry_0931_vwap",
    "entry_0931_volume",
    "entry_0931_close",
)


def normalize_tushare_stk_mins_0931(
    frame: pd.DataFrame,
    trade_date: date,
) -> pd.DataFrame:
    """Return the completed 09:30-09:31 bar in the execution-cache schema."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=ENTRY_COLUMNS)
    required = {
        "ts_code", "trade_time", "open", "high", "low", "close", "vol", "amount"
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Tushare stk_mins response missing columns: {', '.join(missing)}")

    out = frame.copy()
    out["trade_time"] = pd.to_datetime(out["trade_time"], errors="coerce")
    target = pd.Timestamp.combine(trade_date, pd.Timestamp("09:31:00").time())
    out = out[out["trade_time"].eq(target)].copy()
    for column in ("open", "high", "low", "close", "vol", "amount"):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    valid_ohlc = (
        out[["open", "high", "low", "close"]].gt(0).all(axis=1)
        & out["high"].ge(out[["open", "close", "low"]].max(axis=1))
        & out["low"].le(out[["open", "close", "high"]].min(axis=1))
    )
    out = out[valid_ohlc & out["vol"].gt(0) & out["amount"].gt(0)].copy()
    out["entry_0931_vwap"] = out["amount"] / out["vol"]
    out = out[
        out["entry_0931_vwap"].between(out["low"] * 0.999, out["high"] * 1.001)
    ].copy()
    out["stock_code"] = out["ts_code"].astype(str).str[:6].str.zfill(6)
    out["trade_date"] = trade_date
    out["entry_0931_open"] = out["open"]
    out["entry_0931_volume"] = out["vol"]
    out["entry_0931_close"] = out["close"]
    return (
        out.loc[:, ENTRY_COLUMNS]
        .drop_duplicates(["stock_code", "trade_date"], keep="last")
        .sort_values("stock_code")
        .reset_index(drop=True)
    )


def fetch_tushare_historical_0931(
    symbols: list[str],
    trade_date: date,
    *,
    pro=None,
) -> pd.DataFrame:
    """Fetch one trading day's first completed continuous-auction minute.

    The currently subscribed ``stk_mins`` entitlement permits one request per
    hour, so callers should put the full point-in-time universe in one request.
    """
    if not symbols:
        return pd.DataFrame(columns=ENTRY_COLUMNS)
    client = pro or get_pro()
    frame = client.stk_mins(
        ts_code=",".join(symbols),
        freq="1min",
        start_date=f"{trade_date.isoformat()} 09:30:00",
        end_date=f"{trade_date.isoformat()} 09:31:00",
    )
    return normalize_tushare_stk_mins_0931(frame, trade_date)
