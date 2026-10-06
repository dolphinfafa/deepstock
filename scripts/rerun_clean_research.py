"""Fixed input/config reruns only; preserve historical outputs and all candidates."""
from __future__ import annotations
import argparse
import hashlib
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
from deepstock.data.store import ROOT, write_json
from deepstock.data.performance import annualize_net_returns
from deepstock.research_control import research_pause


def publish(strategy, folder, summary_path, daily_path, scope, label, baseline=None):
    summary = json.loads(summary_path.read_text())
    metrics = summary.get("out_of_sample", summary) if scope == "oos" else summary
    if "full" in metrics:
        metrics = metrics["full"]
    daily = pd.read_csv(daily_path, parse_dates=["date"]).set_index("date")
    if scope == "oos":
        daily = daily.loc["2021-08-23":]
    annual = annualize_net_returns(daily.portfolio_net_return, scope, "原固定参数；单边5bp成本，保留原成交和滑点假设")
    selected = {name: float(metrics[name]) for name in ["total_return", "annualized_return", "sharpe_ratio", "maximum_drawdown", "total_transaction_cost", "total_turnover"] if name in metrics}
    selected["annualized_return"] = annual["value"]
    refs = summary.get("data_versions", [])
    if not refs and (folder / "manifest.json").exists():
        refs = json.loads((folder / "manifest.json").read_text()).get("data_versions", [])
    comparison = {}
    if baseline and baseline.exists():
        old = json.loads(baseline.read_text())
        if scope == "oos":
            old = old.get("out_of_sample", old)
        comparison = {k: {"old": old[k], "new": v, "delta": v - float(old[k])} for k, v in selected.items() if isinstance(old.get(k), (float, int))}
    report = f"# 清洗版本固定回测：{label}\n\n区间 {annual['data_start']} 至 {annual['data_end']}；{annual['sessions']} 交易日，范围 {scope}。\n\n参数、成本、候选集均不按新OOS结果选择。旧报告保留。\n\n年化 {annual['value']:.2%}。\n\n输入版本 {len(refs)} 个；缺失行情没有插值补造。\n\n旧结果对照：\n\n```json\n{json.dumps(comparison, ensure_ascii=False, indent=2)}\n```\n"
    value = {"id": "reclean-" + strategy + "-" + folder.name, "strategy_id": strategy, "status": "complete", "scope": scope,
             "as_of_date": datetime.now(timezone.utc).date().isoformat(), "data_start": annual["data_start"], "data_end": annual["data_end"],
             "annualization": annual, "metrics": selected, "data_versions": refs, "config": summary.get("config", {}),
             "config_hash": hashlib.sha256(json.dumps(summary.get("config", {}), sort_keys=True).encode()).hexdigest(),
             "code_version": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip(),
             "summary": label + "：固定参数、清洗版本、成本后结果", "comparison": comparison, "report_md": report}
    write_json(folder / "publication.json", value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=["adaptive_defensive_etf", "range_grid", "spy_mean_reversion", "stock_turtle", "arc"], required=True)
    args = parser.parse_args()
    catalog = json.loads((ROOT / "config/strategy_catalog.json").read_text())["strategies"]
    entry = next(s for s in catalog if s["id"] == args.strategy)
    if entry.get("is_archived") or research_pause(args.strategy, ROOT):
        raise RuntimeError("Research is frozen or paused")
    run_folder = ROOT / "artifacts/reclean" / (args.strategy + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6])
    run_folder.mkdir(parents=True)
    prices = "artifacts/research/norgate/etf_prices.csv"
    universe = "artifacts/research/norgate/stock-universe-sp500-liquidity"
    commands = {
        "adaptive_defensive_etf": ["scripts/run_defensive_etf_backtest.py", "--profile", "adaptive", "--prices", prices],
        "range_grid": ["scripts/run_arc_grid_backtest.py", "--prices", prices],
        "spy_mean_reversion": ["scripts/run_spy_mean_reversion_backtest.py", "--prices", prices],
        "stock_turtle": ["-m", "scripts.run_point_in_time_turtle", "--universe-dir", universe, "--etf-prices", prices, "--save-artifacts"],
        "arc": ["-m", "scripts.run_arc_adx_backtest", "--universe-dir", universe, "--etf-prices", prices, "--spy-ohlc", "artifacts/research/norgate/spy_daily_ohlc.csv"],
    }
    with (run_folder / "run.log").open("w") as log:
        process = subprocess.run([sys.executable, *commands[args.strategy], "--output-dir", str(run_folder)], cwd=ROOT, stdout=log, stderr=log, check=False)
    if process.returncode:
        value = {"id": "reclean-" + args.strategy + "-" + run_folder.name, "strategy_id": args.strategy, "status": "blocked",
                 "as_of_date": datetime.now(timezone.utc).date().isoformat(), "summary": "清洗版本固定回测阻塞，保留上一成功结果", "metrics": {},
                 "report_md": "# 固定回测阻塞\n\n" + (run_folder / "run.log").read_text()[-4000:]}
        write_json(run_folder / "publication.json", value)
        print(json.dumps({"strategy": args.strategy, "status": "blocked", "folder": str(run_folder)}))
        return
    if args.strategy == "arc":
        for path in run_folder.glob("*/summary.json"):
            publish(args.strategy, path.parent, path, path.with_name("daily_results.csv"), "full", path.parent.name,
                    ROOT / "artifacts/robustness/arc-adx-full-history-2026-09-30" / path.parent.name / "summary.json")
        # Principal is the preregistered anti-churn controller, not the best rerun.
        principal = run_folder / "adx_14_5_confirm_10_hold_20_reentry/publication.json"
        value = json.loads(principal.read_text())
        value["report_md"] += "\n\n三种既定控制器全部保留；主展示固定为原已登记抗抖动候选，不按收益切换。"
        write_json(run_folder / "publication.json", value)
    elif args.strategy == "stock_turtle":
        table = pd.read_csv(run_folder / "parameter_results.csv")
        baseline = table[table.candidate == "baseline_55_20_5"].iloc[0]
        metrics = {k.removeprefix("standalone_out_of_sample_"): v for k, v in baseline.items() if k.startswith("standalone_out_of_sample_")}
        manifest = json.loads((run_folder / "manifest.json").read_text())
        summary = {**metrics, "data_versions": manifest["data_versions"], "config": {"candidate": "baseline_55_20_5", "all_candidates_preserved": True}}
        write_json(run_folder / "summary.json", summary)
        publish(args.strategy, run_folder, run_folder / "summary.json", run_folder / "baseline_55_20_5_standalone_daily.csv", "oos", "个股海龟固定55/20基线；全部六候选保留")
    else:
        scope = "full" if args.strategy == "range_grid" else "oos"
        old = ROOT / ("artifacts/robustness/arc-grid-2026-08-22/summary.json" if args.strategy == "range_grid" else "artifacts/robustness/spy-mean-reversion-2026-08-25/summary.json")
        publish(args.strategy, run_folder, run_folder / "summary.json", run_folder / "daily_results.csv", scope, entry["display_name"], old if args.strategy != "adaptive_defensive_etf" else None)
    print(json.dumps({"strategy": args.strategy, "status": "complete", "folder": str(run_folder)}))


if __name__ == "__main__":
    main()
