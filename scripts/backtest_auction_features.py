# -*- coding: utf-8 -*-
"""Download FTShare auction history and run matched three-model ablations."""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from datetime import date, timedelta
from pathlib import Path
import time
from typing import Any

import numpy as np
import pandas as pd
from deepstock.strategies.cn.auction.inputs import read_auction_csv as read_clean_csv
from deepstock.data.store import report_json
from deepstock.data.inventory import metadata_for
from deepstock.data.store import DataStore
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from deepstock.strategies.cn.auction.repository import SessionLocal
from deepstock.strategies.cn.auction.providers import get_pro
from deepstock.strategies.cn.auction.auction import (
    AuctionBacktestConfig,
    _historical_membership,
    attach_execution_entries,
    evaluate_execution_backtest,
    evaluate_optimized_execution_backtest,
    prepare_auction_feature_frame,
    prepare_tushare_auction_feature_frame,
    walk_forward_ablation,
)
from deepstock.strategies.cn.auction.data import (
    ShortTermMember,
    fetch_tushare_recent_bars,
    load_market_bars,
    load_members_by_codes,
    overlay_recent_bars,
)
from deepstock.strategies.cn.auction.features import build_feature_frame
from deepstock.strategies.cn.auction.ftshare import DEFAULT_MCP_URL, fetch_minute_entries
from deepstock.strategies.cn.auction.tushare_history import fetch_tushare_historical_0931
from scripts.probe_auction_sources import _fetch_ft_page, fetch_ftshare_full


DEFAULT_ROOT = "artifacts/auction_history"
DEFAULT_TUSHARE_ROOT = "artifacts/auction_history_tushare"


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _json_default(value: Any):
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if pd.isna(value):
        return None
    raise TypeError(f"cannot serialize {type(value).__name__}")


def fetch_universe_snapshots(start: date, end: date) -> pd.DataFrame:
    pro = get_pro()
    first_month = (pd.Period(start, freq="M") - 1)
    months = pd.period_range(first_month, pd.Period(end, freq="M"), freq="M")
    frames: list[pd.DataFrame] = []
    for month in months:
        frame = pro.index_weight(
            index_code="000300.SH",
            start_date=month.start_time.strftime("%Y%m%d"),
            end_date=month.end_time.strftime("%Y%m%d"),
        )
        if frame is None or frame.empty:
            continue
        frames.append(frame[["trade_date", "con_code", "weight"]].copy())
    if not frames:
        raise RuntimeError("Tushare returned no historical CSI 300 snapshots")
    snapshots = pd.concat(frames, ignore_index=True)
    snapshots = snapshots.rename(columns={
        "trade_date": "snapshot_date",
        "con_code": "ts_code",
    })
    snapshots["stock_code"] = snapshots["ts_code"].astype(str).str[:6]
    return snapshots.sort_values(["snapshot_date", "stock_code"]).reset_index(drop=True)


async def fetch_history(args: argparse.Namespace) -> dict:
    root = Path(args.output_root)
    raw_dir = root / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    snapshots = fetch_universe_snapshots(args.start, args.end)
    snapshots.to_csv(root / "universe_snapshots.csv", index=False)
    wanted = set(snapshots["stock_code"])

    calendar = get_pro().trade_cal(
        exchange="SSE",
        start_date=args.start.strftime("%Y%m%d"),
        end_date=args.end.strftime("%Y%m%d"),
        is_open="1",
        fields="cal_date,is_open",
    )
    trade_dates = sorted(calendar["cal_date"].astype(str).tolist())
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {}
    pending = [
        value for value in trade_dates
        if not (raw_dir / f"{value}.csv.gz").exists()
        and (args.retry_missing or manifest.get(value, {}).get("status") != "missing")
    ]

    async with streamable_http_client(args.ftshare_url) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()

            async def fetch_one(value: str) -> tuple[str, dict]:
                events: list[dict] = []
                first, event = await _fetch_ft_page(session, value, 1, 200)
                events.append(event)
                if event["error"]:
                    return value, {"status": "error", "error": event["error"]}
                total = int(event.get("total") or 0)
                if total < args.minimum_market_coverage:
                    return value, {"status": "missing", "market_rows": total}
                try:
                    full = await fetch_ftshare_full(
                        session, value, first, events, concurrency=args.page_concurrency
                    )
                except Exception as exc:
                    return value, {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
                full["trade_date"] = date(
                    int(value[:4]), int(value[4:6]), int(value[6:8])
                )
                full["stock_code"] = full["symbol"].astype(str).str[:6]
                selected = full[full["stock_code"].isin(wanted)].copy()
                selected.to_csv(raw_dir / f"{value}.csv.gz", index=False, compression="gzip")
                return value, {
                    "status": "ok",
                    "market_rows": len(full),
                    "selected_rows": len(selected),
                }

            for offset in range(0, len(pending), args.date_concurrency):
                batch = pending[offset:offset + args.date_concurrency]
                results = await asyncio.gather(*(fetch_one(value) for value in batch))
                for value, status in results:
                    manifest[value] = status
                    print(value, status, flush=True)
                manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
                )

    statuses = pd.Series([item.get("status") for item in manifest.values()]).value_counts()
    report = {
        "start": args.start.isoformat(),
        "end": args.end.isoformat(),
        "calendar_dates": len(trade_dates),
        "universe_snapshots": snapshots["snapshot_date"].nunique(),
        "universe_union": len(wanted),
        "status_counts": statuses.to_dict(),
    }
    (root / "fetch_report.json").write_text(
        report_json(report, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    return report


def _load_auction_rows(root: Path) -> pd.DataFrame:
    files = sorted((root / "raw").glob("*.csv.gz"))
    if not files:
        raise RuntimeError("no cached FTShare auction history")
    return pd.concat((read_clean_csv(path) for path in files), ignore_index=True)


def _load_historical_members(codes: set[str]) -> tuple[ShortTermMember, ...]:
    db = SessionLocal()
    try:
        return load_members_by_codes(db, codes)
    finally:
        db.close()


def _pending_minute_windows(
    windows: list[tuple[date, date]],
    output_dir: Path,
    manifest: dict[str, dict],
) -> list[tuple[date, date]]:
    pending: list[tuple[date, date]] = []
    for start, end in windows:
        key = f"{start:%Y%m%d}_{end:%Y%m%d}"
        path = output_dir / f"{key}.csv.gz"
        if path.exists() and manifest.get(key, {}).get("status") == "ok":
            continue
        pending.append((start, end))
    return pending


def _fetch_tushare_stock_history(
    ts_code: str,
    start: date,
    end: date,
    retries: int,
) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            frame = get_pro().stk_auction(
                ts_code=ts_code,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                ts_type="STK",
            )
            return pd.DataFrame() if frame is None else frame
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.0 + attempt)
    assert last_error is not None
    raise last_error


def _normalize_raw_trade_dates(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize cached/API trade dates before concatenation and sorting."""
    if frame.empty:
        return frame.copy()
    if "trade_date" not in frame.columns:
        raise ValueError("Tushare auction data missing trade_date")
    out = frame.copy()
    compact = (
        out["trade_date"]
        .astype("string")
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
        .str.replace("-", "", regex=False)
    )
    parsed = pd.to_datetime(compact, format="%Y%m%d", errors="coerce")
    if parsed.isna().any():
        invalid = sorted(set(compact[parsed.isna()].dropna().astype(str)))
        raise ValueError(f"Invalid Tushare trade_date values: {invalid[:5]}")
    out["trade_date"] = parsed.dt.strftime("%Y%m%d")
    return out


def fetch_tushare_history(args: argparse.Namespace) -> dict:
    root = Path(args.output_root)
    raw_dir = root / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    snapshots = fetch_universe_snapshots(args.start, args.end)
    snapshots.to_csv(root / "universe_snapshots.csv", index=False)
    ts_codes = sorted(set(snapshots["ts_code"].astype(str)))
    manifest_path = root / "tushare_manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    pending: list[tuple[str, date]] = []
    for value in ts_codes:
        path = raw_dir / f"{value.replace('.', '_')}.csv.gz"
        fetch_start = args.start
        if path.exists():
            cached = _normalize_raw_trade_dates(read_clean_csv(path))
            if not cached.empty:
                parsed_last = pd.to_datetime(
                    cached["trade_date"], format="%Y%m%d"
                ).max().date()
                fetch_start = max(fetch_start, parsed_last + timedelta(days=1))
        if fetch_start <= args.end:
            pending.append((value, fetch_start))

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {
            executor.submit(
                _fetch_tushare_stock_history,
                ts_code,
                fetch_start,
                args.end,
                args.retries,
            ): (ts_code, fetch_start)
            for ts_code, fetch_start in pending
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            ts_code, _ = futures[future]
            try:
                frame = future.result()
                if frame.empty:
                    status = {"status": "missing", "rows": 0}
                else:
                    path = raw_dir / f"{ts_code.replace('.', '_')}.csv.gz"
                    frame = _normalize_raw_trade_dates(frame)
                    if path.exists():
                        cached = _normalize_raw_trade_dates(read_clean_csv(path))
                        frame = pd.concat([cached, frame], ignore_index=True)
                    frame = frame.drop_duplicates(["ts_code", "trade_date"], keep="last")
                    frame = frame.sort_values("trade_date", ascending=False)
                    frame.to_csv(path, index=False, compression="gzip")
                    status = {
                        "status": "ok",
                        "rows": len(frame),
                        "first_date": str(frame["trade_date"].min()),
                        "last_date": str(frame["trade_date"].max()),
                    }
            except Exception as exc:
                status = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            manifest[ts_code] = status
            print(ts_code, status, flush=True)
            if completed % 10 == 0:
                manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
                )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    counts = pd.Series([item.get("status") for item in manifest.values()]).value_counts()
    report = {
        "source": "tushare.stk_auction",
        "start": args.start.isoformat(),
        "end": args.end.isoformat(),
        "universe_snapshots": snapshots["snapshot_date"].nunique(),
        "universe_union": len(ts_codes),
        "status_counts": counts.to_dict(),
    }
    (root / "fetch_report.json").write_text(
        report_json(report, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    return report


def fetch_execution_entries(args: argparse.Namespace) -> dict:
    root = Path(args.output_root)
    output_dir = root / "minute_entries"
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshots = read_clean_csv(root / "universe_snapshots.csv", dtype={"stock_code": str})
    snapshots["stock_code"] = snapshots["stock_code"].str[:6].str.zfill(6)
    codes = set(snapshots["stock_code"])
    members = _load_historical_members(codes)
    symbols_by_code = (
        snapshots.drop_duplicates("stock_code", keep="last")
        .set_index("stock_code")["ts_code"]
        .astype(str)
        .to_dict()
    )
    symbols_by_code.update({member.stock_code: member.symbol for member in members})

    calendar = get_pro().trade_cal(
        exchange="SSE",
        start_date=args.start.strftime("%Y%m%d"),
        end_date=args.end.strftime("%Y%m%d"),
        is_open="1",
        fields="cal_date,is_open",
    )
    trade_dates = [
        _parse_date(f"{value[:4]}-{value[4:6]}-{value[6:8]}")
        for value in sorted(calendar["cal_date"].astype(str))
    ]
    windows = [(value, value) for value in trade_dates]
    point_in_time = _historical_membership(pd.Series(trade_dates), snapshots)
    mapped_codes = set(symbols_by_code)
    manifest_path = root / "minute_manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    pending_before = _pending_minute_windows(windows, output_dir, manifest)
    maximum_windows = getattr(args, "maximum_windows_per_run", None)
    if maximum_windows is not None and maximum_windows < 1:
        raise ValueError("--maximum-windows-per-run must be at least 1")
    pending = (
        pending_before[:maximum_windows]
        if maximum_windows is not None else pending_before
    )

    minute_source = getattr(args, "minute_source", "ftshare")
    request_spacing = args.request_spacing
    if request_spacing is None:
        request_spacing = 3605.0 if minute_source == "tushare" else 0.5
    next_tushare_request_at = [0.0]

    def fetch_window(start: date, end: date) -> tuple[str, pd.DataFrame, dict]:
        key = f"{start:%Y%m%d}_{end:%Y%m%d}"
        last_error: Exception | None = None
        for attempt in range(args.retries + 1):
            if minute_source == "tushare":
                delay = next_tushare_request_at[0] - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
            try:
                expected = point_in_time[start] & mapped_codes
                expected_symbols = [
                    symbols_by_code[code] for code in sorted(expected)
                    if code in symbols_by_code
                ]
                entries = (
                    fetch_tushare_historical_0931(expected_symbols, start)
                    if minute_source == "tushare"
                    else fetch_minute_entries(
                        expected_symbols,
                        start,
                        end,
                        url=args.ftshare_url,
                        symbol_batch_size=args.symbol_batch_size,
                    )
                )
                if minute_source == "tushare":
                    next_tushare_request_at[0] = time.monotonic() + request_spacing
                returned = set(entries["stock_code"]) if not entries.empty else set()
                covered = len(expected & returned)
                coverage = covered / len(expected) if expected else 0.0
                status = {
                    "status": "ok" if coverage >= args.minimum_date_coverage else "incomplete",
                    "rows": len(entries),
                    "symbols": entries["stock_code"].nunique() if not entries.empty else 0,
                    "dates": entries["trade_date"].nunique() if not entries.empty else 0,
                    "point_in_time_members": len(expected),
                    "covered_members": covered,
                    "coverage": coverage,
                    "attempts": attempt + 1,
                    "source": (
                        "tushare.stk_mins" if minute_source == "tushare"
                        else "ftshare.stock_minutes_batch"
                    ),
                }
                return key, entries, status
            except Exception as exc:
                last_error = exc
                if minute_source == "tushare":
                    next_tushare_request_at[0] = time.monotonic() + request_spacing
                if attempt < args.retries:
                    wait = args.retry_wait_seconds * (attempt + 1)
                    if minute_source == "tushare" and "频率超限" in str(exc):
                        wait = max(wait, 3605.0)
                    time.sleep(wait)
        assert last_error is not None
        return key, pd.DataFrame(), {
            "status": "error",
            "error": f"{type(last_error).__name__}: {last_error}",
            "attempts": args.retries + 1,
        }

    if minute_source == "tushare" and args.date_concurrency != 1:
        raise ValueError("Tushare stk_mins backfill requires --date-concurrency 1")
    with ThreadPoolExecutor(max_workers=args.date_concurrency) as executor:
        futures = {
            executor.submit(fetch_window, start, end): (start, end)
            for start, end in pending
        }
        for future in as_completed(futures):
            key, entries, status = future.result()
            if not entries.empty:
                entries.to_csv(
                    output_dir / f"{key}.csv.gz", index=False, compression="gzip"
                )
                DataStore().import_file(output_dir / f"{key}.csv.gz", metadata_for(output_dir / f"{key}.csv.gz"))
            manifest[key] = status
            print(key, status, flush=True)
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, default=_json_default),
                encoding="utf-8",
            )
    requested_keys = {f"{start:%Y%m%d}_{end:%Y%m%d}" for start, end in windows}
    remaining = _pending_minute_windows(windows, output_dir, manifest)
    counts = pd.Series([
        manifest[key].get("status") for key in requested_keys if key in manifest
    ]).value_counts()
    report = {
        "source": (
            "Tushare stk_mins one-minute unadjusted candles"
            if minute_source == "tushare"
            else "FTShare one-minute unadjusted candles"
        ),
        "start": args.start.isoformat(),
        "end": args.end.isoformat(),
        "requested_symbols": len(set().union(*point_in_time.values()) & mapped_codes),
        "calendar_dates": len(trade_dates),
        "windows": len(windows),
        "pending_before_run": len(pending_before),
        "processed_this_run": len(pending),
        "remaining_windows": len(remaining),
        "remaining_dates": [start.isoformat() for start, _ in remaining],
        "status_counts": counts.to_dict(),
        "entry_definition": "09:30-09:31 turnover / volume VWAP",
    }
    (root / "minute_fetch_report.json").write_text(
        report_json(report, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    return report


def finalize_minute_backfill(args: argparse.Namespace) -> dict:
    """Run the frozen backtest only after every requested minute day is usable."""
    root = Path(args.output_root)
    output_dir = root / "minute_entries"
    manifest_path = root / "minute_manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists() else {}
    )
    calendar = get_pro().trade_cal(
        exchange="SSE",
        start_date=args.start.strftime("%Y%m%d"),
        end_date=args.end.strftime("%Y%m%d"),
        is_open="1",
        fields="cal_date,is_open",
    )
    trade_dates = [
        _parse_date(f"{value[:4]}-{value[4:6]}-{value[6:8]}")
        for value in sorted(calendar["cal_date"].astype(str))
    ]
    windows = [(value, value) for value in trade_dates]
    remaining = _pending_minute_windows(windows, output_dir, manifest)
    if remaining:
        return {
            "status": "waiting_for_minute_backfill",
            "start": args.start.isoformat(),
            "end": args.end.isoformat(),
            "completed_windows": len(windows) - len(remaining),
            "remaining_windows": len(remaining),
            "remaining_dates": [start.isoformat() for start, _ in remaining],
        }
    backtest_args = argparse.Namespace(
        output_root=args.output_root,
        start=date(2026, 1, 5),
        end=args.end,
        training_days=60,
        half_life_days=20,
        validation_days=60,
        refit_frequency="daily",
        minimum_cross_section=240,
        top=5,
        report_name=args.report_name,
        optimized_only=False,
        skip_alternate_exit=True,
    )
    report = run_execution_backtest(backtest_args)
    return {
        "status": "complete",
        "report_name": args.report_name,
        "execution_dates": report["diagnostics"]["execution_dates"],
        "validation_start": report["execution_backtest"]["validation_start"],
        "validation_end": report["execution_backtest"]["validation_end"],
    }


def _load_tushare_auction_rows(root: Path) -> pd.DataFrame:
    files = sorted((root / "raw").glob("*.csv.gz"))
    if not files:
        raise RuntimeError("no cached Tushare auction history")
    return pd.concat((read_clean_csv(path) for path in files), ignore_index=True)


def _load_minute_entries(root: Path) -> pd.DataFrame:
    manifest_path = root / "minute_manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    files = [
        path for path in sorted((root / "minute_entries").glob("*.csv.gz"))
        if manifest.get(path.name.removesuffix(".csv.gz"), {}).get("status") == "ok"
        and len(set(path.name.removesuffix(".csv.gz").split("_"))) == 1
    ]
    if not files:
        raise RuntimeError("no cached 09:31 minute entries")
    return pd.concat(
        (read_clean_csv(path, dtype={"stock_code": str}) for path in files),
        ignore_index=True,
    )


def _load_recent_daily_bars(root: Path, end: date) -> pd.DataFrame:
    path = root / "recent_daily_bars.csv.gz"
    if not path.exists():
        return pd.DataFrame()
    frame = read_clean_csv(path, dtype={"stock_code": str})
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
    return frame[frame["trade_date"] <= end].copy()


def _store_recent_daily_bars(
    root: Path,
    cached: pd.DataFrame,
    recent: pd.DataFrame,
) -> None:
    if cached.empty and recent.empty:
        return
    frame = pd.concat([cached, recent], ignore_index=True)
    frame["stock_code"] = frame["stock_code"].astype(str).str[:6].str.zfill(6)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
    frame = frame.drop_duplicates(["stock_code", "trade_date"], keep="last")
    frame.sort_values(["trade_date", "stock_code"]).to_csv(
        root / "recent_daily_bars.csv.gz", index=False, compression="gzip"
    )
    DataStore().import_file(root / "recent_daily_bars.csv.gz", metadata_for(root / "recent_daily_bars.csv.gz"))


def _format_execution_report(report: dict) -> str:
    lines = [
        "# CSI 300 executable opening-auction backtest",
        "",
        f"Validation: {report['execution_backtest']['validation_start']} to "
        f"{report['execution_backtest']['validation_end']}",
        "",
        "| Model | Top 5 mean | 20 bps | Universe | Spread | Win rate | Rank IC |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, metrics in report["execution_backtest"]["models"].items():
        lines.append(
            f"| {name} | {metrics['selected_mean_return']:.3%} | "
            f"{metrics['selected_mean_after_cost']['20']:.3%} | "
            f"{metrics['universe_mean_return']:.3%} | "
            f"{metrics['mean_return_spread']:.3%} | "
            f"{metrics['selected_stock_win_rate']:.2%} | "
            f"{metrics['mean_rank_ic']:.4f} |"
        )
    lines.extend([
        "",
        "Entry is the observed 09:30-09:31 minute VWAP; no auction-price fallback.",
        "Returns are per trade, not annualized. Capacity assumes at most 5% of that minute's notional.",
        "Research only; commissions, price impact and fill uncertainty remain model assumptions.",
        "",
    ])
    return "\n".join(lines)


def bound_replay_signal_dates(frame: pd.DataFrame, end: date) -> pd.DataFrame:
    """Retain training warm-up and future exit labels, but bound signal dates."""
    return frame.loc[frame["trade_date"] <= end].copy()


def run_execution_backtest(args: argparse.Namespace) -> dict:
    root = Path(args.output_root)
    snapshots = read_clean_csv(root / "universe_snapshots.csv", dtype={"stock_code": str})
    snapshots["stock_code"] = snapshots["stock_code"].str[:6].str.zfill(6)
    auction = _load_tushare_auction_rows(root)
    entries = _load_minute_entries(root)
    normalized_dates = auction["trade_date"].astype(str).str.replace("-", "", regex=False)
    auction_dates = pd.to_datetime(normalized_dates, format="%Y%m%d", errors="raise")
    start = max(args.start, auction_dates.min().date())
    end = min(args.end, auction_dates.max().date())
    members = _load_historical_members(set(snapshots["stock_code"]))

    db = SessionLocal()
    try:
        bars = load_market_bars(
            db, members, start - timedelta(days=45), end + timedelta(days=10)
        )
    finally:
        db.close()
    refresh_end = min(end + timedelta(days=10), date.today())
    cached = _load_recent_daily_bars(root, refresh_end)
    bars = overlay_recent_bars(bars, cached, members)
    coverage = bars.groupby("trade_date")["security_id"].nunique()
    eligible_local = coverage[coverage >= args.minimum_cross_section]
    local_through = max(eligible_local.index) if not eligible_local.empty else start
    refresh_start = max(start, local_through + timedelta(days=1))
    recent = fetch_tushare_recent_bars(members, refresh_start, refresh_end)
    _store_recent_daily_bars(root, cached, recent)
    bars = overlay_recent_bars(bars, recent, members)

    base = build_feature_frame(bars)
    feature_frame, diagnostics = prepare_tushare_auction_feature_frame(
        base, auction, snapshots
    )
    feature_frame = attach_execution_entries(feature_frame, entries)
    # Future bars may construct exits, but never create extra signal dates or
    # shift the frozen validation window after more history is downloaded.
    # Keep pre-start training/warm-up history; only the upper bound is applied
    # here. The fixed validation window is checked below before publication.
    feature_frame = bound_replay_signal_dates(feature_frame, end)
    config = AuctionBacktestConfig(
        training_window_days=args.training_days,
        validation_days=args.validation_days,
        half_life_days=args.half_life_days,
        minimum_cross_section=args.minimum_cross_section,
        top_k=args.top,
        refit_frequency=args.refit_frequency,
    )
    execution = (
        evaluate_optimized_execution_backtest(feature_frame, config)
        if args.optimized_only
        else evaluate_execution_backtest(
            feature_frame,
            config,
            include_alternate_exit=not args.skip_alternate_exit,
        )
    )
    if date.fromisoformat(execution["validation_start"]) < start or date.fromisoformat(execution["validation_end"]) > end:
        raise ValueError("Fixed validation window falls outside the declared signal interval")
    entry_rows = feature_frame["execution_tradable"].fillna(False)
    report = {
        "model": f"{config.refit_frequency}-refit Tushare auction production features",
        "data_start": start.isoformat(),
        "data_end": end.isoformat(),
        "mapped_historical_members": len(members),
        "diagnostics": {
            **diagnostics,
            "execution_rows": int(entry_rows.sum()),
            "execution_dates": int(feature_frame.loc[entry_rows, "trade_date"].nunique()),
        },
        "config": config.__dict__,
        "execution_backtest": execution,
    }
    (root / f"{args.report_name}.json").write_text(
        report_json(report, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    (root / f"{args.report_name}.md").write_text(
        _format_execution_report(report), encoding="utf-8"
    )
    return report


def _format_report(report: dict) -> str:
    lines = [
        "# CSI 300 opening-auction ablation",
        "",
        f"Auction dates: {report['diagnostics']['dates']}",
        f"Auction/local-open match rate (0.1%): {report['diagnostics']['open_match_rate_0_1pct']:.2%}",
        "",
    ]
    for target_name, target in report["targets"].items():
        lines.extend([
            f"## {target_name}",
            "",
            "| Model | Top 5 mean | Universe mean | Spread | Win rate | Rank IC | Daily t |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ])
        for model_name, metrics in target["models"].items():
            lines.append(
                f"| {model_name} | {metrics['selected_mean_return']:.3%} | "
                f"{metrics['universe_mean_return']:.3%} | "
                f"{metrics['mean_return_spread']:.3%} | "
                f"{metrics['selected_stock_win_rate']:.2%} | "
                f"{metrics['mean_rank_ic']:.4f} | "
                f"{metrics['selected_daily_t_stat']:.2f} |"
            )
        incremental = target["incremental_selected_return"]
        incremental_t = target["incremental_daily_t_stat"]
        lines.extend([
            "",
            f"Auction minus base: {incremental['auction_minus_base']:.3%} "
            f"(paired daily t={incremental_t['auction_minus_base']:.2f})",
            f"Combined minus base: {incremental['combined_minus_base']:.3%} "
            f"(paired daily t={incremental_t['combined_minus_base']:.2f})",
            f"Combined minus auction: {incremental['combined_minus_auction']:.3%} "
            f"(paired daily t={incremental_t['combined_minus_auction']:.2f})",
            "",
        ])
    lines.extend([
        "## Interpretation limits",
        "",
        "- All models use the same auction-open return labels for feature ablation.",
        "- A signal formed from the completed 09:25 snapshot cannot assume a fill at the auction price; reported returns are feature labels, not tradable PnL.",
        "- A deployable strategy still needs historical 09:30/09:31 entry prices, slippage, fees, limit-up and capacity simulation.",
        "- The historical FTShare auction archive has gaps and minute bars are not available at matching scale.",
        "- Historical CSI 300 membership is point-in-time, but unmapped local securities reduce each cross-section.",
        "",
    ])
    return "\n".join(lines)


def run_backtest(args: argparse.Namespace) -> dict:
    root = Path(args.output_root)
    snapshots = read_clean_csv(root / "universe_snapshots.csv", dtype={"stock_code": str})
    snapshots["stock_code"] = snapshots["stock_code"].str.zfill(6)
    auction = _load_auction_rows(root)
    auction["stock_code"] = auction["stock_code"].astype(str).str.zfill(6)
    auction["trade_date"] = pd.to_datetime(auction["trade_date"]).dt.date
    start = auction["trade_date"].min()
    end = auction["trade_date"].max()
    members = _load_historical_members(set(snapshots["stock_code"]))

    db = SessionLocal()
    try:
        bars = load_market_bars(
            db, members, start - timedelta(days=45), end + timedelta(days=10)
        )
    finally:
        db.close()
    base = build_feature_frame(bars)
    feature_frame, diagnostics = prepare_auction_feature_frame(
        base, auction, snapshots
    )
    config = AuctionBacktestConfig(
        training_window_days=args.training_days,
        validation_days=args.validation_days,
        minimum_cross_section=args.minimum_cross_section,
        top_k=args.top,
    )
    report = {
        "model": "monthly-refit weighted ridge three-way auction ablation",
        "data_start": start.isoformat(),
        "data_end": end.isoformat(),
        "mapped_historical_members": len(members),
        "diagnostics": diagnostics,
        "config": config.__dict__,
        "targets": {
            "same_day_close": walk_forward_ablation(
                feature_frame, "target_same_day", "target_same_date", config
            ),
            "next_day_close": walk_forward_ablation(
                feature_frame, "target_next_close", "target_next_date", config
            ),
        },
    }
    (root / f"{args.report_name}.json").write_text(
        report_json(report, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    (root / f"{args.report_name}.md").write_text(
        _format_report(report), encoding="utf-8"
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CSI 300 opening-auction research")
    subparsers = parser.add_subparsers(dest="command", required=True)
    fetch = subparsers.add_parser("fetch")
    fetch.add_argument("--start", type=_parse_date, default=date(2023, 8, 1))
    fetch.add_argument("--end", type=_parse_date, default=date(2025, 1, 27))
    fetch.add_argument("--output-root", default=DEFAULT_ROOT)
    fetch.add_argument("--ftshare-url", default=DEFAULT_MCP_URL)
    fetch.add_argument("--minimum-market-coverage", type=int, default=4000)
    fetch.add_argument("--date-concurrency", type=int, default=2)
    fetch.add_argument("--page-concurrency", type=int, default=5)
    fetch.add_argument("--retry-missing", action="store_true")
    fetch.set_defaults(handler=lambda args: asyncio.run(fetch_history(args)))

    backtest = subparsers.add_parser("backtest")
    backtest.add_argument("--output-root", default=DEFAULT_ROOT)
    backtest.add_argument("--training-days", type=int, default=252)
    backtest.add_argument("--validation-days", type=int, default=60)
    backtest.add_argument("--minimum-cross-section", type=int, default=240)
    backtest.add_argument("--top", type=int, default=5)
    backtest.add_argument("--report-name", default="ablation_report")
    backtest.set_defaults(handler=run_backtest)

    tushare = subparsers.add_parser("fetch-tushare")
    tushare.add_argument("--start", type=_parse_date, default=date(2025, 1, 16))
    tushare.add_argument("--end", type=_parse_date, default=date.today())
    tushare.add_argument("--output-root", default=DEFAULT_TUSHARE_ROOT)
    tushare.add_argument("--concurrency", type=int, default=4)
    tushare.add_argument("--retries", type=int, default=2)
    tushare.set_defaults(handler=fetch_tushare_history)

    minute = subparsers.add_parser("fetch-minute-entries")
    minute.add_argument("--start", type=_parse_date, default=date(2026, 1, 1))
    minute.add_argument("--end", type=_parse_date, default=date.today())
    minute.add_argument("--output-root", default=DEFAULT_TUSHARE_ROOT)
    minute.add_argument("--ftshare-url", default=DEFAULT_MCP_URL)
    minute.add_argument(
        "--minute-source", choices=("tushare", "ftshare"), default="tushare"
    )
    minute.add_argument("--symbol-batch-size", type=int, default=50)
    minute.add_argument("--date-concurrency", type=int, default=1)
    minute.add_argument("--minimum-date-coverage", type=float, default=0.90)
    minute.add_argument("--retries", type=int, default=3)
    minute.add_argument("--retry-wait-seconds", type=float, default=5.0)
    minute.add_argument("--request-spacing", type=float, default=None)
    minute.add_argument("--maximum-windows-per-run", type=int)
    minute.set_defaults(handler=fetch_execution_entries)

    finalize = subparsers.add_parser("finalize-minute-backfill")
    finalize.add_argument("--start", type=_parse_date, default=date(2026, 8, 31))
    finalize.add_argument("--end", type=_parse_date, default=date(2026, 9, 29))
    finalize.add_argument("--output-root", default=DEFAULT_TUSHARE_ROOT)
    finalize.add_argument(
        "--report-name", default="execution_backtest_20260929_frozen"
    )
    finalize.set_defaults(handler=finalize_minute_backfill)

    live = subparsers.add_parser("backtest-live")
    live.add_argument("--start", type=_parse_date, default=date(2026, 1, 1))
    live.add_argument("--end", type=_parse_date, default=date.today())
    live.add_argument("--output-root", default=DEFAULT_TUSHARE_ROOT)
    live.add_argument("--training-days", type=int, default=60)
    live.add_argument("--half-life-days", type=int, default=20)
    live.add_argument("--validation-days", type=int, default=60)
    live.add_argument(
        "--refit-frequency", choices=("daily", "weekly", "monthly"), default="daily"
    )
    live.add_argument("--minimum-cross-section", type=int, default=240)
    live.add_argument("--top", type=int, default=5)
    live.add_argument("--report-name", default="execution_backtest_report")
    live.add_argument("--optimized-only", action="store_true")
    live.add_argument("--skip-alternate-exit", action="store_true")
    live.set_defaults(handler=run_execution_backtest)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = args.handler(args)
    print(report_json(report, ensure_ascii=False, indent=2, default=_json_default))


if __name__ == "__main__":
    main()
