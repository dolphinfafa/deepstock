"""Optional, bounded A-share source audit; never a trading/strategy dependency."""
from __future__ import annotations
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import time
import numpy as np
import pandas as pd
from .store import DataQualityError, write_json

SDK_COMMIT = "41e56376fafa3abae0f6538f271eafd6af26d27f"
HOSTS = ("121.36.248.138", "123.60.47.136", "121.37.207.165")
SYMBOLS = ("000001.SZ", "000333.SZ", "300750.SZ", "600519.SH", "601398.SH", "510300.SH")
MINUTE_SYMBOLS = ("000001.SZ", "510300.SH")


class RequestBudget:
    def __init__(self, limit=70, interval=1., clock=time.monotonic, sleep=time.sleep):
        self.limit, self.interval, self.clock, self.sleep = limit, interval, clock, sleep
        self.last = None
        self.events = []

    def consume(self, host, kind):
        if len(self.events) >= self.limit:
            raise DataQualityError("70-request hard budget reached (includes handshakes/pages/retries)")
        if self.last is not None:
            self.sleep(max(0., self.interval - (self.clock() - self.last)))
        self.last = self.clock()
        self.events.append({"host": host, "kind": kind, "sent_at_utc": datetime.now(timezone.utc).isoformat()})


class CountedSocket:
    def __init__(self, socket, budget, host):
        self.socket, self.budget, self.host = socket, budget, host

    def sendall(self, data):
        self.budget.consume(self.host, "tcp_sendall_including_setup")
        return self.socket.sendall(data)

    def __getattr__(self, name):
        return getattr(self.socket, name)


def make_client(host, budget, evidence_dir):
    """Lazy SDK import, no global patch, no hidden ping/failover/heartbeat."""
    os.environ["EASY_TDX_CONFIG_DIR"] = str(evidence_dir / "sdk-config")
    from easy_tdx import MacClient
    from easy_tdx.transport.sync import TdxConnection

    class AuditedConnection(TdxConnection):
        def _send_setup(self):
            self._sock = CountedSocket(self._sock, budget, host)
            super()._send_setup()

        def execute(self, cmd):
            value = super().execute(cmd)
            params = {k: int(v) if isinstance(v, (int, np.integer)) else v for k, v in vars(cmd).items()}
            # Persist decoded page returns BEFORE SDK concatenation/fallback.
            rows = [asdict(x) if is_dataclass(x) else x for x in value] if isinstance(value, list) else value
            name = f"page-{len(budget.events):03d}.json"
            import json
            rows = json.loads(json.dumps(rows, default=str))
            write_json(evidence_dir / name, {"server": host, "command": type(cmd).__name__, "params": params,
                                           "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "decoded_sdk_page": rows})
            return value

    class NoRepairClient(MacClient):
        def _local_recompute_qfq(self, *args, **kwargs):
            raise DataQualityError("SDK QFQ repair rejected; retain page evidence, no recomputed prices")

    client = NoRepairClient(host, port=7709, timeout=3., auto_reconnect=False, heartbeat_interval=0)
    client._conn = AuditedConnection(host, 7709, 3.)
    return client


def clean_tdx_frame(raw, metadata):
    """Versioned units/time semantics; no price, activity or gap imputation."""
    from .store import clean_frame
    if metadata.get("normalizer") != "easy-tdx-cn-v1" or metadata.get("volume_unit") != "shares" or metadata.get("amount_unit") != "CNY":
        raise DataQualityError("Explicit easy-tdx normalizer/units required")
    frame = raw.copy()
    if {"datetime", "open", "high", "low", "close", "vol", "amount"}.difference(frame):
        raise DataQualityError("Missing easy-tdx bar fields")
    dates = pd.to_datetime(frame.pop("datetime"), errors="coerce", format="mixed")
    frame["symbol"] = metadata["symbol"]
    frame = frame.rename(columns={"vol": "volume"})
    minute = metadata["period"] == "MIN_1"
    if minute:
        if metadata.get("bar_time") != "start":
            raise DataQualityError("Fixed SDK minute start semantics required")
        frame["timestamp"] = dates.dt.tz_localize("Asia/Shanghai") if dates.dt.tz is None else dates.dt.tz_convert("Asia/Shanghai")
        frame["bar_end"] = frame.timestamp + pd.Timedelta(minutes=1)
    else:
        frame["date"] = dates.dt.strftime("%Y-%m-%d")
    next_metadata = {**metadata, "endpoint": "normalized_easy_tdx", "timezone": "Asia/Shanghai"}
    clean, rejected, quality, kind = clean_frame(frame, next_metadata)
    quality.update(raw_ordered=bool(dates.is_monotonic_increasing), normalizer="easy-tdx-cn-v1",
                   volume_unit="shares_declared_pending_cross_source_validation", amount_unit="CNY_declared",
                   adjustment=metadata["adjustment"], bar_time="start" if minute else "session")
    return clean, rejected, quality, "minute_bars" if minute else kind


def compare_daily(actual, reference, symbol):
    """No scale fitting. Tick/rounding tolerance is fixed before outcomes."""
    tick = .001 if symbol == "510300.SH" else .01
    ref = reference.copy()
    if "turnover" in ref and "amount" not in ref:
        ref = ref.rename(columns={"turnover": "amount"})
    both = actual.merge(ref, on="date", suffixes=("_tdx", "_reference"), validate="one_to_one")
    differences = []
    for field in ["open", "high", "low", "close", "volume", "amount"]:
        if field + "_reference" not in both:
            continue
        a, r = both[field + "_tdx"], both[field + "_reference"]
        tolerance = pd.Series(tick + 1e-6, index=both.index) if field in {"open", "high", "low", "close"} else r.abs() * .001 + (1 if field == "volume" else .01)
        bad = ~np.isfinite(a) | ~np.isfinite(r) | (a - r).abs().gt(tolerance)
        for i in both.index[bad]:
            differences.append({"symbol": symbol, "date": str(both.loc[i, "date"]), "field": field,
                                "tdx": float(a.loc[i]), "reference": float(r.loc[i]), "difference": float(a.loc[i] - r.loc[i]), "tolerance": float(tolerance.loc[i])})
    return {"matched_sessions": len(both), "threshold_exceedances": len(differences),
            "status": "no_independent_overlap" if both.empty else "differences_detected" if differences else "within_fixed_tolerances"}, differences


def daily_coverage(frame, sessions):
    dates = pd.DatetimeIndex(pd.to_datetime(frame.date))
    expected = sessions[(sessions >= dates.min()) & (sessions <= dates.max())]
    return {"missing_sessions": [str(x.date()) for x in expected.difference(dates)],
            "unexpected_sessions": [str(x.date()) for x in dates.difference(sessions)],
            "latest_complete_session": str(sessions[-1].date()), "latest_observed_session": str(dates.max().date()),
            "current_through_latest_complete_session": bool(dates.max() == sessions[-1])}


def minute_audit(frame, sessions):
    stamps = pd.DatetimeIndex(pd.to_datetime(frame.timestamp))
    expected = pd.DatetimeIndex([])
    for day in sessions:
        start = pd.Timestamp(day).tz_localize("Asia/Shanghai")
        parts = [pd.date_range(start + pd.Timedelta(hours=9, minutes=30), periods=120, freq="min"),
                 pd.date_range(start + pd.Timedelta(hours=13), periods=120, freq="min")]
        for part in parts:
            expected = expected.append(part)
    selected = stamps[stamps.normalize().tz_localize(None).isin(sessions)]
    # All returned bars are checked for out-of-session labels, not only the latest five days.
    minutes = stamps.hour * 60 + stamps.minute
    legal = ((minutes >= 570) & (minutes < 690)) | ((minutes >= 780) & (minutes < 900))
    invalid = stamps[~legal | (stamps.second != 0)]
    return {"expected_minutes": len(expected), "observed_latest_five_minutes": len(selected),
            "missing_minutes": [str(t) for t in expected.difference(selected)], "unexpected_minutes": [str(t) for t in selected.difference(expected)],
            "non_session_labels": [str(t) for t in invalid], "independent_minute_reference": "unavailable_for_this_window_accuracy_not_independently_proven"}


def aggregate_minutes(frame, sessions):
    f = frame.copy()
    f["date"] = pd.to_datetime(f.timestamp).dt.strftime("%Y-%m-%d")
    f = f.loc[f.date.isin([str(d.date()) for d in sessions])].sort_values("timestamp")
    return f.groupby("date", as_index=False).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                                                close=("close", "last"), volume=("volume", "sum"), amount=("amount", "sum"))
