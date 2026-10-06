# -*- coding: utf-8 -*-
"""Point-in-time Tushare minute snapshots for paper-trading entries."""
from __future__ import annotations

from datetime import date, datetime, time
import json
import time as time_module
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from deepstock.strategies.cn.auction.providers import get_pro


SHANGHAI = ZoneInfo("Asia/Shanghai")
ENTRY_BAR_TIME = time(9, 31)
ENTRY_COLUMNS = (
    "stock_code",
    "trade_date",
    "entry_0931_open",
    "entry_0931_high",
    "entry_0931_low",
    "entry_0931_vwap",
    "entry_0931_volume",
    "entry_0931_amount",
    "entry_0931_close",
    "bar_time",
    "observed_at",
    "raw_json",
)


def _empty_entries() -> pd.DataFrame:
    return pd.DataFrame(columns=ENTRY_COLUMNS)


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _symbol(value: str) -> str:
    text = str(value).strip().upper()
    code = text[:6].zfill(6)
    if "." in text:
        return text
    return f"{code}.SH" if code.startswith("6") else f"{code}.SZ"


def normalize_tushare_rt_min(
    frame: pd.DataFrame,
    trade_date: date,
    observed_at: datetime,
    target_bar_time: time = ENTRY_BAR_TIME,
) -> pd.DataFrame:
    """Normalize the completed 09:30-09:31 bar and reject other minutes."""
    if frame is None or frame.empty:
        return _empty_entries()
    required = {
        "ts_code", "time", "open", "high", "low", "close", "vol", "amount",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(
            f"Tushare rt_min response missing columns: {', '.join(missing)}"
        )
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=SHANGHAI)
    observed_at = observed_at.astimezone(SHANGHAI)

    entries: list[dict] = []
    for raw in frame.to_dict("records"):
        parsed = pd.to_datetime(raw.get("time"), errors="coerce")
        if pd.isna(parsed):
            continue
        bar_at = parsed.to_pydatetime()
        if bar_at.tzinfo is None:
            bar_at = bar_at.replace(tzinfo=SHANGHAI)
        else:
            bar_at = bar_at.astimezone(SHANGHAI)
        if bar_at.date() != trade_date or bar_at.time().replace(tzinfo=None) != target_bar_time:
            continue
        if observed_at < bar_at:
            continue

        numeric = {
            key: float(pd.to_numeric(raw.get(key), errors="coerce"))
            for key in ("open", "high", "low", "close", "vol", "amount")
        }
        if not all(np.isfinite(value) for value in numeric.values()):
            continue
        if numeric["vol"] <= 0 or numeric["amount"] <= 0:
            continue
        vwap = numeric["amount"] / numeric["vol"]
        if vwap <= 0:
            continue
        low = min(numeric["low"], numeric["high"])
        high = max(numeric["low"], numeric["high"])
        if low > 0 and high > 0 and not low * 0.999 <= vwap <= high * 1.001:
            continue
        entries.append({
            "stock_code": str(raw.get("ts_code") or "")[:6].zfill(6),
            "trade_date": trade_date,
            "entry_0931_open": numeric["open"],
            "entry_0931_high": numeric["high"],
            "entry_0931_low": numeric["low"],
            "entry_0931_vwap": vwap,
            "entry_0931_volume": numeric["vol"],
            "entry_0931_amount": numeric["amount"],
            "entry_0931_close": numeric["close"],
            "bar_time": bar_at.isoformat(timespec="seconds"),
            "observed_at": observed_at.isoformat(timespec="seconds"),
            "raw_json": json.dumps(
                raw, ensure_ascii=False, separators=(",", ":"), default=str
            ),
        })
    if not entries:
        return _empty_entries()
    return (
        pd.DataFrame(entries, columns=ENTRY_COLUMNS)
        .drop_duplicates(["stock_code", "trade_date"], keep="first")
        .sort_values(["trade_date", "stock_code"])
        .reset_index(drop=True)
    )


def fetch_tushare_rt_min_entries(
    symbols: list[str],
    trade_date: date,
    *,
    pro=None,
    observed_at: datetime | None = None,
    target_bar_time: time = ENTRY_BAR_TIME,
    batch_size: int = 50,
) -> pd.DataFrame:
    """Fetch the latest minute for up to 50 symbols per Tushare request."""
    if not symbols:
        return _empty_entries()
    if not 1 <= batch_size <= 50:
        raise ValueError("Tushare rt_min batch_size must be between 1 and 50")
    pro = pro or get_pro()
    observed_at = observed_at or datetime.now(SHANGHAI)
    normalized_symbols = sorted({_symbol(value) for value in symbols})
    frames: list[pd.DataFrame] = []
    for symbols_chunk in _chunks(normalized_symbols, batch_size):
        frame = pro.query(
            "rt_min", ts_code=",".join(symbols_chunk), freq="1MIN"
        )
        if frame is not None and not frame.empty:
            frames.append(frame)
    if not frames:
        return _empty_entries()
    raw_frame = pd.concat(frames, ignore_index=True)
    entries = normalize_tushare_rt_min(
        raw_frame,
        trade_date,
        observed_at,
        target_bar_time,
    )
    entries.attrs["response_rows"] = len(raw_frame)
    entries.attrs["response_bar_times"] = sorted({
        str(value) for value in raw_frame["time"].dropna().tolist()
    })
    return entries


def poll_tushare_rt_min_entries(
    symbols: list[str],
    trade_date: date,
    *,
    pro=None,
    wait_seconds: float = 90,
    poll_seconds: float = 3,
    target_bar_time: time = ENTRY_BAR_TIME,
    now_fn: Callable[[], datetime] | None = None,
    monotonic_fn: Callable[[], float] = time_module.monotonic,
    sleep_fn: Callable[[float], None] = time_module.sleep,
) -> tuple[pd.DataFrame, dict]:
    """Poll until the completed target bar is observed or the deadline expires."""
    if wait_seconds < 0:
        raise ValueError("wait_seconds must be non-negative")
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    now_fn = now_fn or (lambda: datetime.now(SHANGHAI))
    requested = {str(value)[:6].zfill(6) for value in symbols}
    collected: dict[str, dict] = {}
    last_signatures: dict[str, tuple] = {}
    stable_counts: dict[str, int] = {}
    attempts = 0
    errors: list[str] = []
    response_rows = 0
    response_bar_times: set[str] = set()
    deadline = monotonic_fn() + wait_seconds
    while True:
        attempts += 1
        observed_at = now_fn()
        try:
            frame = fetch_tushare_rt_min_entries(
                symbols,
                trade_date,
                pro=pro,
                observed_at=observed_at,
                target_bar_time=target_bar_time,
            )
            response_rows = int(frame.attrs.get("response_rows", response_rows))
            response_bar_times.update(frame.attrs.get("response_bar_times", []))
            for row in frame.to_dict("records"):
                code = row["stock_code"]
                signature = (
                    row["bar_time"],
                    row["entry_0931_open"],
                    row["entry_0931_high"],
                    row["entry_0931_low"],
                    row["entry_0931_close"],
                    row["entry_0931_volume"],
                    row["entry_0931_amount"],
                )
                stable_counts[code] = (
                    stable_counts.get(code, 0) + 1
                    if last_signatures.get(code) == signature else 1
                )
                last_signatures[code] = signature
                if stable_counts[code] >= 2:
                    collected.setdefault(code, row)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        if requested.issubset(collected):
            break
        remaining = deadline - monotonic_fn()
        if remaining <= 0:
            break
        sleep_fn(min(poll_seconds, remaining))
    entries = (
        pd.DataFrame(list(collected.values()), columns=ENTRY_COLUMNS)
        if collected else _empty_entries()
    )
    return entries, {
        "source": "tushare.rt_min",
        "attempts": attempts,
        "requested_symbols": len(requested),
        "observed_symbols": len(collected),
        "stable_observations_required": 2,
        "response_rows": response_rows,
        "response_bar_times": sorted(response_bar_times)[-5:],
        "errors": errors[-3:],
    }
