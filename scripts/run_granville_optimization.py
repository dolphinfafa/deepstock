"""Preregistered entry-episode diagnostic; reuse v1 evidence, never promote a winner."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import uuid

import pandas as pd

from deepstock.data.store import ROOT, digest, input_evidence, write_json
from deepstock.research_control import research_pause
from deepstock.strategies.both.granville_portfolio import PortfolioDataError, run_stock_portfolio
from deepstock.strategies.both.granville_diagnostics import cost_path_attribution, ledger_diagnostics, validate_optimization

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.run_granville_portfolio import fixed_config, load_stock_inputs
from scripts.run_granville_backtest import period_metrics
from scripts.rerun_clean_research import capture_code_provenance


def experiment_config(path):
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if (cfg["strategy_id"] != "granville_stock_portfolio" or cfg["experiment_id"] != "granville-entry-episodes-v2-20261006"
        or cfg["variant"] != "trend_pullback" or cfg["exit_policy"] != "time_7"
        or cfg["entry_policies"] != ["signal_level", "once_per_episode"] or cfg["cost_cases"] != ["base", "stress"]
        or cfg["principal_entry_policy"] != "signal_level" or cfg["paper_authorized"] or cfg["live_authorized"]):
        raise ValueError("Fixed entry-episode/principal boundary changed")
    return cfg


def verify_baseline(daily, trades, directory, summary, cost):
    stem = f"trend_pullback-time_7-{cost}"
    paths = {name: directory / f"{stem}-{name}.csv" for name in ["daily", "trades"]}
    for path in paths.values():
        if digest(path) != summary["artifact_hashes"][path.name]:
            raise ValueError("Immutable baseline ledger hash differs")
    original_daily = pd.read_csv(paths["daily"], parse_dates=["date"]).set_index("date")
    original_trades = pd.read_csv(paths["trades"])
    pd.testing.assert_frame_equal(daily[original_daily.columns], original_daily, check_dtype=False, check_freq=False, rtol=1e-10, atol=1e-8)
    pd.testing.assert_frame_equal(trades[original_trades.columns], original_trades, check_dtype=False, rtol=1e-10, atol=1e-8)
    return {path.name: digest(path) for path in paths.values()}


def run(market, data_dir, baseline_dir, output, config_path):
    exp = experiment_config(config_path)
    if research_pause(exp["strategy_id"], ROOT):
        raise ValueError("Research is paused")
    base_path = ROOT / exp["base_config"]
    cfg = fixed_config(base_path)
    rule = json.loads((ROOT / cfg["signal_config"]).read_text(encoding="utf-8"))["signal"]
    original = json.loads((baseline_dir / "market_summary.json").read_text(encoding="utf-8"))
    if original["market"] != market or original["config"] != cfg:
        raise ValueError("Original market/config differs; no refit")
    code = capture_code_provenance()
    if code["tracked_dirty"]:
        raise ValueError("Run from committed source, not an untracked parameter experiment")
    output.mkdir(parents=True, exist_ok=False)
    source_names = ["scripts/run_granville_optimization.py", "scripts/run_granville_portfolio.py",
                    "scripts/run_granville_backtest.py", "scripts/rerun_clean_research.py",
                    "src/deepstock/strategies/both/granville_portfolio.py", "src/deepstock/strategies/both/granville_diagnostics.py",
                    "src/deepstock/strategies/both/granville.py", "src/deepstock/data/store.py",
                    "src/deepstock/data/stock_actions.py", cfg["signal_config"], exp["base_config"], "pyproject.toml"]
    code["source_file_hashes"] = {name: digest(ROOT / name) for name in source_names}
    for name in source_names:
        target = output / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    shutil.copyfile(config_path, output / "experiment_config.json")
    write_json(output / "start_provenance.json", code)
    panel, manifest = load_stock_inputs(market, data_dir, cfg, rule, base_path)
    result = original.copy()
    opt = {"experiment": exp, "experiment_hash": digest(config_path), "cases": [], "cost_attribution": {},
           "source_baseline_summary_hash": digest(baseline_dir / "market_summary.json"), "code_provenance": code,
           "runtime": {"python": sys.version, "pandas": pd.__version__}}
    result["optimization"] = opt
    for policy in exp["entry_policies"]:
        diagnostics = {}
        for cost in exp["cost_cases"]:
            case = {"entry_policy": policy, "cost_case": cost, "status": "blocked", "periods": {}}
            try:
                daily, trades, summary = run_stock_portfolio(panel, cfg, rule, market, exp["variant"], exp["exit_policy"], cost == "stress", entry_policy=policy)
                if policy == "signal_level":
                    case["verified_baseline_artifact_hashes"] = verify_baseline(daily, trades, baseline_dir, original, cost)
                    case["baseline_verified"] = True
                daily.to_csv(output / f"{policy}-{cost}-daily.csv")
                trades.to_csv(output / f"{policy}-{cost}-trades.csv", index=False)
                case.update(status="completed", periods=period_metrics(daily, cfg["diagnostic_split"]),
                            diagnostics=ledger_diagnostics(daily, trades, cfg["initial_capital"]))
                diagnostics[cost] = case["diagnostics"]
            except PortfolioDataError as error:
                case["blocking_reason"] = str(error)
            opt["cases"].append(case)
            print(json.dumps({"market": market, "policy": policy, "cost": cost, "status": case["status"],
                              "metrics": case["periods"].get("full"), "blocking_reason": case.get("blocking_reason")}), flush=True)
        if set(diagnostics) == {"base", "stress"}:
            opt["cost_attribution"][policy] = cost_path_attribution(diagnostics["base"], diagnostics["stress"])
    # Preserve the old fixed principal, all old candidates and their blocks.
    # Independent replay is required, not simply attaching old headline metrics.
    baseline = next(c for c in opt["cases"] if c["entry_policy"] == "signal_level" and c["cost_case"] == "base")
    if baseline["status"] != "completed":
        raise PortfolioDataError("Original primary baseline could not be independently reproduced")
    result["metrics"] = baseline["periods"]["full"]
    opt["artifact_hashes"] = {p.name: digest(p) for p in output.glob("*.csv")}
    result["data_versions"] = list({v["version"]: v for v in [*original["data_versions"], *manifest.get("data_versions", []), *input_evidence()["data_versions"]]}.values())
    result["input_exclusions"] = [*original["input_exclusions"], *input_evidence()["input_exclusions"]]
    if capture_code_provenance()["source_sha256"] != code["source_sha256"] or digest(config_path) != opt["experiment_hash"]:
        raise ValueError("Source/experiment changed during the run; cannot publish")
    write_json(output / "market_summary.json", result)
    return result


def render_report(value):
    lines = ["# 葛兰威尔多股票组合：退出、重入及成本诊断", "", value["summary"], "",
             "只修改入场重复规则：同一股票连续成立的收盘信号区间，最多成功开仓一次。信号不成立后再次成立才重新获得资格。",
             "未成交、仓位满或资金不足不消耗资格；保留暖期信号区间。它是信号区间代理，不代表严格辨认一次新的实际回踩。",
             "其他均线、排名、5仓、16%新仓、80%开仓总上限、7日退出、止损、冷却、成本与数据全部不变。",
             "主展示保留原基线，不按结果选择赢家。原24组与WBA阻塞保留，新8组全部报告。历史已被查看，不是前瞻OOS，也没有新增完整Walk-Forward窗口。", ""]
    for market, result in value["market_results"].items():
        opt = result["optimization"]
        lines += [f"## {market} · {result['currency']}", "", "|入场规则 / 成本|累计|年化|回撤|年化换手|平均暴露|买入|同区间重复买入|直接成本/初始资金|",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for case in opt["cases"]:
            label = f"{case['entry_policy']} / {case['cost_case']}"
            if case["status"] != "completed":
                lines.append(f"|{label}|阻塞：{case['blocking_reason']}|—|—|—|—|—|—|—|")
                continue
            m, d = case["periods"]["full"], case["diagnostics"]
            lines.append(f"|{label}|{m['total_return']:.2%}|{m['annualized_return']:.2%}|{m['maximum_drawdown']:.2%}|{m['annualized_turnover']:.2f}|{m['average_exposure']:.2%}|{d['buy_fills']}|{d['same_episode_repeat_buys']}|{d['cash_identity']['direct_cost_initial_capital_ratio']:.2%}|")
        lines += ["", "### 退出归因及滑点压力分解", "", "```json", json.dumps({"cases": [{"policy": c["entry_policy"], "cost": c["cost_case"], "diagnostics": c.get("diagnostics"), "periods": c["periods"]} for c in opt["cases"]], "cost_attribution": opt["cost_attribution"]}, ensure_ascii=False, indent=2), "```", ""]
    lines += ["## 口径与未解决问题", "", "直接成本/初始资金是实际账本金额比，不是复利收益损失。压力差按现金恒等式拆为新增直接成本与持仓路径毛损益变化；不假设压力成交路径相同，不虚构零成本重投收益。",
              "退出原因分析只统计完整平仓，含买卖滑点和费用；没有逐股分配股息税，末期持仓不强行卖出。",
              "原数据、月末成分代理、经济收益单位、行业/终止兑付/税务结算限制全部保留。更长历史、风险定仓和市场过滤是后续独立实验，不在本轮叠加。",
              "当前研究不构成策略改进成功或Paper准入；没有创建观察任务或订单。"]
    return "\n".join(lines) + "\n"


def publish(us_path, cn_path, output, config_path):
    exp = experiment_config(config_path)
    cfg = fixed_config(ROOT / exp["base_config"])
    results = {m: json.loads(p.read_text(encoding="utf-8")) for m, p in [("US", us_path), ("CN", cn_path)]}
    for market, result in results.items():
        if result["market"] != market or result["config"] != cfg or result["optimization"]["experiment"] != exp:
            raise ValueError("Cross-node base/experiment contracts differ")
    value = {"id": "granville-episodes-" + output.name, "strategy_id": exp["strategy_id"], "experiment": exp, "config": cfg,
             "config_hash": digest(ROOT / exp["base_config"]), "experiment_hash": digest(config_path),
             "experiment_semantic_hash": hashlib.sha256(json.dumps(exp, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
             "data_start": cfg["evaluation_start"], "data_end": cfg["evaluation_end"],
             "as_of_date": datetime.now(timezone.utc).date().isoformat(), "market_results": results,
             "status": "completed_with_blocks" if any(c["status"] == "blocked" for r in results.values() for c in [*r["cases"], *r["optimization"]["cases"]]) else "completed",
             "summary": "单项优化诊断：每个连续信号区间只成功买入一次；两市场基础/压力共8组，主指标仍为原基线，未按结果升级策略。",
             "source_summary_hashes": {"US": digest(us_path), "CN": digest(cn_path)},
             "data_versions": list({v["version"]: v for r in results.values() for v in r["data_versions"]}.values()),
             "input_exclusions": [v for r in results.values() for v in r["input_exclusions"]]}
    validate_optimization(value)
    output.mkdir(parents=True, exist_ok=False)
    for market, path in [("US", us_path), ("CN", cn_path)]:
        shutil.copyfile(path, output / f"{market}-market_summary.json")
    value["report_md"] = render_report(value)
    (output / "report.md").write_text(value["report_md"], encoding="utf-8")
    write_json(output / "publication.json", value)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=["US", "CN"])
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--us-summary", type=Path)
    parser.add_argument("--cn-summary", type=Path)
    parser.add_argument("--config", type=Path, default=ROOT / "config/granville_portfolio_episodes_v2.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/research/granville-stock-optimizations" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]))
    args = parser.parse_args()
    if args.us_summary and args.cn_summary:
        result = publish(args.us_summary, args.cn_summary, args.output_dir, args.config)
    elif args.market and args.data_dir and args.baseline_dir:
        result = run(args.market, args.data_dir, args.baseline_dir, args.output_dir, args.config)
    else:
        parser.error("Need market/data-dir/baseline-dir or both market summaries")
    print(json.dumps({"status": result.get("status", "completed"), "output": str(args.output_dir)}))


if __name__ == "__main__":
    main()
