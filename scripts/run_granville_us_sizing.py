"""Four preregistered US sizing cases; no CN rerun and no outcome selection."""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

import pandas as pd
from deepstock.data.store import ROOT, digest, input_evidence, write_json
from deepstock.research_control import research_pause
from deepstock.strategies.both.granville_portfolio import PortfolioDataError, run_stock_portfolio
from deepstock.strategies.both.granville_diagnostics import ledger_diagnostics, cost_path_attribution
from deepstock.strategies.us.granville_sizing import validate_sizing_config, validate_sizing

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.run_granville_portfolio import fixed_config, load_stock_inputs
from scripts.run_granville_optimization import verify_baseline
from scripts.run_granville_backtest import period_metrics
from scripts.rerun_clean_research import capture_code_provenance


def run(data_dir, baseline_dir, output, config_path):
    exp = validate_sizing_config(json.loads(config_path.read_text(encoding="utf-8")))
    if research_pause(exp["strategy_id"], ROOT):
        raise ValueError("Research paused")
    base_path = ROOT / exp["base_config"]
    cfg = fixed_config(base_path)
    if cfg["initial_position_target"] != .16:
        raise ValueError("Original 16% sizing contract required")
    original = json.loads((baseline_dir / "market_summary.json").read_text(encoding="utf-8"))
    if original["market"] != "US" or original["config"] != cfg:
        raise ValueError("Original dataset/config differs")
    code = capture_code_provenance()
    if code["tracked_dirty"]:
        raise ValueError("Committed clean source required")
    output.mkdir(parents=True, exist_ok=False)
    names = ["scripts/run_granville_us_sizing.py", "scripts/run_granville_optimization.py", "scripts/run_granville_portfolio.py",
             "scripts/run_granville_backtest.py", "scripts/rerun_clean_research.py", "src/deepstock/strategies/us/granville_sizing.py",
             "src/deepstock/strategies/both/granville_portfolio.py", "src/deepstock/strategies/both/granville_diagnostics.py",
             "src/deepstock/strategies/both/granville.py", "src/deepstock/data/store.py", "src/deepstock/data/stock_actions.py", "src/deepstock/data/membership.py",
             exp["base_config"], cfg["signal_config"], "config/granville_us_sizing_v3.json", "pyproject.toml"]
    code["source_file_hashes"] = {n: digest(ROOT / n) for n in names}
    for name in names:
        target = output / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    write_json(output / "start_provenance.json", code)
    rule = json.loads((ROOT / cfg["signal_config"]).read_text(encoding="utf-8"))["signal"]
    panel, manifest = load_stock_inputs("US", data_dir, cfg, rule, base_path)
    if len(panel.symbols) != 535 or manifest["symbol_count"] != 535:
        raise ValueError("Original 535-name historical pool required")
    result = deepcopy(original)
    opt = {"experiment": exp, "experiment_hash": digest(config_path), "cases": [], "cost_attribution": {},
           "code_provenance": code, "runtime": {"python": sys.version, "pandas": pd.__version__},
           "source_baseline_summary_hash": digest(baseline_dir / "market_summary.json")}
    result["sizing_experiment"] = opt
    # Both baselines run/verify first. A mismatch or baseline block stops all comparisons.
    for policy in exp["sizing_policies"]:
        ds = {}
        for cost in exp["cost_cases"]:
            case = {"sizing_policy": policy, "cost_case": cost, "status": "blocked", "periods": {}}
            try:
                daily, trades, summary = run_stock_portfolio(panel, cfg, rule, "US", stress=cost == "stress", sizing_policy=policy)
                if policy == "fixed_16pct":
                    case["verified_baseline_artifact_hashes"] = verify_baseline(daily, trades, baseline_dir, original, cost)
                    case["baseline_verified"] = True
                stem = f"{policy}-{cost}"
                daily.to_csv(output / f"{stem}-daily.csv")
                trades.to_csv(output / f"{stem}-trades.csv", index=False)
                pd.DataFrame(summary["sizing_audit"]).to_csv(output / f"{stem}-sizing.csv", index=False)
                case.update(status="completed", periods=period_metrics(daily, cfg["diagnostic_split"]),
                            diagnostics=ledger_diagnostics(daily, trades, cfg["initial_capital"]),
                            average_cash_fraction=summary["average_cash_fraction"], invalid_sizing_candidates=summary["invalid_sizing_candidates"],
                            max_target_weight=max((a["target_weight"] for a in summary["sizing_audit"] if a["status"] == "filled"), default=0))
                ds[cost] = case["diagnostics"]
            except PortfolioDataError as error:
                case["blocking_reason"] = str(error)
                if policy == "fixed_16pct":
                    raise
            opt["cases"].append(case)
            print(json.dumps({"policy": policy, "cost": cost, "status": case["status"], "metrics": case["periods"].get("full")}), flush=True)
        if set(ds) == {"base", "stress"}:
            opt["cost_attribution"][policy] = cost_path_attribution(ds["base"], ds["stress"])
    opt["artifact_hashes"] = {p.name: digest(p) for p in output.glob("*.csv")}
    result["data_versions"] = list({v["version"]: v for v in [*original["data_versions"], *manifest.get("data_versions", []), *input_evidence()["data_versions"]]}.values())
    result["input_exclusions"] = [*original["input_exclusions"], *input_evidence()["input_exclusions"]]
    if capture_code_provenance()["source_sha256"] != code["source_sha256"] or digest(config_path) != opt["experiment_hash"]:
        raise ValueError("Source changed during experiment")
    write_json(output / "market_summary.json", result)
    return result


def render_report(value):
    us = value["market_results"]["US"]
    lines = ["# 葛兰威尔美股组合：仅风险缩仓对照", "", value["summary"], "",
             "固定公式：16% × min(1, 历史合资格股票池20日波动中位数 / 个股20日波动)。使用前收盘复权收益、ddof=0；低波动不加仓，无效波动禁止开仓并登记。余款留现金。",
             "同一535只历史成员、同一清洗版本与成本；入场、排名、7日退出、止损、冷却都不变。仅补实际空位，最多5仓，80%开仓总上限，不每日再平衡。",
             "两套基线逐日/逐笔复现且验证旧文件SHA。原24组、v2与CN历史全部保留；本轮没有回测CN。主展示仍为基线，不按结果晋升。",
             "已见历史：2025-01-02—2026-09-29，零完整504+252窗口，不是前瞻OOS。", "",
             "|仓位 / 成本|累计|年化|Sharpe|回撤|年换手|均暴露|均现金|直接成本/初始资金|",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for case in us["sizing_experiment"]["cases"]:
        if case["status"] != "completed":
            lines.append(f"|{case['sizing_policy']} / {case['cost_case']}|阻塞：{case['blocking_reason']}|—|—|—|—|—|—|—|")
            continue
        m = case["periods"]["full"]
        lines.append(f"|{case['sizing_policy']} / {case['cost_case']}|{m['total_return']:.2%}|{m['annualized_return']:.2%}|{m['sharpe_ratio']:.2f}|{m['maximum_drawdown']:.2%}|{m['annualized_turnover']:.2f}|{m['average_exposure']:.2%}|{case['average_cash_fraction']:.2%}|{case['diagnostics']['cash_identity']['direct_cost_initial_capital_ratio']:.2%}|")
    for name in ["benchmark_metrics", "matched_benchmark_metrics"]:
        m = us[name]
        lines += ["", f"SPY {name}：累计{m['total_return']:.2%}、年化{m['annualized_return']:.2%}、回撤{m['maximum_drawdown']:.2%}。80%基准是初始暴露，不是与缩仓方案动态现金匹配。"]
    lines += ["", "## 分段、费用与成交路径", "", "```json",
              json.dumps(us["sizing_experiment"], ensure_ascii=False, indent=2), "```", "",
              "现金收益0；金额成本比不是复利拖累。经济总收益单位不等于可执行真实股数。原终止兑付、历史行业、税务结算与长历史限制不变；无Paper/Live权限。CN仅沿用10月6日证据，未在本轮回测。"]
    return "\n".join(lines) + "\n"


def publish(us_path, history_path, output, config_path):
    exp = validate_sizing_config(json.loads(config_path.read_text(encoding="utf-8")))
    history = json.loads(history_path.read_text(encoding="utf-8"))
    result = json.loads(us_path.read_text(encoding="utf-8"))
    if history["config"] != fixed_config(ROOT / exp["base_config"]):
        raise ValueError("Historical base contract changed")
    if result["market"] != "US" or result["config"] != history["config"] or result["sizing_experiment"]["experiment"] != exp:
        raise ValueError("Cross-node sizing/history contracts differ")
    old_us = history["market_results"]["US"]
    if result["cases"] != old_us["cases"] or result["metrics"] != old_us["metrics"]:
        raise ValueError("Immutable US history changed")
    result["optimization"] = deepcopy(old_us["optimization"])
    cn = deepcopy(history["market_results"]["CN"])
    cn["current_experiment_status"] = "not_rerun_historical_evidence"
    value = {"id": "granville-sizing-" + output.name, "strategy_id": exp["strategy_id"], "experiment": exp,
             "config": history["config"], "config_hash": history["config_hash"], "experiment_hash": digest(config_path),
             "data_start": history["data_start"], "data_end": history["data_end"], "as_of_date": datetime.now(timezone.utc).date().isoformat(),
             "market_results": {"US": result, "CN": cn}, "status": "completed_with_blocks",
             "summary": "美股固定单项风险缩仓实验：两种仓位×两种成本共4组；原基线主展示及Both历史保留，A股本轮未回测。不是前瞻OOS或模拟盘准入。",
             "source_summary_hash": digest(us_path), "history_publication_hash": digest(history_path),
             "data_versions": list({v["version"]: v for r in [result, cn] for v in r["data_versions"]}.values()),
             "input_exclusions": [*result["input_exclusions"], *cn["input_exclusions"]]}
    validate_sizing(value)
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(us_path, output / "US-market_summary.json")
    value["report_md"] = render_report(value)
    (output / "report.md").write_text(value["report_md"], encoding="utf-8")
    write_json(output / "publication.json", value)
    return value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", type=Path)
    p.add_argument("--baseline-dir", type=Path)
    p.add_argument("--us-summary", type=Path)
    p.add_argument("--history-publication", type=Path)
    p.add_argument("--config", type=Path, default=ROOT / "config/granville_us_sizing_v3.json")
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    if a.us_summary and a.history_publication:
        publish(a.us_summary, a.history_publication, a.output_dir, a.config)
    elif a.data_dir and a.baseline_dir:
        run(a.data_dir, a.baseline_dir, a.output_dir, a.config)
    else:
        p.error("Need data/baseline dirs or US summary/history publication")


if __name__ == "__main__":
    main()
