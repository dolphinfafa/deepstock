# -*- coding: utf-8 -*-
"""Universe and market-data loading for the short-term MVP."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import time

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from deepstock.strategies.cn.auction.repository import load_members, load_bars
from deepstock.strategies.cn.auction.providers import get_pro


@dataclass(frozen=True)
class ShortTermMember:
    company_id: str
    security_id: str
    stock_code: str
    name: str
    industry_name: str | None = None

    @property
    def symbol(self) -> str:
        suffix = "SH" if self.stock_code.startswith("6") else "SZ"
        return f"{self.stock_code}.{suffix}"


@dataclass(frozen=True)
class UniverseSnapshot:
    index_code: str
    snapshot_date: date
    members: tuple[ShortTermMember, ...]
    missing_codes: tuple[str, ...]


def _parse_yyyymmdd(value: object) -> date:
    return pd.Timestamp(str(value)).date()


def load_members_by_codes(
    db: Session,
    stock_codes: set[str] | list[str] | tuple[str, ...],
) -> tuple[ShortTermMember, ...]:
    return load_members(stock_codes)


def load_current_hs300(db: Session, as_of: date | None = None) -> UniverseSnapshot:
    """Load the latest available CSI 300 snapshot and map it to local securities."""
    end = as_of or date.today()
    start = end - timedelta(days=120)
    frame = get_pro().index_weight(
        index_code="000300.SH",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
    if frame is None or frame.empty:
        raise RuntimeError("Tushare returned no CSI 300 index weights")
    latest = frame["trade_date"].max()
    codes = sorted({str(value)[:6] for value in frame[frame["trade_date"] == latest]["con_code"]})

    members = load_members_by_codes(db, codes)
    by_code = {member.stock_code: member for member in members}
    return UniverseSnapshot(
        index_code="000300.SH",
        snapshot_date=_parse_yyyymmdd(latest),
        members=tuple(by_code[code] for code in codes if code in by_code),
        missing_codes=tuple(code for code in codes if code not in by_code),
    )


def fetch_tushare_auction_snapshot(
    members: tuple[ShortTermMember, ...],
    signal_date: date,
    extra_codes: tuple[str, ...] = (),
) -> pd.DataFrame:
    """Fetch one completed Tushare stock-auction snapshot and filter the universe."""
    frame = get_pro().stk_auction(
        trade_date=signal_date.strftime("%Y%m%d"),
        ts_type="STK",
    )
    if frame is None or frame.empty:
        return pd.DataFrame()
    wanted = {member.stock_code for member in members} | {
        str(value)[:6].zfill(6) for value in extra_codes
    }
    return frame[frame["ts_code"].astype(str).str[:6].isin(wanted)].copy()


def load_market_bars(
    db: Session,
    members: tuple[ShortTermMember, ...],
    start_date: date,
    end_date: date | None = None,
) -> pd.DataFrame:
    return load_bars(members, start_date, end_date)


def fetch_tushare_recent_bars(
    members: tuple[ShortTermMember, ...],
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """Fetch recent raw daily bars one market date at a time.

    Tushare's per-date response covers the whole market and is materially more
    reliable for a 300-stock refresh than FTShare's flattened bulk MCP result.
    """
    if __import__("os").getenv("DEEPSTOCK_AUCTION_OFFLINE") == "true":
        return pd.DataFrame()
    if start_date > end_date:
        return pd.DataFrame()
    pro = get_pro()
    calendar = pro.trade_cal(
        exchange="SSE",
        start_date=start_date.strftime("%Y%m%d"),
        end_date=end_date.strftime("%Y%m%d"),
        is_open="1",
        fields="cal_date,is_open",
    )
    if calendar is None or calendar.empty:
        return pd.DataFrame()

    wanted = {member.stock_code for member in members}
    rows: list[dict] = []
    for value in sorted(calendar["cal_date"].astype(str).tolist()):
        daily = pro.daily(trade_date=value)
        if daily is None or daily.empty:
            continue
        daily = daily[daily["ts_code"].str[:6].isin(wanted)]
        for _, item in daily.iterrows():
            rows.append({
                "stock_code": str(item["ts_code"])[:6],
                "trade_date": _parse_yyyymmdd(item["trade_date"]),
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "volume": float(item["vol"]) * 100.0,
                "turnover": float(item.get("amount") or 0.0) * 1000.0,
            })
        time.sleep(0.05)
    return pd.DataFrame(rows)


def load_signal_day_suspensions(
    members: tuple[ShortTermMember, ...],
    signal_date: date,
) -> set[str]:
    """Validate the signal date and return known full-day suspensions."""
    pro = get_pro()
    value = signal_date.strftime("%Y%m%d")
    calendar = pro.trade_cal(
        exchange="SSE",
        start_date=value,
        end_date=value,
        fields="cal_date,is_open",
    )
    if calendar is None or calendar.empty or int(calendar.iloc[0]["is_open"]) != 1:
        raise ValueError(f"signal date {signal_date} is not an SSE trading day")

    suspended = pro.suspend_d(
        trade_date=value,
        suspend_type="S",
        fields="ts_code,trade_date,suspend_type",
    )
    if suspended is None or suspended.empty:
        return set()
    wanted = {member.stock_code for member in members}
    return {
        str(ts_code)[:6]
        for ts_code in suspended["ts_code"]
        if str(ts_code)[:6] in wanted
    }


def latest_trading_day_before(signal_date: date) -> date:
    calendar = get_pro().trade_cal(
        exchange="SSE",
        start_date=(signal_date - timedelta(days=30)).strftime("%Y%m%d"),
        end_date=(signal_date - timedelta(days=1)).strftime("%Y%m%d"),
        is_open="1",
        fields="cal_date,is_open",
    )
    if calendar is None or calendar.empty:
        raise RuntimeError(f"no prior SSE trading day found for {signal_date}")
    return _parse_yyyymmdd(calendar["cal_date"].astype(str).max())


def overlay_recent_bars(
    local_bars: pd.DataFrame,
    remote_bars: pd.DataFrame,
    members: tuple[ShortTermMember, ...],
) -> pd.DataFrame:
    """Overlay recent raw bars in memory without mutating Deepstock tables."""
    if remote_bars.empty:
        return local_bars.copy()
    by_code = {member.stock_code: member for member in members}
    remote = remote_bars[remote_bars["stock_code"].isin(by_code)].copy()
    remote["trade_date"] = pd.to_datetime(remote["trade_date"])
    remote["security_id"] = remote["stock_code"].map(lambda code: by_code[code].security_id)
    remote["company_id"] = remote["stock_code"].map(lambda code: by_code[code].company_id)
    remote["company_name"] = remote["stock_code"].map(lambda code: by_code[code].name)
    remote["industry_name"] = remote["stock_code"].map(
        lambda code: by_code[code].industry_name
    )

    reference = local_bars[
        local_bars["market_cap"].notna()
        & local_bars["close"].notna()
        & local_bars["close"].gt(0)
    ].copy()
    reference["trade_date"] = pd.to_datetime(reference["trade_date"])
    reference["implied_shares"] = reference["market_cap"] / reference["close"]
    estimated: list[pd.DataFrame] = []
    for security_id, group in remote.groupby("security_id", sort=False):
        history = reference[reference["security_id"] == security_id]
        if history.empty:
            group = group.copy()
            group["market_cap"] = np.nan
            estimated.append(group)
            continue
        joined = pd.merge_asof(
            group.sort_values("trade_date"),
            history[["trade_date", "implied_shares"]].sort_values("trade_date"),
            on="trade_date",
            direction="backward",
        )
        joined["market_cap"] = joined["close"] * joined["implied_shares"]
        estimated.append(joined.drop(columns="implied_shares"))
    remote = pd.concat(estimated, ignore_index=True) if estimated else remote

    columns = list(local_bars.columns)
    combined = pd.concat([local_bars, remote.reindex(columns=columns)], ignore_index=True)
    combined["trade_date"] = pd.to_datetime(combined["trade_date"]).dt.date
    combined = combined.sort_values(["security_id", "trade_date"])
    combined = combined.drop_duplicates(["security_id", "trade_date"], keep="last")
    combined["market_cap"] = combined.groupby("security_id")["market_cap"].ffill()
    return combined.reset_index(drop=True)
