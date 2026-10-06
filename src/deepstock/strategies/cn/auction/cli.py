# -*- coding: utf-8 -*-
"""CLI for backtesting and running the A-share short-horizon MVP."""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import date, datetime, time, timedelta
import math
from pathlib import Path
import time as clock
from typing import Any

import numpy as np
import pandas as pd
from deepstock.strategies.cn.auction.inputs import read_auction_csv as read_clean_csv
from deepstock.data.store import report_json, input_evidence
from deepstock.data.inventory import metadata_for
from deepstock.data.store import DataStore

from deepstock.strategies.cn.auction.repository import SessionLocal
from deepstock.strategies.cn.auction.auction import (
    PRODUCTION_POLICY_VERSION,
    AuctionBacktestConfig,
    CONTEXT_AUCTION_FEATURE_COLUMNS,
    DEFAULT_ENSEMBLE_WINDOWS,
    attach_execution_entries,
    evaluate_optimized_execution_backtest,
    fit_latest_auction,
    fit_latest_auction_ensemble,
    prepare_tushare_auction_feature_frame,
    rank_auction_signal,
    rank_auction_strategy,
)
from deepstock.strategies.cn.auction.data import (
    UniverseSnapshot,
    fetch_tushare_auction_snapshot,
    fetch_tushare_recent_bars,
    latest_trading_day_before,
    load_current_hs300,
    load_market_bars,
    load_members_by_codes,
    load_signal_day_suspensions,
    overlay_recent_bars,
)
from deepstock.strategies.cn.auction.features import append_signal_rows, build_feature_frame
from deepstock.strategies.cn.auction.ftshare import SHANGHAI, fetch_news
from deepstock.strategies.cn.auction.model import (
    ShortTermConfig,
    fit_latest,
    rank_signal,
    walk_forward_backtest,
)


DEFAULT_AUCTION_HISTORY_ROOT = "artifacts/auction_history_tushare"


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


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


def _config(args: argparse.Namespace) -> ShortTermConfig:
    return ShortTermConfig(
        training_window_days=args.training_days,
        validation_days=getattr(args, "validation_days", 252),
        minimum_cross_section=args.minimum_cross_section,
        top_k=args.top,
    )


def _universe_summary(snapshot: UniverseSnapshot) -> dict:
    return {
        "index_code": snapshot.index_code,
        "snapshot_date": snapshot.snapshot_date.isoformat(),
        "index_members": len(snapshot.members) + len(snapshot.missing_codes),
        "mapped_members": len(snapshot.members),
        "missing_codes": list(snapshot.missing_codes),
        "warning": "Latest constituents are used historically; MVP backtest has survivorship bias.",
    }


def _candidate_dict(row: pd.Series, model=None) -> dict:
    item = {
        "rank": int(row["prediction_rank"]),
        "stock_code": row.get("stock_code"),
        "name": row.get("company_name"),
        "up_probability": float(row["up_probability"]),
        "predicted_return": float(row["predicted_return"]),
        "prediction_score": float(row["prediction_score"]),
    }
    if pd.notna(row.get("model_rank")) and int(row["model_rank"]) != item["rank"]:
        item["model_rank"] = int(row["model_rank"])
    if pd.notna(row.get("target_return")):
        item["realized_return"] = float(row["target_return"])
    if model is not None:
        item["return_drivers"] = model.explain_return(row)
    return item


def _print_backtest(report: dict) -> None:
    universe = report["universe"]
    summary = report["backtest"]
    metrics = summary["metrics"]
    print("Short-term A-share MVP backtest")
    print(
        f"Universe: CSI 300 snapshot {universe['snapshot_date']}, "
        f"mapped {universe['mapped_members']}/{universe['index_members']}"
    )
    print(
        f"Validation: {summary['validation_start']} to {summary['validation_end']}, "
        f"monthly refits={summary['refit_count']}"
    )
    print(
        f"Top {metrics['top_k']} mean return: {metrics['selected_mean_return']:.3%}; "
        f"universe: {metrics['universe_mean_return']:.3%}; "
        f"spread: {metrics['mean_return_spread']:.3%}"
    )
    print(
        f"Top {metrics['top_k']} stock win rate: {metrics['selected_stock_win_rate']:.2%}; "
        f"daily basket win rate: {metrics['selected_daily_win_rate']:.2%}; "
        f"mean rank IC: {metrics['mean_rank_ic']:.4f}"
    )
    print("Latest validation selections:")
    for item in report["latest_validation_candidates"]:
        print(
            f"  {item['rank']:>2}. {item['stock_code']} {item['name']}  "
            f"p(up)={item['up_probability']:.1%}  "
            f"pred={item['predicted_return']:.2%}  "
            f"realized={item.get('realized_return', float('nan')):.2%}"
        )


def run_backtest(args: argparse.Namespace) -> dict:
    db = SessionLocal()
    try:
        snapshot = load_current_hs300(db, args.end)
        bars = load_market_bars(db, snapshot.members, args.start, args.end)
    finally:
        db.close()

    config = _config(args)
    feature_frame = build_feature_frame(bars)
    result = walk_forward_backtest(feature_frame, config)
    latest_date = result.predictions["trade_date"].max()
    latest = result.predictions[
        (result.predictions["trade_date"] == latest_date)
        & (result.predictions["prediction_rank"] <= config.top_k)
    ].sort_values("prediction_rank")
    report = {
        "model": "monthly-refit weighted ridge MVP",
        "target": "buy at signal-day open, sell at next-trading-day close (A-share T+1)",
        "universe": _universe_summary(snapshot),
        "backtest": result.summary(),
        "latest_validation_candidates": [_candidate_dict(row) for _, row in latest.iterrows()],
        "limitations": [
            "Current CSI 300 constituents are used for historical dates.",
            "Raw local prices are not yet adjusted with a persisted corporate-action series.",
            "News is excluded from model scores until a point-in-time history is available.",
            "Results exclude commissions, slippage, and partial fills.",
        ],
    }
    if args.json:
        print(report_json(report, ensure_ascii=False, indent=2, default=_json_default))
    else:
        _print_backtest(report)
    return report


def _latest_complete_date(bars: pd.DataFrame, minimum_cross_section: int) -> date:
    coverage = bars.groupby("trade_date")["security_id"].nunique()
    eligible = coverage[coverage >= minimum_cross_section]
    if eligible.empty:
        raise RuntimeError("recent bars do not contain a complete enough cross-section")
    return max(eligible.index)


def _recent_refresh_start(
    history_start: date,
    signal_date: date,
    refresh_days: int,
    local_complete_through: date,
) -> date:
    # Once a complete date is known, daily history is immutable for this model.
    # Start at the first missing day; callers retain refresh_days for CLI compatibility.
    _ = signal_date, refresh_days
    return max(history_start, local_complete_through + timedelta(days=1))


def _attach_news(
    candidates: list[dict],
    market_data_through: date,
    signal_date: date,
) -> None:
    start_at = datetime.combine(market_data_through, time(15, 0), SHANGHAI)
    end_at = datetime.combine(signal_date, time(9, 15), SHANGHAI)
    queries = [item["name"] for item in candidates if item.get("name")]
    news = fetch_news(queries, start_at, end_at, limit_per_query=3)
    by_query: dict[str, list[dict]] = {query: [] for query in queries}
    for item in news:
        matched_query = item["matched_query"]
        title = str(item.get("title") or "")
        if matched_query not in title:
            continue
        compact = {
            "title": item.get("title"),
            "publish_time": item.get("publish_time"),
            "fetch_time": item.get("fetch_time"),
            "available_at": item.get("available_at"),
            "source": item.get("source_site"),
            "url": item.get("article_url"),
            "semantic_score": item.get("score"),
        }
        by_query.setdefault(matched_query, []).append(compact)
    for candidate in candidates:
        candidate["news_evidence"] = by_query.get(candidate.get("name"), [])


def _print_prediction(report: dict) -> None:
    print("Short-term A-share MVP prediction")
    print(
        f"Signal cutoff: {report['signal_cutoff']}; market data through "
        f"{report['market_data_through']}"
    )
    print(
        f"Model training: {report['model_training']['start']} to "
        f"{report['model_training']['end']} ({report['model_training']['rows']} rows)"
    )
    for item in report["candidates"]:
        print(
            f"  {item['rank']:>2}. {item['stock_code']} {item['name']}  "
            f"p(up)={item['up_probability']:.1%}  "
            f"expected={item['predicted_return']:.2%}  score={item['prediction_score']:.2f}"
        )
        for news in item.get("news_evidence", []):
            visible_at = news.get("available_at") or news.get("publish_time")
            print(f"      news {visible_at} [{news['source']}] {news['title']}")
    if str(report.get("news_status", "")).startswith("unavailable"):
        print(f"News evidence {report['news_status']}")


def run_prediction(args: argparse.Namespace) -> dict:
    config = _config(args)
    db = SessionLocal()
    try:
        snapshot = load_current_hs300(db, args.signal_date)
        local_bars = load_market_bars(
            db, snapshot.members, args.start, args.signal_date - timedelta(days=1)
        )
    finally:
        db.close()

    suspended_codes = load_signal_day_suspensions(
        snapshot.members, args.signal_date
    )
    expected_market_date = latest_trading_day_before(args.signal_date)

    local_complete_through = _latest_complete_date(
        local_bars, config.minimum_cross_section
    )
    refresh_start = _recent_refresh_start(
        args.start,
        args.signal_date,
        args.refresh_days,
        local_complete_through,
    )
    remote = fetch_tushare_recent_bars(
        snapshot.members,
        refresh_start,
        args.signal_date - timedelta(days=1),
    )
    bars = overlay_recent_bars(local_bars, remote, snapshot.members)
    required_snapshot = max(
        config.minimum_cross_section,
        math.ceil(len(snapshot.members) * 0.8),
    )
    market_data_through = _latest_complete_date(bars, required_snapshot)
    if market_data_through != expected_market_date:
        raise RuntimeError(
            f"market data ends at {market_data_through}, expected {expected_market_date}; "
            f"cannot signal for {args.signal_date}"
        )
    fresh_security_ids = set(
        bars.loc[bars["trade_date"] == market_data_through, "security_id"]
    )

    with_signal = append_signal_rows(bars, args.signal_date)
    feature_frame = build_feature_frame(with_signal)
    model = fit_latest(feature_frame, config)
    ranked = rank_signal(model, feature_frame, args.signal_date)
    ranked = ranked[
        ranked["security_id"].isin(fresh_security_ids)
        & ~ranked["stock_code"].isin(suspended_codes)
    ].head(config.top_k).copy()
    ranked["model_rank"] = ranked["prediction_rank"].astype(int)
    ranked["prediction_rank"] = np.arange(1, len(ranked) + 1)
    candidates = [_candidate_dict(row, model) for _, row in ranked.iterrows()]
    news_status = "not_requested"
    if args.news:
        try:
            _attach_news(candidates, market_data_through, args.signal_date)
            news_status = "attached"
        except Exception as exc:
            for candidate in candidates:
                candidate["news_evidence"] = []
            news_status = f"unavailable: {type(exc).__name__}: {exc}"

    report = {
        "model": "latest-window weighted ridge MVP",
        "target": "buy at signal-day open, sell at next-trading-day close (A-share T+1)",
        "signal_cutoff": f"{args.signal_date.isoformat()} 09:15 Asia/Shanghai",
        "market_data_through": market_data_through.isoformat(),
        "universe": _universe_summary(snapshot),
        "model_training": {
            "start": model.train_start.isoformat(),
            "end": model.train_end.isoformat(),
            "rows": model.training_rows,
        },
        "candidates": candidates,
        "execution_filters": {
            "known_suspensions_excluded": sorted(suspended_codes),
            "stale_previous_day_bars_excluded": sorted(
                member.stock_code
                for member in snapshot.members
                if member.security_id not in fresh_security_ids
            ),
            "opening_limit_status": "unknown at the 09:15 signal cutoff",
        },
        "warning": "Research ranking only; probability is a linear estimate, not a guarantee.",
        "news_policy": "News is evidence-only and does not alter the MVP score.",
        "news_status": news_status,
    }
    if args.json:
        print(report_json(report, ensure_ascii=False, indent=2, default=_json_default))
    else:
        _print_prediction(report)
    return report


def _load_tushare_auction_cache(root: Path) -> pd.DataFrame:
    files = sorted((root / "raw").glob("*.csv.gz"))
    if not files:
        raise RuntimeError(f"no Tushare auction cache under {root / 'raw'}")
    return pd.concat((read_clean_csv(path) for path in files), ignore_index=True)


def _load_execution_entry_cache(root: Path) -> pd.DataFrame:
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
        raise RuntimeError(f"no 09:31 execution cache under {root / 'minute_entries'}")
    return pd.concat(
        (read_clean_csv(path, dtype={"stock_code": str}) for path in files),
        ignore_index=True,
    )


def _load_recent_bar_cache(root: Path, signal_date: date) -> pd.DataFrame:
    path = root / "recent_daily_bars.csv.gz"
    if not path.exists():
        return pd.DataFrame()
    frame = read_clean_csv(path, dtype={"stock_code": str})
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
    return frame[frame["trade_date"] < signal_date].copy()


def _store_recent_bar_cache(
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
    frame = frame.sort_values(["trade_date", "stock_code"])
    frame.to_csv(root / "recent_daily_bars.csv.gz", index=False, compression="gzip")
    DataStore().import_file(root / "recent_daily_bars.csv.gz", metadata_for(root / "recent_daily_bars.csv.gz"))


def _poll_tushare_auction(
    members,
    extra_codes: tuple[str, ...],
    signal_date: date,
    required_source_coverage: int,
    wait_seconds: int,
    poll_seconds: float,
) -> tuple[pd.DataFrame, datetime]:
    deadline = clock.monotonic() + max(wait_seconds, 0)
    last = pd.DataFrame()
    while True:
        last = fetch_tushare_auction_snapshot(members, signal_date, extra_codes)
        if not last.empty:
            price = pd.to_numeric(last.get("price"), errors="coerce")
            volume = pd.to_numeric(last.get("vol"), errors="coerce")
            valid = price.gt(0) & volume.gt(0)
            if int(valid.sum()) >= required_source_coverage:
                return last, datetime.now(SHANGHAI)
        if clock.monotonic() >= deadline:
            break
        clock.sleep(max(poll_seconds, 0.2))
    valid_rows = 0
    if not last.empty:
        valid_rows = int(
            (
                pd.to_numeric(last.get("price"), errors="coerce").gt(0)
                & pd.to_numeric(last.get("vol"), errors="coerce").gt(0)
            ).sum()
        )
    raise RuntimeError(
        f"Tushare auction snapshot has {valid_rows} valid members; "
        f"requires {required_source_coverage}"
    )


def _auction_candidate(row: pd.Series, model) -> dict:
    strategy_rank = row.get("strategy_rank")
    item = {
        "rank": int(
            strategy_rank if pd.notna(strategy_rank) else row["prediction_rank"]
        ),
        "model_rank": int(row["prediction_rank"]),
        "stock_code": row["stock_code"],
        "name": row.get("company_name"),
        "industry": row.get("industry_name"),
        "up_probability": float(row["up_probability"]),
        "predicted_return": float(row["predicted_return"]),
        "predicted_excess_return": (
            float(row["predicted_excess_return"])
            if pd.notna(row.get("predicted_excess_return")) else None
        ),
        "prediction_score": float(row["prediction_score"]),
        "ensemble_model_agreement": (
            float(row["ensemble_model_agreement"])
            if pd.notna(row.get("ensemble_model_agreement")) else None
        ),
        "market_regime": row.get("auction_market_regime"),
        "auction_price": float(row["auction_price"]),
        "auction_gap": float(row["auction_gap"]),
        "auction_volume": float(row["auction_volume"]),
        "auction_turnover_rate": float(row["auction_turnover_rate"]),
        "auction_volume_ratio": float(row["auction_volume_ratio"]),
        "return_drivers": model.explain_return(row),
    }
    return item


def _industry_capped_top(
    ranked: pd.DataFrame,
    top_k: int,
    max_per_industry: int = 2,
) -> pd.DataFrame:
    selected: list[int] = []
    counts: dict[str, int] = {}
    for index, row in ranked.sort_values("prediction_rank").iterrows():
        industry = str(row.get("industry_name") or "UNKNOWN")
        if industry != "UNKNOWN" and counts.get(industry, 0) >= max_per_industry:
            continue
        selected.append(index)
        counts[industry] = counts.get(industry, 0) + 1
        if len(selected) >= top_k:
            break
    out = ranked.loc[selected].copy()
    out["strategy_rank"] = np.arange(1, len(out) + 1)
    return out


def _strategy_health(backtest: dict, model_name: str | None = None) -> dict:
    if model_name is None:
        model_name = (
            "optimized_ensemble"
            if "optimized_ensemble" in backtest["models"]
            else "auction"
        )
    metrics = backtest["models"][model_name]
    after_cost = metrics.get(
        "portfolio_daily_mean_after_cost",
        metrics["selected_mean_after_cost"]["20"],
    )
    thresholds = {
        "selected_mean_after_20bps_gt": 0.0,
        "mean_return_spread_gt": 0.0,
        "selected_daily_t_stat_gte": 1.0,
        "active_day_rate_gte": 0.20,
    }
    checks = {
        "selected_mean_after_20bps": after_cost > 0,
        "mean_return_spread": metrics["mean_return_spread"] > 0,
        "selected_daily_t_stat": metrics["selected_daily_t_stat"] >= 1.0,
        "active_day_rate": metrics.get("active_day_rate", 1.0) >= 0.20,
    }
    return {
        "model": model_name,
        "passed": all(checks.values()),
        "checks": checks,
        "thresholds": thresholds,
        "observed": {
            "selected_mean_return": metrics["selected_mean_return"],
            "selected_mean_after_20bps": after_cost,
            "mean_return_spread": metrics["mean_return_spread"],
            "selected_daily_t_stat": metrics["selected_daily_t_stat"],
            "selected_daily_win_rate": metrics["selected_daily_win_rate"],
            "mean_rank_ic": metrics["mean_rank_ic"],
            "active_day_rate": metrics.get("active_day_rate", 1.0),
            "average_selected_count": metrics.get("average_selected_count"),
        },
        "validation_start": backtest["validation_start"],
        "validation_end": backtest["validation_end"],
        "policy": "Fail closed for automatic execution; ranking remains research output.",
    }


def _rank_live_auction_candidates(
    feature_frame: pd.DataFrame,
    signal_date: date,
    config: AuctionBacktestConfig,
    strategy_health: dict,
    return_ensemble,
    excess_ensemble,
) -> tuple[pd.DataFrame, str, Any]:
    if strategy_health["passed"]:
        ranked = rank_auction_strategy(
            return_ensemble,
            excess_ensemble,
            feature_frame,
            signal_date,
            minimum_coverage=config.minimum_cross_section,
            top_k=config.top_k,
        )
        selected = ranked[ranked["strategy_selected"]].sort_values("strategy_rank")
        return selected, "optimized_ensemble", return_ensemble

    fallback_config = replace(config, training_window_days=60)
    fallback_model = fit_latest_auction(
        feature_frame,
        fallback_config,
        target_column="target_0931_vwap_to_next_close",
        feature_names=CONTEXT_AUCTION_FEATURE_COLUMNS,
        as_of_date=signal_date,
    )
    ranked = rank_auction_signal(
        fallback_model,
        feature_frame,
        signal_date,
        minimum_coverage=config.minimum_cross_section,
    )
    selected = _industry_capped_top(ranked, config.top_k)
    return selected, "context_research_fallback", fallback_model


def _print_auction_prediction(report: dict) -> None:
    print("CSI 300 Tushare opening-auction prediction")
    print(
        f"Available at {report['signal_available_at']}; "
        f"source coverage {report['auction_coverage']['valid']}/"
        f"{report['auction_coverage']['expected']}"
    )
    health = report["strategy_health"]
    print(
        f"Strategy health: {'PASS' if health['passed'] else 'FAIL'}; "
        f"recent return after 20 bps="
        f"{health['observed']['selected_mean_after_20bps']:.3%}; "
        f"daily t={health['observed']['selected_daily_t_stat']:.2f}"
    )
    print(
        f"Candidate mode: {report['candidate_mode']}; "
        f"actionable={'yes' if report['actionable'] else 'no'}"
    )
    for item in report["candidates"]:
        print(
            f"  {item['rank']:>2}. {item['stock_code']} {item['name']}  "
            f"p(up)={item['up_probability']:.1%}  "
            f"expected={item['predicted_return']:.2%}  "
            + (
                f"excess={item['predicted_excess_return']:.2%}  "
                if item["predicted_excess_return"] is not None else ""
            )
            + f"auction={item['auction_price']:.2f} ({item['auction_gap']:+.2%})"
        )
    if not report["candidates"]:
        print("  No candidate passed the dynamic confidence and cost thresholds.")


def run_auction_prediction(args: argparse.Namespace) -> dict:
    root = Path(args.auction_history_root)
    snapshots = read_clean_csv(root / "universe_snapshots.csv", dtype={"stock_code": str})
    snapshots["stock_code"] = snapshots["stock_code"].str[:6].str.zfill(6)
    historical_auction = _load_tushare_auction_cache(root)
    minute_entries = _load_execution_entry_cache(root)

    db = SessionLocal()
    try:
        current = load_current_hs300(db, args.signal_date)
        union_codes = set(snapshots["stock_code"]) | {
            member.stock_code for member in current.members
        }
        members = load_members_by_codes(db, union_codes)
        bars = load_market_bars(
            db, members, args.start, args.signal_date - timedelta(days=1)
        )
    finally:
        db.close()

    expected_market_date = latest_trading_day_before(args.signal_date)
    cached_bars = _load_recent_bar_cache(root, args.signal_date)
    bars = overlay_recent_bars(bars, cached_bars, members)
    local_through = _latest_complete_date(bars, args.minimum_cross_section)
    refresh_start = _recent_refresh_start(
        args.start,
        args.signal_date,
        args.refresh_days,
        local_through,
    )
    recent = fetch_tushare_recent_bars(
        members, refresh_start, args.signal_date - timedelta(days=1)
    )
    _store_recent_bar_cache(root, cached_bars, recent)
    bars = overlay_recent_bars(bars, recent, members)
    market_data_through = _latest_complete_date(bars, args.minimum_cross_section)
    if market_data_through != expected_market_date:
        raise RuntimeError(
            f"market data ends at {market_data_through}, expected {expected_market_date}"
        )

    current_members = current.members
    source_member_count = len(current_members) + len(current.missing_codes)
    required_source_coverage = max(
        args.minimum_cross_section,
        math.ceil(source_member_count * args.minimum_auction_coverage),
    )
    live_auction, available_at = _poll_tushare_auction(
        current_members,
        current.missing_codes,
        args.signal_date,
        required_source_coverage,
        args.wait_seconds,
        args.poll_seconds,
    )
    live_valid = (
        pd.to_numeric(live_auction["price"], errors="coerce").gt(0)
        & pd.to_numeric(live_auction["vol"], errors="coerce").gt(0)
    )

    live_snapshot_rows = pd.DataFrame([
        {
            "snapshot_date": args.signal_date.strftime("%Y%m%d"),
            "stock_code": member.stock_code,
            "ts_code": member.symbol,
            "weight": np.nan,
        }
        for member in current_members
    ] + [
        {
            "snapshot_date": args.signal_date.strftime("%Y%m%d"),
            "stock_code": stock_code,
            "ts_code": (
                f"{stock_code}.SH" if stock_code.startswith("6")
                else f"{stock_code}.SZ"
            ),
            "weight": np.nan,
        }
        for stock_code in current.missing_codes
    ])
    snapshots = pd.concat([snapshots, live_snapshot_rows], ignore_index=True)
    auction = pd.concat([historical_auction, live_auction], ignore_index=True)
    with_signal = append_signal_rows(bars, args.signal_date)
    base = build_feature_frame(with_signal)
    feature_frame, diagnostics = prepare_tushare_auction_feature_frame(
        base, auction, snapshots
    )
    feature_frame = attach_execution_entries(feature_frame, minute_entries)
    config = AuctionBacktestConfig(
        training_window_days=args.training_days,
        half_life_days=args.half_life_days,
        minimum_cross_section=args.minimum_cross_section,
        top_k=args.top,
        refit_frequency="daily",
    )
    target = "target_0931_vwap_to_next_close"
    return_ensemble = fit_latest_auction_ensemble(
        feature_frame,
        config,
        target_column=target,
        feature_names=CONTEXT_AUCTION_FEATURE_COLUMNS,
        windows=DEFAULT_ENSEMBLE_WINDOWS,
        as_of_date=args.signal_date,
    )
    excess_ensemble = fit_latest_auction_ensemble(
        feature_frame,
        config,
        target_column=f"{target}_universe_excess",
        feature_names=CONTEXT_AUCTION_FEATURE_COLUMNS,
        windows=DEFAULT_ENSEMBLE_WINDOWS,
        probability_target_column=target,
        as_of_date=args.signal_date,
    )
    suspended = load_signal_day_suspensions(current_members, args.signal_date)
    current_ids = {member.security_id for member in current_members}
    strategy_frame = feature_frame.copy()
    excluded = (
        strategy_frame["trade_date"].eq(args.signal_date)
        & (
            ~strategy_frame["security_id"].isin(current_ids)
            | strategy_frame["stock_code"].isin(suspended)
        )
    )
    strategy_frame.loc[excluded, "auction_tradable"] = False
    health_config = AuctionBacktestConfig(
        training_window_days=args.training_days,
        validation_days=args.health_validation_days,
        half_life_days=args.half_life_days,
        minimum_cross_section=args.minimum_cross_section,
        top_k=args.top,
        refit_frequency="daily",
    )
    strategy_health = _strategy_health(
        evaluate_optimized_execution_backtest(feature_frame, health_config),
        model_name="optimized_ensemble",
    )
    selected, candidate_mode, candidate_model = _rank_live_auction_candidates(
        strategy_frame,
        args.signal_date,
        config,
        strategy_health,
        return_ensemble,
        excess_ensemble,
    )
    candidates = [
        _auction_candidate(row, candidate_model) for _, row in selected.iterrows()
    ]
    if candidate_mode == "optimized_ensemble":
        model_training = {
            "windows": list(return_ensemble.windows),
            "start": min(
                model.train_start for model in return_ensemble.models
            ).isoformat(),
            "end": max(
                model.train_end for model in return_ensemble.models
            ).isoformat(),
            "rows_by_window": {
                str(window): model.training_rows
                for window, model in zip(
                    return_ensemble.windows, return_ensemble.models
                )
            },
            "absolute_target": return_ensemble.target_column,
            "excess_target": excess_ensemble.target_column,
        }
    else:
        model_training = {
            "windows": [60],
            "start": candidate_model.train_start.isoformat(),
            "end": candidate_model.train_end.isoformat(),
            "rows_by_window": {"60": candidate_model.training_rows},
            "absolute_target": candidate_model.target_column,
            "excess_target": None,
        }
    report = {
        **input_evidence(),
        "model": "Tushare contextual opening-auction ridge",
        "production_policy_version": PRODUCTION_POLICY_VERSION,
        "policy_frozen_at": "2026-08-29",
        "point_in_time_rule": "target_next_date < signal_date",
        "model_features": "auction flow plus CSI 300 market and industry context",
        "target": "buy during 09:30-09:31 at minute VWAP, sell next trading-day close",
        "signal_date": args.signal_date.isoformat(),
        "signal_available_at": available_at.isoformat(),
        "market_data_through": market_data_through.isoformat(),
        "auction_source": "tushare.stk_auction",
        "auction_coverage": {
            "valid": int(live_valid.sum()),
            "expected": source_member_count,
            "required": required_source_coverage,
            "model_mapped": len(current_members),
        },
        "universe": {
            **_universe_summary(current),
            "history_policy": "point-in-time monthly CSI 300 snapshots",
            "warning": "Unmapped local securities are excluded and listed explicitly.",
        },
        "candidate_mode": candidate_mode,
        "model_training": model_training,
        "feature_diagnostics": diagnostics,
        "strategy_health": strategy_health,
        "actionable": strategy_health["passed"] and bool(candidates),
        "candidates": candidates,
        "selection_policy": {
            "maximum_top_k": args.top,
            "dynamic_top_k": candidate_mode == "optimized_ensemble",
            "minimum_model_agreement": 0.5,
            "maximum_per_industry": 2,
            "base_expected_return_floor": "20 bps",
            "risk_off_policy": "require 55% up probability and 40 bps expected return",
            "research_fallback": (
                "60-day contextual model, fixed Top K, maximum two per industry"
            ),
        },
        "execution_reference": "first continuous-auction minute VWAP (09:30-09:31)",
        "known_suspensions_excluded": sorted(suspended),
        "news_policy": "excluded from the production score until point-in-time history exists",
        "warning": "Research ranking only; no automatic order and no return guarantee.",
    }
    if args.json:
        print(report_json(report, ensure_ascii=False, indent=2, default=_json_default))
    else:
        _print_auction_prediction(report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A-share short-horizon research MVP")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--start", type=_parse_date, default=date(2020, 1, 1))
    common.add_argument("--training-days", type=int, default=756)
    common.add_argument("--minimum-cross-section", type=int, default=200)
    common.add_argument("--top", type=int, default=5)
    common.add_argument("--json", action="store_true")

    backtest = subparsers.add_parser("backtest", parents=[common])
    backtest.add_argument("--end", type=_parse_date, default=None)
    backtest.add_argument("--validation-days", type=int, default=252)
    backtest.set_defaults(handler=run_backtest)

    predict = subparsers.add_parser("predict", parents=[common])
    predict.add_argument("--signal-date", type=_parse_date, required=True)
    predict.add_argument("--refresh-days", type=int, default=120)
    predict.add_argument("--news", action="store_true")
    predict.set_defaults(handler=run_prediction)

    auction = subparsers.add_parser("predict-auction", parents=[common])
    auction.add_argument("--signal-date", type=_parse_date, required=True)
    auction.add_argument("--auction-history-root", default=DEFAULT_AUCTION_HISTORY_ROOT)
    auction.add_argument("--refresh-days", type=int, default=30)
    auction.add_argument("--half-life-days", type=int, default=20)
    auction.add_argument("--minimum-auction-coverage", type=float, default=0.95)
    auction.add_argument("--wait-seconds", type=int, default=180)
    auction.add_argument("--poll-seconds", type=float, default=2.0)
    auction.add_argument("--health-validation-days", type=int, default=40)
    auction.set_defaults(
        handler=run_auction_prediction,
        training_days=60,
        minimum_cross_section=240,
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
