"""Fixed auction-state study: local replay and isolated forward reports."""
from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta
import json
import os
from pathlib import Path
import subprocess

import pandas as pd

from deepstock.data.store import ROOT, digest, input_evidence, write_json
from deepstock.strategies.cn.auction import study
from deepstock.strategies.cn.auction.auction import (
    _historical_membership, attach_execution_entries, prepare_tushare_auction_feature_frame,
)
from deepstock.strategies.cn.auction.cli import (
    _load_execution_entry_cache, _load_tushare_auction_cache,
)
from deepstock.strategies.cn.auction.data import overlay_recent_bars
from deepstock.strategies.cn.auction.features import build_feature_frame
from deepstock.strategies.cn.auction.inputs import read_auction_csv
from deepstock.strategies.cn.auction.repository import load_bars, load_members
from deepstock.strategies.cn.auction.forward import (
    apply_entry_fills, apply_exit_prices, expire_unfilled_entries,
)

DEFAULT_ROOT = ROOT / "artifacts" / study.POLICY


def backtest(root: Path, history: Path) -> dict:
    active = study.activation(root)
    if not active:
        raise ValueError("Preregister the approved research note with init before backtesting")
    previous_offline = os.environ.get("DEEPSTOCK_AUCTION_OFFLINE")
    os.environ["DEEPSTOCK_AUCTION_OFFLINE"] = "true"
    try:
        return _backtest(root, history, active)
    finally:
        if previous_offline is None:
            os.environ.pop("DEEPSTOCK_AUCTION_OFFLINE", None)
        else:
            os.environ["DEEPSTOCK_AUCTION_OFFLINE"] = previous_offline


def _backtest(root: Path, history: Path, active: dict) -> dict:
    spec = study.specification()
    start, end = (date.fromisoformat(spec[k]) for k in ("historical_start", "historical_end"))
    now = datetime.now(study.SHANGHAI)
    run_root = root / "historical" / now.strftime("%Y%m%dT%H%M%S%f")
    run_root.mkdir(parents=True)
    write_json(run_root / "activation.json", {**active, "enabled": False})
    code_files = [*sorted((ROOT / "src/deepstock/strategies/cn/auction").glob("*.py")),
                  Path(__file__), study.CONFIG_PATH]
    code_state = {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "dirty_state": subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True),
        "files": {str(p.relative_to(ROOT)): digest(p) for p in code_files},
    }
    write_json(run_root / "starting_code.json", code_state)
    manifest_hashes = {str(history / k): digest(history / k) for k in ("minute_manifest.json", "tushare_manifest.json")}
    snapshots = read_auction_csv(history / "universe_snapshots.csv", dtype={"stock_code": str})
    snapshots["stock_code"] = snapshots["stock_code"].str[:6].str.zfill(6)
    members = load_members(set(snapshots["stock_code"]))
    bars = load_bars(members, date(2024, 1, 1), end + timedelta(days=10))
    cached = read_auction_csv(history / "recent_daily_bars.csv.gz", dtype={"stock_code": str})
    cached["trade_date"] = pd.to_datetime(cached["trade_date"]).dt.date
    bars = overlay_recent_bars(bars, cached[cached["trade_date"] <= end + timedelta(days=10)], members)
    auction = _load_tushare_auction_cache(history)
    entries = _load_execution_entry_cache(history)
    entries["trade_date"] = pd.to_datetime(entries["trade_date"]).dt.date
    base = build_feature_frame(bars)
    frame, diagnostics = prepare_tushare_auction_feature_frame(base, auction, snapshots)
    frame = attach_execution_entries(frame, entries)
    # Pin every resolved upstream before inspecting any strategy outcomes.
    evidence = input_evidence()
    if any(digest(Path(p)) != h for p, h in manifest_hashes.items()):
        raise ValueError("History manifests changed during input capture")
    write_json(run_root / "frozen_inputs.json", {
        **evidence, "manifest_hashes": manifest_hashes, "feature_diagnostics": diagnostics,
        "historical_start": str(start), "historical_end": str(end),
        "evidence_label": "historical_development_diagnostic",
    })
    dates = sorted(set(bars["trade_date"]))
    requested = [d for d in dates if start <= d <= end]
    membership = _historical_membership(pd.Series(requested), snapshots)
    config = study.model_config()
    db = study.open_ledger(run_root)
    for day in requested:
        previous, following = dates[dates.index(day) - 1], dates[dates.index(day) + 1]
        current = frame[frame["trade_date"].eq(day)]
        expected = len(membership[day])
        observed = current["stock_code"].nunique()
        minute_codes = set(entries.loc[entries["trade_date"].eq(day), "stock_code"])
        minute_count = len(minute_codes & membership[day])
        minute_ready = bool(expected and minute_count / expected >= spec["minimum_minute_coverage"])
        coverage = {"expected": expected, "auction_observed": observed, "minute_observed": minute_count}
        if not expected or observed < max(config.minimum_cross_section, expected * spec["minimum_auction_coverage"]):
            study._attempt(run_root, day, "input_blocked", "auction_coverage", now)
            continue
        try:
            scored, sample_diagnostics = study.score(frame, day, config, include_full=True)
        except (ValueError, RuntimeError) as error:
            study._attempt(run_root, day, "input_blocked", str(error), now)
            continue
        study.record_decisions(
            db, scored, day, previous, following,
            datetime.combine(day, time(9, 27), study.SHANGHAI),
            {**evidence, "coverage": coverage, "diagnostics": sample_diagnostics,
             "entry_input_blocked": not minute_ready, "historical_time_is_assumed": True},
            mode="historical",
        )
        day_entries = entries[entries["trade_date"].eq(day)]
        apply_entry_fills(db, day, day_entries, now)
    # Use retained daily prices; suspensions/limit-down keep positions open.
    ordered = bars.sort_values(["stock_code", "trade_date"]).copy()
    ordered["pre_close"] = ordered.groupby("stock_code")["close"].shift(1)
    ordered["ts_code"] = ordered["stock_code"].map(lambda c: f"{c}.SH" if c.startswith("6") else f"{c}.SZ")
    markets = {d: rows for d, rows in ordered.groupby("trade_date") if start < d <= end + timedelta(days=10)}
    apply_exit_prices(db, markets, end + timedelta(days=10))
    expire_unfilled_entries(db, end + timedelta(days=10))
    db.close()
    report = study.write_report(run_root, mode="historical", as_of=end)
    report["requested_days"] = len(requested)
    report["missing_signal_dates"] = sorted(set(map(str, requested)) - {d["signal_date"] for d in report["daily"]})
    report["input_manifest"] = "frozen_inputs.json"
    report["historical_window_complete"] = not report["missing_signal_dates"] and all(
        d["evaluable"] for d in report["daily"]
    )
    write_json(run_root / "reports/historical.json", report)
    write_json(root / "latest_historical_run.json", {"root": str(run_root), "created_at": now.isoformat()})
    return {"status": "completed", "run_root": str(run_root), "report": report}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init")
    initialize.add_argument("--note-id", required=True)
    replay = commands.add_parser("backtest")
    replay.add_argument("--history-root", type=Path, default=ROOT / "artifacts/auction_history_tushare")
    report = commands.add_parser("report")
    report.add_argument("--as-of", type=date.fromisoformat)
    commands.add_parser("disable")
    commands.add_parser("enable")
    args = parser.parse_args()
    if args.command == "init":
        result = study.initialize(args.root, note_id=args.note_id)
    elif args.command == "backtest":
        result = backtest(args.root, args.history_root)
    elif args.command == "report":
        result = study.write_report(args.root, as_of=args.as_of)
    else:
        result = study.activation(args.root)
        if not result:
            raise ValueError("Study not initialized")
        result["enabled"] = args.command == "enable"
        write_json(args.root / "activation.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
