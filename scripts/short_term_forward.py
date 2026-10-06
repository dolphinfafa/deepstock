# -*- coding: utf-8 -*-
"""CLI for announcement capture and CSI 300 forward paper trading."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path

from deepstock.strategies.cn.auction.forward import (
    DEFAULT_FORWARD_ROOT,
    collect_announcements,
    create_late_auction_counterfactual,
    fill_entries,
    generate_signal,
    open_store,
    settle_counterfactual_positions,
    settle_positions,
    write_forward_report,
)


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _print(report: dict) -> None:
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


def run_init(args: argparse.Namespace) -> dict:
    connection = open_store(Path(args.root))
    connection.close()
    return {"status": "initialized", "root": args.root}


def run_collect(args: argparse.Namespace) -> dict:
    target = args.date or date.today()
    dates = [target - timedelta(days=offset) for offset in range(args.lookback_days + 1)]
    return collect_announcements(
        Path(args.root), dates, observation_mode=args.mode
    )


def run_signal(args: argparse.Namespace) -> dict:
    return generate_signal(
        Path(args.root),
        args.signal_date,
        args.auction_history_root,
        args.wait_seconds,
    )


def run_fill(args: argparse.Namespace) -> dict:
    return fill_entries(
        Path(args.root),
        args.trade_date,
        wait_seconds=args.wait_seconds,
        poll_seconds=args.poll_seconds,
    )


def run_settle(args: argparse.Namespace) -> dict:
    return settle_positions(Path(args.root), args.as_of_date)


def run_counterfactual(args: argparse.Namespace) -> dict:
    return create_late_auction_counterfactual(
        Path(args.root), args.signal_date, args.auction_history_root
    )


def run_counterfactual_settle(args: argparse.Namespace) -> dict:
    return settle_counterfactual_positions(Path(args.root), args.as_of_date)


def run_report(args: argparse.Namespace) -> dict:
    return write_forward_report(Path(args.root), args.as_of_date)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CSI 300 announcement capture and forward paper trading"
    )
    parser.add_argument("--root", default=str(DEFAULT_FORWARD_ROOT))
    subparsers = parser.add_subparsers(dest="command", required=True)

    initialize = subparsers.add_parser("init")
    initialize.set_defaults(handler=run_init)

    collect = subparsers.add_parser("collect")
    collect.add_argument("--date", type=_parse_date, default=None)
    collect.add_argument("--lookback-days", type=int, default=0)
    collect.add_argument("--mode", choices=("live", "backfill"), default="live")
    collect.set_defaults(handler=run_collect)

    signal = subparsers.add_parser("signal")
    signal.add_argument("--signal-date", type=_parse_date, default=None)
    signal.add_argument(
        "--auction-history-root", default="artifacts/auction_history_tushare"
    )
    signal.add_argument("--wait-seconds", type=int, default=180)
    signal.set_defaults(handler=run_signal)

    fill = subparsers.add_parser("fill")
    fill.add_argument("--trade-date", type=_parse_date, default=None)
    fill.add_argument("--wait-seconds", type=float, default=90)
    fill.add_argument("--poll-seconds", type=float, default=3)
    fill.set_defaults(handler=run_fill)

    settle = subparsers.add_parser("settle")
    settle.add_argument("--as-of-date", type=_parse_date, default=None)
    settle.set_defaults(handler=run_settle)

    counterfactual = subparsers.add_parser("counterfactual-auction")
    counterfactual.add_argument("--signal-date", type=_parse_date, default=None)
    counterfactual.add_argument(
        "--auction-history-root", default="artifacts/auction_history_tushare"
    )
    counterfactual.set_defaults(handler=run_counterfactual)

    counterfactual_settle = subparsers.add_parser("settle-counterfactual")
    counterfactual_settle.add_argument(
        "--as-of-date", type=_parse_date, default=None
    )
    counterfactual_settle.set_defaults(handler=run_counterfactual_settle)

    report = subparsers.add_parser("report")
    report.add_argument("--as-of-date", type=_parse_date, default=None)
    report.set_defaults(handler=run_report)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    _print(args.handler(args))


if __name__ == "__main__":
    main()
