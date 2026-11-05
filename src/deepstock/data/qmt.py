"""Explicit QMT minute contracts and snapshot diagnostics, outside strategies."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .store import DataQualityError

NORMALIZER = "qmt-native-minute-v1"
BOUNDARY = "left_closed_right_open"


def clean_qmt_minutes(raw: pd.DataFrame, metadata: dict):
    """Retain native labels/auction observations; never relabel as Tushare bars."""
    from .store import clean_frame

    expected = {"normalizer": NORMALIZER, "volume_unit": "lots_100_shares",
                "amount_unit": "CNY", "boundary": BOUNDARY,
                "adjustment": "none", "fill_data": False, "strategy_input_approved": False}
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise DataQualityError("Explicit unadjusted QMT minute contract required")
    required = {"symbol", "time", "open", "high", "low", "close", "volume", "amount"}
    if required.difference(raw):
        raise DataQualityError("Missing QMT minute fields")
    frame = raw.copy()
    millis = pd.to_numeric(frame["time"], errors="coerce")
    stamps = pd.to_datetime(millis, unit="ms", utc=True, errors="coerce").dt.tz_convert("Asia/Shanghai")
    scope = metadata.get("minute_window", "all_observations")
    if scope not in {"all_observations", "exact_0931_v1"}:
        raise DataQualityError("Unknown QMT minute selection contract")
    in_session = pd.Series(True, index=frame.index)
    if metadata.get("trade_date"):
        in_session = stamps.dt.date.eq(date.fromisoformat(metadata["trade_date"]))
    excluded = 0
    if scope == "exact_0931_v1":
        if not metadata.get("trade_date"):
            raise DataQualityError("Exact-minute view requires an explicit session")
        # Keep unknown/cross-session timestamps so their invalidity still blocks.
        wanted = (stamps.dt.hour.eq(9) & stamps.dt.minute.eq(31)) | stamps.isna() | ~in_session
        excluded = int((~wanted).sum())
        frame["provider_raw_row"] = np.arange(len(raw))
        frame = frame.loc[wanted].reset_index(drop=True)
        stamps = stamps.loc[wanted].reset_index(drop=True)
        in_session = in_session.loc[wanted].reset_index(drop=True)
    minute = stamps.dt.hour * 60 + stamps.dt.minute
    auction = minute.eq(570) & stamps.dt.second.eq(0)
    legal = ((minute.between(571, 690) | minute.between(781, 900)) | auction)
    legal &= stamps.dt.second.eq(0) & stamps.dt.microsecond.eq(0)
    legal &= in_session
    frame["timestamp"] = stamps.where(legal)
    frame["bar_start"] = (stamps - pd.Timedelta(minutes=1)).where(~auction & legal)
    frame["bar_end"] = stamps.where(~auction & legal)
    frame["bar_kind"] = np.where(auction, "opening_auction", "continuous")
    frame["boundary"] = np.where(auction, "separate_auction_observation", BOUNDARY)
    frame["provider_volume_lots"] = frame["volume"]
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce") * 100
    valid_symbol = frame.symbol.astype("string").str.fullmatch(r"\d{6}\.(SH|SZ)").fillna(False)
    frame.loc[~valid_symbol, "symbol"] = pd.NA
    # Generic OHLC/finite-value/conflict validation remains authoritative.
    clean, rejected, quality, _ = clean_frame(frame, {**metadata, "endpoint": "normalized_qmt_minutes",
                                                       "timezone": "Asia/Shanghai"})
    active = clean.volume.gt(0) & clean.amount.gt(0)
    invalid_vwap = active & ~(clean.amount / clean.volume).between(clean.low * .999, clean.high * 1.001)
    if invalid_vwap.any():
        extra = clean.loc[invalid_vwap].copy()
        extra["_quality_flags"] += "invalid_vwap;"
        rejected = pd.concat([rejected, extra], ignore_index=True)
        clean = clean.loc[~invalid_vwap].reset_index(drop=True)
        quality["invalid_rows"] += int(invalid_vwap.sum())
        quality["blocking"] = True
        quality["issues"].append("QMT volume-unit VWAP inconsistent with native OHLC")
    quality.update(normalizer=NORMALIZER, boundary=BOUNDARY, volume_unit="shares",
                   amount_unit="CNY", separate_auction_rows=int(auction.sum()),
                   non_session_labels=int((~legal).sum()),
                   minute_window=scope, excluded_observations_outside_window=excluded,
                   strategy_input_approved=False)
    return clean, rejected, quality, "qmt_minute_bars"


def snapshot_window_activity(ticks: pd.DataFrame, trade_date: date, *, right_closed: bool) -> dict:
    """Cumulative snapshot diagnostic only; not an exchange trade-tape OHLC bar.

    pvolume is optional: this SDK may leave it entirely zero. Nothing here
    fabricates intraminute highs/lows or grants strategy admission.
    """
    required = {"time", "volume", "amount"}
    if ticks.empty or required.difference(ticks):
        raise DataQualityError("Missing cumulative tick fields")
    columns = sorted(required | ({"pvolume"} if "pvolume" in ticks else set()))
    frame = ticks.loc[:, columns].copy().drop_duplicates()
    frame["time"] = pd.to_numeric(frame.time, errors="coerce")
    if frame.time.isna().any() or frame.time.duplicated().any():
        raise DataQualityError("Invalid/conflicting tick timestamps")
    frame = frame.sort_values("time")
    times = pd.to_datetime(frame.time, unit="ms", utc=True).dt.tz_convert("Asia/Shanghai")
    if not times.dt.date.eq(trade_date).all():
        raise DataQualityError("Tick capture includes another session")
    raw_shares_available = "pvolume" in frame and pd.to_numeric(frame.pvolume, errors="coerce").ne(0).any()
    for column in ["volume", "amount"] + (["pvolume"] if raw_shares_available else []):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if (~np.isfinite(frame[column]) | frame[column].lt(0)).any() or frame[column].diff().dropna().lt(0).any():
            raise DataQualityError("Invalid/reset cumulative activity")
    if raw_shares_available and (frame.pvolume - frame.volume * 100).abs().ge(100).any():
        raise DataQualityError("Raw share/hand unit reconciliation failed")
    start = pd.Timestamp.combine(trade_date, pd.Timestamp("09:30").time()).tz_localize("Asia/Shanghai")
    end = start + pd.Timedelta(minutes=1)
    before = frame.loc[times.le(start) if right_closed else times.lt(start)]
    through = frame.loc[times.le(end) if right_closed else times.lt(end)]
    if before.empty or through.empty:
        raise DataQualityError("Missing boundary observations")
    left, right = before.iloc[-1], through.iloc[-1]
    left_at = pd.to_datetime(left.time, unit="ms", utc=True).tz_convert("Asia/Shanghai")
    right_at = pd.to_datetime(right.time, unit="ms", utc=True).tz_convert("Asia/Shanghai")
    # The last 09:25 auction snapshot may be the final observation before open.
    if not start - pd.Timedelta(minutes=5) <= left_at <= start or not end - pd.Timedelta(seconds=6) <= right_at <= end:
        raise DataQualityError("Boundary snapshots are too distant")
    rounded = float((right.volume - left.volume) * 100)
    shares = float(right.pvolume - left.pvolume) if raw_shares_available else rounded
    amount = float(right.amount - left.amount)
    if shares <= 0 or amount <= 0:
        raise DataQualityError("No observed positive window activity")
    return {"boundary": "left_open_right_closed" if right_closed else BOUNDARY,
            "baseline_at": left_at.isoformat(), "terminal_at": right_at.isoformat(),
            "volume_shares": shares, "volume_rounded_lots_as_shares": rounded,
            "volume_source": "pvolume_shares" if raw_shares_available else "volume_lots_x100",
            "share_count_exact": bool(raw_shares_available),
            "lot_rounding_error_bound_shares": 0 if raw_shares_available else 100,
            "amount_cny": amount, "vwap": amount / shares,
            "snapshot_diagnostic_only": True, "strategy_input_approved": False}


def reconcile_snapshot_windows(tick_frames: dict, reference: pd.DataFrame, trade_date: date):
    """Fixed-tolerance diagnostic; independent OHLC equivalence stays unverified."""
    ref = reference.copy()
    if {"ts_code", "vol", "amount"}.difference(ref) or ref.ts_code.duplicated().any():
        raise DataQualityError("Require unique pinned reference symbols and activity")
    ref = ref.set_index("ts_code")
    result = {"sample_count": len(tick_frames), "right_closed": {}, "left_closed": {},
              "ohlc_equivalence_verified": False, "strategy_input_approved": False}
    private = {}
    for key, right_closed in [("right_closed", True), ("left_closed", False)]:
        counts = {"valid": 0, "blocked": 0, "volume_matches": 0, "volume_within_rounding_bound": 0,
                  "rounded_lot_samples": 0, "amount_matches": 0,
                  "vwap_within_1bp": 0, "vwap_within_5bp": 0, "block_reasons": {}}
        for symbol, ticks in tick_frames.items():
            private.setdefault(symbol, {})
            try:
                if symbol not in ref.index:
                    raise DataQualityError("Missing reference symbol")
                row = ref.loc[symbol]
                volume, amount = float(row.vol), float(row.amount)
                if not all(np.isfinite(v) and v > 0 for v in [volume, amount]):
                    raise DataQualityError("Invalid reference activity")
                flow = snapshot_window_activity(ticks, trade_date, right_closed=right_closed)
                error = abs(flow["vwap"] / (amount / volume) - 1)
                flow.update(reference_volume=volume, reference_amount=amount, relative_vwap_difference=error)
                private[symbol][key] = flow
                counts["valid"] += 1
                counts["volume_matches"] += int(abs(flow["volume_shares"] - volume) < .5)
                counts["volume_within_rounding_bound"] += int(abs(flow["volume_shares"] - volume) <= max(.5, flow["lot_rounding_error_bound_shares"]))
                counts["rounded_lot_samples"] += int(not flow["share_count_exact"])
                counts["amount_matches"] += int(abs(flow["amount_cny"] - amount) <= max(1., amount * 1e-7))
                counts["vwap_within_1bp"] += int(error <= .0001)
                counts["vwap_within_5bp"] += int(error <= .0005)
            except (DataQualityError, TypeError, ValueError) as exc:
                reason = str(exc)
                private[symbol][key] = {"status": "blocked", "reason": reason}
                counts["blocked"] += 1
                counts["block_reasons"][reason] = counts["block_reasons"].get(reason, 0) + 1
        result[key] = counts
    return result, private
