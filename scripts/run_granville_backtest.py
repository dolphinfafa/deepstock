"""Fixed independent Granville comparisons; no optimizer, broker or winner selection."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import uuid

import exchange_calendars as xcals
import numpy as np
import pandas as pd

from deepstock.data.store import ROOT, digest, input_evidence, read_clean_csv, write_json
from deepstock.strategies.both.granville import VARIANTS, run_granville, summarize, walk_forward
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.rerun_clean_research import capture_code_provenance


def period_metrics(daily, split):
    return {name: summarize(part) for name, part in [("full", daily), ("pre_split", daily.loc[daily.index < pd.Timestamp(split)]),
            ("retrospective_holdout", daily.loc[split:])] if not part.empty}


def render_report(publication):
    cfg = publication["config"]
    lines = ["# 葛兰威尔均线波段：美股 / A股独立验证", "", publication["summary"], "",
             "本研究是笔记启发的固定量化代理，不是作者未公开的三均线/关键K系统。",
             "预先指定主展示 **trend_pullback**，全部候选与成本情景保留，不依据新结果挑选赢家。",
             "所有历史早于登记；2021-08-23后仅为回溯诊断留出，不是真正未见的前瞻OOS。未授权Paper/实盘。", "",
             "## 固定规则与成本", "",
             "MA20/60/200，ATR14，收盘确认后下一交易日开盘交易；初始目标暴露80%，随行情漂移，不是每日硬上限。",
             "普通退出最少持有3日；8%收盘风险止损和第7日收盘时间退出不受最低持有约束。退出后冷却3日。",
             "现金利息为0；没有期末强制清仓。未平仓、待执行退出和应收分红保留在净值与账本中。", "",
             "US：Norgate TOTALRETURN OHLC分析性小数单位，包含经济总收益，但不等同历史真实报价、股数或IBKR执行回放。",
             "单边佣金2.5bp（最低USD1）+滑点2.5bp；压力情景滑点10bp。无原始成交量，不声称验证开盘容量。",
             "CN：510300原始价成交、复权因子仅算指标；现金分红除息计应收、派息日收盘后可用。100股整手、T+1、ETF印花税0。",
             "单边佣金3bp（最低CNY5）+滑点5bp；压力滑点10bp，上一交易日成交量1%容量，涨跌停开盘保守延迟。",
             "A股买入持有基准领取分红但不自动再投资；与美股总收益单位口径存在差异，不比较两市场绝对净值。", ""]
    for market, result in publication["market_results"].items():
        lines += [f"## {market} · {result['symbol']}", "", f"数据与评估区间：{result['metrics']['start']} — {result['metrics']['end']}；{result['metrics']['trading_days']}交易日。", "",
                  "|候选 / 成本|累计收益|年化|Sharpe|最大回撤|年化换手|费用金额|平均暴露|买入次数|",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for case in result["cases"]:
            m = case["periods"]["full"]
            sharpe = "—" if m["sharpe_ratio"] is None else f"{m['sharpe_ratio']:.2f}"
            lines.append(f"|{case['variant']} / {case['cost_case']}|{m['total_return']:.2%}|{m['annualized_return']:.2%}|{sharpe}|{m['maximum_drawdown']:.2%}|{m['annualized_turnover']:.2f}|{m['commission'] + m['slippage']:.2f} {result['currency']}|{m['average_exposure']:.2%}|{m['entry_fills']}|")
        for cost, benchmarks in result["benchmarks"].items():
            for name, periods in benchmarks.items():
                m = periods["full"]
                lines.append(f"|{name} / {cost}|{m['total_return']:.2%}|{m['annualized_return']:.2%}|{m['sharpe_ratio']:.2f}|{m['maximum_drawdown']:.2%}|{m['annualized_turnover']:.2f}|{m['commission'] + m['slippage']:.2f} {result['currency']}|{m['average_exposure']:.2%}|{m['entry_fills']}|")
        lines += ["", "### 回溯留出：所有固定候选", "", "|候选 / 成本|区间|年化|回撤|100%基准年化|80%基准年化|", "|---|---|---:|---:|---:|---:|"]
        for case in result["cases"]:
            m = case["periods"]["retrospective_holdout"]
            benchmarks = result["benchmarks"][case["cost_case"]]
            lines.append(f"|{case['variant']} / {case['cost_case']}|{m['start']} — {m['end']}|{m['annualized_return']:.2%}|{m['maximum_drawdown']:.2%}|{benchmarks['buy_hold_100']['retrospective_holdout']['annualized_return']:.2%}|{benchmarks['buy_hold_80']['retrospective_holdout']['annualized_return']:.2%}|")
        lines += ["", "### 固定滚动诊断（全部候选）", "", "504日历史 / 252日测试 / 252日步进；无参数训练，切片连续账户、不重置持仓；末尾不足252日仍在全历史/留出中报告。", "",
                  "|候选 / 成本|测试区间|年化|回撤|基准年化|成本比率和|", "|---|---|---:|---:|---:|---:|"]
        for case in result["cases"]:
            for w in case["walk_forward"]:
                lines.append(f"|{case['variant']} / {case['cost_case']}|{w['start']} — {w['end']}|{w['annualized_return']:.2%}|{w['maximum_drawdown']:.2%}|{w['benchmark']['annualized_return']:.2%}|{w['total_transaction_cost']:.2%}|")
        lines += ["", "### 主展示诊断", "", json.dumps(result["diagnostics"], ensure_ascii=False), ""]
    lines += ["## 数据、可复现性与下一步", "", f"配置SHA256：`{publication['config_hash']}`。",
              f"起始源码状态：`{json.dumps(publication['code_provenance'], ensure_ascii=False)}`。源码快照及哈希随产物保存。",
              f"固定版本输入数：{len(publication['data_versions'])}；原始响应与清洗快照均保留。没有填补未知价格，没有调用分钟接口。",
              "下一步：审阅暴露/空仓机会成本、负窗口和滑点压力；新ETF/个股扩展须另行固定规则及样本，不能用本轮留出反选参数。",
              "数据截至2026-09-29，尚无登记后的新市场观察；不能据此进入模拟盘。",
              "完整净值、成交、信号和结构化指标在本运行目录，历史产物不覆盖。"]
    return "\n".join(lines) + "\n"


def run(us_path: Path, cn_dir: Path, output: Path, config_path: Path):
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    from deepstock.research_control import research_pause
    if research_pause(cfg["strategy_id"], ROOT):
        raise ValueError("ETF strategy research is paused")
    output.mkdir(parents=True, exist_ok=False)
    if tuple(cfg["variants"]) != VARIANTS or cfg["principal_variant"] != "trend_pullback" or cfg["paper_authorized"] or cfg["live_authorized"]:
        raise ValueError("Fixed candidate/execution boundary changed")
    code = capture_code_provenance()
    source_names = ["scripts/run_granville_backtest.py", "scripts/download_granville_cn_daily.py", "src/deepstock/strategies/both/granville.py",
                    "src/deepstock/data/store.py", "pyproject.toml"]
    code["source_file_hashes"] = {name: digest(ROOT / name) for name in source_names}
    (output / "source").mkdir()
    for name in source_names:
        target = output / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    shutil.copyfile(config_path, output / "fixed_config.json")
    write_json(output / "start_provenance.json", code)
    us = read_clean_csv(us_path)
    us.index = pd.DatetimeIndex(pd.to_datetime(us.date))
    for field in ["open", "high", "low", "close"]:
        us[field] = us["adjusted_" + field]
    calendar_us = xcals.get_calendar("XNYS", start=str(us.index[0].date()), end=str(us.index[-1].date())).sessions.tz_localize(None)
    cn = read_clean_csv(cn_dir / "daily.csv")
    cn.index = pd.DatetimeIndex(pd.to_datetime(cn.date))
    dividends = read_clean_csv(cn_dir / "dividends.csv")
    calendar_cn = pd.DatetimeIndex(pd.to_datetime(read_clean_csv(cn_dir / "calendar.csv").date))
    publication = {"id": "granville-" + output.name, "strategy_id": cfg["strategy_id"], "status": "completed",
                   "as_of_date": datetime.now(timezone.utc).date().isoformat(), "data_start": cfg["evaluation_start"], "data_end": cfg["evaluation_end"],
                   "config": cfg, "config_hash": digest(config_path), "code_version": None, "code_provenance": code,
                   "summary": "独立双市场ETF固定回测，三类信号与两种成本全部保留；仅回溯诊断，未授权交易。", "market_results": {},
                   "runtime": {"numpy": np.__version__, "pandas": pd.__version__}}
    for market, bars, calendar, div in [("US", us, calendar_us, None), ("CN", cn, calendar_cn, dividends)]:
        market_cfg = cfg["markets"][market]
        data = {"symbol": market_cfg["symbol"], "currency": market_cfg["currency"], "principal_variant": cfg["principal_variant"],
                "cost_basis": market_cfg, "evidence_scope": cfg["evidence_scope"], "cases": [], "benchmarks": {}}
        for stress in [False, True]:
            cost = "stress" if stress else "base"
            benchmark_daily = {}
            data["benchmarks"][cost] = {}
            for exposure in [1., .8]:
                name = f"buy_hold_{round(exposure * 100)}"
                result = run_granville(bars, calendar, cfg, market, "buy_hold", stress=stress, dividends=div, benchmark_exposure=exposure)
                benchmark_daily[name] = result.daily
                data["benchmarks"][cost][name] = period_metrics(result.daily, cfg["diagnostic_split"])
                result.daily.to_csv(output / f"{market}-{name}-{cost}-daily.csv")
                result.trades.to_csv(output / f"{market}-{name}-{cost}-trades.csv", index=False)
            for variant in VARIANTS:
                result = run_granville(bars, calendar, cfg, market, variant, stress=stress, dividends=div)
                for name in ["daily", "trades", "signals"]:
                    getattr(result, name).to_csv(output / f"{market}-{variant}-{cost}-{name}.csv", index=name != "trades")
                case = {"variant": variant, "cost_case": cost, "periods": period_metrics(result.daily, cfg["diagnostic_split"]),
                        "walk_forward": walk_forward(result.daily, benchmark_daily["buy_hold_100"], cfg["walk_forward"])}
                data["cases"].append(case)
                if variant == cfg["principal_variant"] and not stress:
                    data["metrics"] = case["periods"]["full"]
                    data["holdout"] = case["periods"]["retrospective_holdout"]
                    data["benchmark_metrics"] = data["benchmarks"][cost]["buy_hold_100"]["full"]
                    data["matched_benchmark_metrics"] = data["benchmarks"][cost]["buy_hold_80"]["full"]
                    data["diagnostics"] = {"walk_forward_windows": len(case["walk_forward"]), "negative_windows": sum(w["total_return"] < 0 for w in case["walk_forward"]),
                                           "worst_window_return": min(w["total_return"] for w in case["walk_forward"]),
                                           "maximum_holding_sessions": int(result.daily.held_sessions.max()),
                                           "pending_action_at_end": result.daily.pending_action.iloc[-1],
                                           "cash_fraction_of_sessions": float(result.daily.quantity.eq(0).mean())}
        publication["market_results"][market] = data
    refs = input_evidence()["data_versions"]
    upstream = json.loads((cn_dir / "manifest.json").read_text())["data_versions"]
    publication["data_versions"] = list({r["version"]: r for r in [*upstream, *refs]}.values())
    publication["input_exclusions"] = input_evidence()["input_exclusions"]
    publication["artifact_hashes"] = {p.name: digest(p) for p in output.glob("*.csv")}
    publication["report_md"] = render_report(publication)
    (output / "report.md").write_text(publication["report_md"], encoding="utf-8")
    write_json(output / "publication.json", publication)
    return {"status": "completed", "folder": str(output), "markets": {m: r["metrics"] for m, r in publication["market_results"].items()}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--us-prices", type=Path, default=ROOT / "artifacts/research/norgate/spy_daily_ohlc.csv")
    p.add_argument("--cn-dir", type=Path, default=ROOT / "artifacts/data/granville-cn-20261006-v4")
    p.add_argument("--config", type=Path, default=ROOT / "config/granville_v1.json")
    p.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/research/granville" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]))
    args = p.parse_args()
    print(json.dumps(run(args.us_prices, args.cn_dir, args.output_dir, args.config), ensure_ascii=False))


if __name__ == "__main__":
    main()
