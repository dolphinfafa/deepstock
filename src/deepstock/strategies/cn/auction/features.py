# -*- coding: utf-8 -*-
"""Point-in-time daily features for the short-horizon A-share MVP."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd


BAR_COLUMNS = (
    "security_id", "trade_date", "open", "high", "low", "close", "volume", "market_cap",
)

RAW_FEATURES = (
    "return_1d",
    "return_5d",
    "return_20d",
    "previous_intraday_return",
    "previous_range",
    "volume_ratio_20d",
    "volatility_20d",
    "previous_close_position",
    "turnover_proxy",
    "size_log",
)

# Fixed limits avoid learning clip thresholds from the validation period.
_FEATURE_LIMITS = {
    "return_1d": (-0.30, 0.30),
    "return_5d": (-0.60, 0.60),
    "return_20d": (-0.80, 0.80),
    "previous_intraday_return": (-0.25, 0.25),
    "previous_range": (0.0, 0.40),
    "volume_ratio_20d": (-0.95, 10.0),
    "volatility_20d": (0.0, 0.20),
    "previous_close_position": (-1.0, 2.0),
    "turnover_proxy": (0.0, 1.0),
    "size_log": (10.0, 40.0),
}

FEATURE_COLUMNS = tuple(f"{name}_cs" for name in RAW_FEATURES) + (
    "market_return_1d",
    "market_return_5d",
)


def _validate_bars(bars: pd.DataFrame) -> None:
    missing = [column for column in BAR_COLUMNS if column not in bars.columns]
    if missing:
        raise ValueError(f"market bars missing columns: {', '.join(missing)}")
    if bars.empty:
        raise ValueError("market bars are empty")


def _price_limit_pct(stock_code: object) -> float:
    """Conservative board-aware opening limit approximation for backtests."""
    code = str(stock_code or "")[:6]
    if code.startswith(("300", "301", "688", "689")):
        return 0.195
    if code.startswith(("4", "8", "9")):
        return 0.295
    return 0.095


def _build_one_security(group: pd.DataFrame) -> pd.DataFrame:
    frame = group.sort_values("trade_date").copy()
    frame["market_cap"] = frame["market_cap"].ffill()

    previous_close = frame["close"].shift(1)
    previous_open = frame["open"].shift(1)
    previous_volume = frame["volume"].shift(1)
    daily_return = frame["close"].pct_change(fill_method=None)

    frame["return_1d"] = previous_close / frame["close"].shift(2) - 1.0
    frame["return_5d"] = previous_close / frame["close"].shift(6) - 1.0
    frame["return_20d"] = previous_close / frame["close"].shift(21) - 1.0
    frame["previous_intraday_return"] = previous_close / previous_open - 1.0
    frame["previous_range"] = (
        frame["high"].shift(1) - frame["low"].shift(1)
    ) / previous_close
    frame["volume_ratio_20d"] = (
        previous_volume
        / previous_volume.rolling(20, min_periods=10).mean()
        - 1.0
    )
    frame["volatility_20d"] = daily_return.shift(1).rolling(20, min_periods=10).std()

    previous_span = (frame["high"].shift(1) - frame["low"].shift(1)).replace(0, np.nan)
    frame["previous_close_position"] = (
        previous_close - frame["low"].shift(1)
    ) / previous_span
    frame["turnover_proxy"] = (
        previous_volume * previous_close / frame["market_cap"].shift(1)
    )
    frame["size_log"] = np.log(frame["market_cap"].shift(1).where(
        frame["market_cap"].shift(1) > 0
    ))

    # Signal at day D is formed before the open. Buying at D open and selling at
    # D+1 close respects A-share T+1. No field from D after the open is a feature.
    frame["target_date"] = frame["trade_date"].shift(-1)
    frame["target_return"] = frame["close"].shift(-1) / frame["open"] - 1.0
    frame["opening_gap"] = frame["open"] / previous_close - 1.0
    if "stock_code" in frame.columns:
        limits = frame["stock_code"].map(_price_limit_pct)
    else:
        limits = pd.Series(0.095, index=frame.index)
    frame["historically_buyable"] = (
        frame["open"].gt(0)
        & frame["volume"].gt(0)
        & frame["opening_gap"].lt(limits)
    )
    return frame


def build_feature_frame(bars: pd.DataFrame) -> pd.DataFrame:
    """Build one row per security and signal date without future feature leakage."""
    _validate_bars(bars)
    source = bars.copy()
    source["trade_date"] = pd.to_datetime(source["trade_date"]).dt.date
    source = source.sort_values(["security_id", "trade_date"])
    source = source.drop_duplicates(["security_id", "trade_date"], keep="last")

    pieces: list[pd.DataFrame] = []
    for security_id, group in source.groupby("security_id", sort=False):
        built = _build_one_security(group)
        built["security_id"] = security_id
        pieces.append(built)
    frame = pd.concat(pieces, ignore_index=True)

    for name in RAW_FEATURES:
        low, high = _FEATURE_LIMITS[name]
        frame[name] = frame[name].clip(low, high)
        by_date = frame.groupby("trade_date")[name]
        std = by_date.transform("std").replace(0, np.nan)
        frame[f"{name}_cs"] = (frame[name] - by_date.transform("mean")) / std

    frame["market_return_1d"] = (
        frame.groupby("trade_date")["return_1d"].transform("median").clip(-0.10, 0.10)
    )
    frame["market_return_5d"] = (
        frame.groupby("trade_date")["return_5d"].transform("median").clip(-0.20, 0.20)
    )
    return frame.sort_values(["trade_date", "security_id"]).reset_index(drop=True)


def append_signal_rows(bars: pd.DataFrame, signal_date: date) -> pd.DataFrame:
    """Append empty day-D rows so features can be formed from day D-1 data."""
    _validate_bars(bars)
    source = bars.copy()
    source["trade_date"] = pd.to_datetime(source["trade_date"]).dt.date
    source = source[source["trade_date"] < signal_date]
    if source.empty:
        raise ValueError(f"no market bars before signal date {signal_date}")

    latest = source.sort_values("trade_date").groupby("security_id", as_index=False).tail(1)
    rows: list[dict] = []
    for _, item in latest.iterrows():
        row = item.to_dict()
        row.update({
            "trade_date": signal_date,
            "open": np.nan,
            "high": np.nan,
            "low": np.nan,
            "close": np.nan,
            "volume": np.nan,
        })
        rows.append(row)
    return pd.concat([source, pd.DataFrame(rows)], ignore_index=True)
