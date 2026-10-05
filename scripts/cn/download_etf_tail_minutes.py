#!/usr/bin/env python3
"""Download licensed ETF history or an explicitly limited public recent pilot."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from deepstock.web.config import settings
from deepstock.strategies.cn.tail_momentum import prepare_sessions
from deepstock.research_control import research_pause


EASTMONEY_URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
EASTMONEY_PARAMS = {
    "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
    "ut": "7eea3edcaed734bea9cbfc24409ed989",  # Public quote-client identifier, not an account secret.
    "ndays": "5", "iscr": "0", "secid": "1.510300",
}


def exchange_calendar(client: httpx.Client, token: str, start: date, end: date) -> pd.DataFrame:
    frame = request(client, token, "trade_cal", {"exchange": "SSE", "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d"), "is_open": "1"})
    if frame.empty or "cal_date" not in frame:
        raise ValueError("Exchange calendar unavailable; do not substitute weekdays")
    return pd.DataFrame({"date": pd.to_datetime(frame["cal_date"], format="%Y%m%d").sort_values().dt.strftime("%Y-%m-%d")})


def normalize_eastmoney(payload: dict) -> pd.DataFrame:
    data = payload.get("data") or {}
    if payload.get("rc") != 0 or data.get("code") != "510300" or not data.get("trends"):
        raise ValueError("Public ETF quote response is empty or for the wrong instrument")
    # fields2 is the declared eight-column order; f56 is shares/100 (lots).
    frame = pd.DataFrame([row.split(",") for row in data["trends"]],
                         columns=["timestamp", "open", "close", "high", "low", "volume", "amount", "average_price"])
    for name in ("open", "close", "high", "low", "volume", "amount"):
        frame[name] = pd.to_numeric(frame[name], errors="raise")
    frame["volume"] *= 100
    frame["symbol"] = "510300.SH"
    # Keep the auction row separate; continuous bars end at 09:31 .. 15:00.
    return frame[["symbol", "timestamp", "open", "high", "low", "close", "volume", "amount"]].sort_values("timestamp").reset_index(drop=True)


def download_recent(output: Path, *, imported_payload: dict | None = None) -> dict:
    """At most five recent sessions, never presented as long-history reproduction."""
    output.mkdir(parents=True, exist_ok=True)
    token = os.getenv("TUSHARE_TOKEN") or os.getenv("TUSHARE_API_TOKEN")
    report = {"provider": "Eastmoney public recent minute quotes", "endpoint": EASTMONEY_URL,
              "captured_at_utc": datetime.now(timezone.utc).isoformat(), "symbol": "510300.SH",
              "stock_minute_endpoint_called": False, "maximum_sessions": 5,
              "transport": "imported_public_response" if imported_payload is not None else "direct_https"}
    try:
        if not token:
            raise ValueError("Tushare calendar token missing; cannot independently verify exchange sessions")
        with httpx.Client(timeout=30) as client:
            if imported_payload is None:
                response = client.get(EASTMONEY_URL, params=EASTMONEY_PARAMS,
                                      headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
                response.raise_for_status()
                payload = response.json()
            else:
                payload = imported_payload
            (output / "raw-eastmoney.json").write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
            bars = normalize_eastmoney(payload)
            timestamps = pd.to_datetime(bars["timestamp"])
            start, end = timestamps.min().date(), timestamps.max().date()
            if end >= datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).date():
                raise ValueError("Recent endpoint includes an unfinished/future session; export completed sessions explicitly")
            calendar = exchange_calendar(client, token, start, end)
        prepare_sessions(bars, pd.DatetimeIndex(pd.to_datetime(calendar["date"])))
        bars.to_csv(output / "minutes.csv.gz", index=False)
        calendar.to_csv(output / "calendar.csv", index=False)
        manifest = {"data_kind": "market", "provider": report["provider"], "interval_minutes": 1,
                    "timezone": "Asia/Shanghai", "timestamp_convention": "end", "adjustment": "none",
                    "volume_unit": "shares", "amount_unit": "CNY", "source_volume_unit": "lots_of_100_shares",
                    "actual_from": str(timestamps.min()), "actual_to": str(timestamps.max()),
                    "captured_at_utc": report["captured_at_utc"], "rows": len(bars), "sessions": len(calendar),
                    "corporate_action_status": "price_only_unverified", "coverage_limit": "at_most_five_recent_sessions",
                    "license": "Public display quotes, exploratory local use only; no raw redistribution or validation-grade licence claim",
                    "definition_source": "https://github.com/akfamily/akshare/blob/master/akshare/fund/fund_etf_em.py",
                    "unit_validation": "amount/(volume*100) checked against each minute OHLC by prepare_sessions",
                    "sha256": {name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in ("raw-eastmoney.json", "minutes.csv.gz", "calendar.csv")}}
        (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report.update(status="downloaded_recent_price_only", rows=len(bars), sessions=len(calendar), actual_from=manifest["actual_from"], actual_to=manifest["actual_to"])
    except (httpx.HTTPError, ValueError, KeyError) as error:
        report.update(status="blocked", reason=str(error).replace(token, "[redacted]") if token else str(error))
    (output / "access-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def request(client: httpx.Client, token: str, name: str, params: dict) -> pd.DataFrame:
    response = client.post("https://api.tushare.pro", json={"api_name": name, "token": token, "params": params, "fields": ""})
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        # Provider messages can contain identifiers; redact the credential explicitly.
        message = str(payload.get("msg", "unknown error")).replace(token, "[redacted]")
        raise ValueError(f"{name}: code={payload.get('code')}; {message}")
    data = payload.get("data") or {}
    return pd.DataFrame(data.get("items") or [], columns=data.get("fields") or [])


def normalize(frame: pd.DataFrame) -> pd.DataFrame:
    needed = {"ts_code", "trade_time", "open", "high", "low", "close", "vol", "amount"}
    if needed.difference(frame.columns) or frame.empty:
        raise ValueError("etf_mins did not return usable minute bars")
    return frame.loc[:, ["ts_code", "trade_time", "open", "high", "low", "close", "vol", "amount"]].rename(
        columns={"ts_code": "symbol", "trade_time": "timestamp", "vol": "volume"}
    ).sort_values(["symbol", "timestamp"]).reset_index(drop=True)


def download(output: Path, start: date, end: date, *, probe_only: bool = False, maximum_requests: int = 120) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    token = os.getenv("TUSHARE_TOKEN") or os.getenv("TUSHARE_API_TOKEN")
    report = {"provider": "Tushare", "endpoint": "etf_mins", "symbol": "510300.SH", "requested_from": start.isoformat(),
              "requested_to": end.isoformat(), "captured_at_utc": datetime.now(timezone.utc).isoformat(),
              "stock_minute_endpoint_called": False, "probe_only": probe_only, "requests": 0}
    try:
        if not token:
            raise ValueError("Local TUSHARE_TOKEN is not configured")
        if end < start or maximum_requests < 1:
            raise ValueError("Invalid download range/request limit")
        frames = []
        cursor = start
        with httpx.Client(timeout=40) as client:
            while cursor <= end:
                if report["requests"] >= maximum_requests:
                    raise ValueError("Request ceiling reached; no silent partial-data backtest")
                until = min(end, cursor + timedelta(days=27))
                report["requests"] += 1
                frame = request(client, token, "etf_mins", {"ts_code": "510300.SH", "freq": "1min",
                    "start_date": f"{cursor} 09:00:00", "end_date": f"{until} 16:00:00"})
                if len(frame) >= 8000:
                    raise ValueError("Provider row limit reached; dataset may be truncated")
                if len(frame):
                    frames.append(normalize(frame))
                print(json.dumps({"window_start": str(cursor), "window_end": str(until), "rows": len(frame)}), flush=True)
                if probe_only:
                    report.update(status="probe_succeeded", rows=len(frame))
                    break
                cursor = until + timedelta(days=1)
                time.sleep(1)
            if not frames:
                raise ValueError("No ETF minute rows returned")
            bars = pd.concat(frames, ignore_index=True)
            if probe_only:
                bars.to_csv(output / "probe-minutes.csv.gz", index=False)
            else:
                calendar = exchange_calendar(client, token, start, end)
                bars.to_csv(output / "minutes.csv.gz", index=False)
                calendar.to_csv(output / "calendar.csv", index=False)
                manifest = {"data_kind": "market", "provider": "Tushare etf_mins", "interval_minutes": 1,
                    "timezone": "Asia/Shanghai", "timestamp_convention": "end", "adjustment": "none", "volume_unit": "shares", "amount_unit": "CNY",
                    "requested_from": str(start), "requested_to": str(end), "actual_from": str(bars["timestamp"].min()), "actual_to": str(bars["timestamp"].max()),
                    "captured_at_utc": datetime.now(timezone.utc).isoformat(), "rows": len(bars),
                    "corporate_action_status": "price_only_unverified", "license": "Existing account access; local internal research, no redistribution",
                    "definition_source": "https://tushare.pro/document/2?doc_id=387",
                    "sha256": {name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in ("minutes.csv.gz", "calendar.csv")}}
                (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                report.update(status="downloaded_price_only", rows=len(bars), actual_from=manifest["actual_from"], actual_to=manifest["actual_to"])
    except (httpx.HTTPError, ValueError, KeyError) as error:
        message = str(error).replace(token, "[redacted]") if token else str(error)
        report.update(status="blocked", reason=message)
    (output / "access-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=settings.project_root / "artifacts/research/cn-etf-tail-momentum/data")
    parser.add_argument("--from", dest="start", type=date.fromisoformat, default=date(2021, 1, 4))
    parser.add_argument("--to", dest="end", type=date.fromisoformat, default=date(2026, 10, 5))
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--maximum-requests", type=int, default=120)
    parser.add_argument("--provider", choices=("tushare", "eastmoney_recent"), default="tushare")
    parser.add_argument("--import-eastmoney-stdin", action="store_true", help="Import a raw public response obtained on an authorised data node")
    args = parser.parse_args()
    paused = research_pause("cn_etf_tail_momentum", settings.project_root)
    if paused:
        print(json.dumps(paused, ensure_ascii=False))
        return
    if args.import_eastmoney_stdin and args.provider != "eastmoney_recent":
        parser.error("--import-eastmoney-stdin requires --provider eastmoney_recent")
    try:
        payload = json.load(sys.stdin) if args.import_eastmoney_stdin else None
    except (ValueError, UnicodeError) as error:
        # A failed upstream fetch must not leave an earlier successful status.
        args.output_dir.mkdir(parents=True, exist_ok=True)
        result = {"provider": "Eastmoney public recent minute quotes", "endpoint": EASTMONEY_URL,
                  "captured_at_utc": datetime.now(timezone.utc).isoformat(), "status": "blocked",
                  "reason": f"Upstream public response is not usable JSON: {type(error).__name__}",
                  "stock_minute_endpoint_called": False}
        (args.output_dir / "access-report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result))
        raise SystemExit(1) from None
    result = download_recent(args.output_dir, imported_payload=payload) if args.provider == "eastmoney_recent" else download(args.output_dir, args.start, args.end, probe_only=args.probe_only, maximum_requests=args.maximum_requests)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
