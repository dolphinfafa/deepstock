#!/usr/bin/env python3
"""Run all preregistered ETF afternoon-momentum paths/costs, or report data blockers."""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from deepstock.data.store import read_clean_csv

from deepstock.strategies.cn.tail_momentum import MARKET, PATHS, STRATEGY_ID, VERSION, cost_cases, prepare_sessions, run_tail_momentum, summarize, walk_forward
from deepstock.research_control import research_pause


ROOT = Path(__file__).resolve().parents[2]


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def verify_input(root: Path) -> tuple[pd.DataFrame, pd.DatetimeIndex, pd.DataFrame, dict]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    contract = {"data_kind": "market", "interval_minutes": 1, "timestamp_convention": "end", "timezone": "Asia/Shanghai", "adjustment": "none", "volume_unit": "shares", "amount_unit": "CNY"}
    if any(manifest.get(key) != value for key, value in contract.items()):
        raise ValueError("Minute provenance/units differ from the fixed data contract")
    if not manifest.get("provider") or "synthetic" in str(manifest["provider"]).lower():
        raise ValueError("A real provider is required; synthetic tests are not research results")
    for name in ("minutes.csv.gz", "calendar.csv"):
        actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
        if manifest.get("sha256", {}).get(name) != actual:
            raise ValueError(f"Input checksum mismatch: {name}")
    bars = read_clean_csv(root / "minutes.csv.gz")
    calendar = pd.DatetimeIndex(pd.to_datetime(read_clean_csv(root / "calendar.csv")["date"]))
    dividends = pd.DataFrame(columns=["ex_date", "pay_date", "cash_per_share"])
    if manifest.get("corporate_action_status") == "cash_dividends_audited_no_splits":
        path = root / "dividends.csv"
        if manifest.get("sha256", {}).get(path.name) != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError("Dividend file checksum mismatch")
        dividends = read_clean_csv(path)
    elif manifest.get("corporate_action_status") != "price_only_unverified":
        raise ValueError("Corporate actions must be audited or explicitly price-only/unverified")
    return bars, calendar, dividends, manifest


def format_report(report: dict) -> str:
    common = (
        "# A股ETF尾盘动量 T+1 · 独立研究\n\n"
        "主信号：13:30—14:00 收益为正；信号完成后延迟一分钟买入，次日首分钟卖出。\n\n"
        "这是对笔记的保守成交适配，不是保证14:00收盘价买入、次日精确开盘价卖出。\n\n"
        "资金：10万元、100份整手、不杠杆；成交限于分钟成交量1%；T+1持仓不能提前出售。\n\n"
        "主成本：每侧佣金2.5bp（最低5元）+滑点2.5bp，往返名义10bp；另展示6/20bp与理想化视频1bp。\n\n"
    )
    if report["status"] == "blocked_data":
        return common + (
            "## 数据阻塞：尚无真实行情回测结果\n\n" + report["reason"] + "\n\n"
            "没有使用视频收益、日线、粗周期K线或合成数据充当市场回测。\n\n"
            "## 下一步\n\n"
            "提供已授权的510300.SH一分钟原始OHLCV/成交额、交易日历及分红/份额变更记录，"
            "或明确开通ETF分钟权限后运行下载与回测命令。不得自动购买权限或占用集合竞价股票分钟配额。\n"
        )
    text = common + f"实际数据：{report['data_start']} — {report['data_end']}，{report['sessions']} 个交易日。\n\n"
    text += f"证据阶段：{report['evidence_stage']}；公司行动口径：{report['corporate_action_status']}。\n\n"
    if report['sessions'] < 756:
        text += "**仅为短样本流程验证：不足756个交易日，固定Walk-Forward窗口为0；不能据此判断策略有效。**\n\n"
    text += "|路径|成本模型|净收益|匹配窗口基准|差值（百分点）|Sharpe|最大回撤|完成交易|\n|---|---|---:|---:|---:|---:|---:|---:|\n"
    for case in report["cases"]:
        s, b = case["summary"], case["matched_benchmark"]
        sharpe = f"{s['sharpe_ratio']:.2f}" if s["sharpe_ratio"] is not None else "—"
        text += f"|{case['path']}|{case['cost_case']}|{s['total_return']:.2%}|{b['total_return']:.2%}|{(s['total_return'] - b['total_return']) * 100:.2f}|{sharpe}|{s['maximum_drawdown']:.2%}|{s['completed_trades']}|\n"
    text += "\n## 成交与成本审计（主成本）\n\n|路径|佣金（元）|滑点（元）|换手倍数|阻塞入场|延迟退出日|未平仓份额|\n|---|---:|---:|---:|---:|---:|---:|\n"
    for case in report["cases"]:
        if case["cost_case"] == "primary_10bp":
            s = case["summary"]
            text += f"|{case['path']}|{s['commission_cny']:.2f}|{s['slippage_cny']:.2f}|{s['total_turnover']:.2f}|{s['blocked_entries']}|{s['delayed_exit_sessions']}|{s['unclosed_shares']}|\n"
    text += "\n## 连续买入持有对照\n\n此对照在样本首日09:31分钟买入并持有，不自动平仓；仍受1%参与率和100份整手限制，实际投入可能小于10万元。\n\n|成本模型|连续持有净收益|最大回撤|平均收盘暴露|\n|---|---:|---:|---:|\n"
    for case in report["cases"]:
        if case["path"] == PATHS[0]:
            b = case["full_buy_hold"]
            text += f"|{case['cost_case']}|{b['total_return']:.2%}|{b['maximum_drawdown']:.2%}|{b['average_exposure']:.2%}|\n"
    text += (
        "\n理想化1bp不含最低佣金/滑点，不能作为实际券商费用。"
        "上表没有挑选最好候选；年化与Sharpe仅为统计缩放，短样本不代表可持续收益。\n\n"
        "分红未核验时仅为价格口径诊断，不能声称总收益或通过策略准入。"
        "已公开的视频区间不视为独立OOS；没有自动交易授权。\n\n"
        "## 下一步\n\n检查完整长历史、多行情阶段、固定滚动窗口、真实费用与成交延迟；"
        "短样本或缺失公司行动时先补数据，不利用现有结果反选参数。\n"
    )
    return text


def run_research(input_root: Path, output: Path) -> dict:
    now = datetime.now(timezone.utc)
    cases = cost_cases()
    config = {"version": VERSION, "paths": list(PATHS), "cost_cases": {key: asdict(value) for key, value in cases.items()}}
    report = {"strategy_id": STRATEGY_ID, "market": MARKET, "version": VERSION, "as_of_date": now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat(),
              "recorded_at_utc": now.isoformat(), "config": config,
              "config_hash": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
              "execution_status": "research_only_no_orders", "oos_parameter_selection_prohibited": True}
    try:
        bars, calendar, dividends, manifest = verify_input(input_root)
        sessions = prepare_sessions(bars, calendar)
    except (FileNotFoundError, ValueError, KeyError) as error:
        access_path = input_root / "access-report.json"
        access = json.loads(access_path.read_text(encoding="utf-8")) if access_path.exists() else {}
        reason = (access.get("reason") if isinstance(error, FileNotFoundError) else None) or str(error)
        report.update(status="blocked_data", reason=reason, access=access, cases=[])
        output.mkdir(parents=True, exist_ok=True)
        write_json(output / "research.json", report)
        (output / "report.md").write_text(format_report(report), encoding="utf-8")
        return report
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for cost_name, cost in cases.items():
        buy_hold = run_tail_momentum(sessions, "buy_hold", cost, dividends=dividends)
        for path in PATHS:
            strategy = run_tail_momentum(sessions, path, cost, dividends=dividends)
            benchmark = run_tail_momentum(sessions, path, cost, always_enter=True, dividends=dividends)
            prefix = f"{path}-{cost_name}"
            strategy.daily.to_csv(output / f"{prefix}-daily.csv", index_label="date")
            strategy.trades.to_csv(output / f"{prefix}-trades.csv", index=False)
            benchmark.daily.to_csv(output / f"{prefix}-benchmark.csv", index_label="date")
            windows = walk_forward(strategy.daily)
            fixed = {}
            for name, start, end in (("pre_video_diagnostic", "2021-01-04", "2025-08-15"),
                                     ("published_video_sample", "2025-08-18", "2026-08-14"),
                                     ("post_video_diagnostic", "2026-08-20", "2026-10-05")):
                segment = strategy.daily.loc[start:end]
                if len(segment):
                    fixed[name] = {"strategy": summarize(segment), "matched_benchmark": summarize(benchmark.daily.loc[segment.index]), "genuine_prospective": False}
            records.append({"path": path, "cost_case": cost_name, "summary": strategy.summary,
                            "matched_benchmark": benchmark.summary, "full_buy_hold": buy_hold.summary,
                            "excess_percentage_points": (strategy.summary["total_return"] - benchmark.summary["total_return"]) * 100,
                            "walk_forward": windows, "fixed_periods": fixed})
    report.update(status="complete", data_start=sessions.index[0].date().isoformat(), data_end=sessions.index[-1].date().isoformat(),
                  sessions=len(sessions), evidence_stage="long_history_diagnostic" if len(sessions) >= 756 else "short_sample_pilot_not_validated",
                  corporate_action_status=manifest["corporate_action_status"], input_manifest=manifest, cases=records)
    write_json(output / "research.json", report)
    (output / "report.md").write_text(format_report(report), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT / "artifacts/research/cn-etf-tail-momentum/data")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/research/cn-etf-tail-momentum/latest")
    args = parser.parse_args()
    paused = research_pause(STRATEGY_ID, ROOT)
    if paused:
        print(json.dumps(paused, ensure_ascii=False))
        return
    report = run_research(args.input_dir, args.output_dir)
    print(json.dumps({key: report.get(key) for key in ("strategy_id", "status", "reason", "data_start", "data_end", "sessions", "evidence_stage")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
