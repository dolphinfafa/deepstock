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
from deepstock.data.store import ROOT, digest, write_json
from deepstock.data.performance import annualize_net_returns
from deepstock.research_control import research_pause
from deepstock.turtle import TurtleConfig


def capture_code_provenance():
    """Fingerprint tracked bytes before computation, never secrets or artifacts."""
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    paths = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout.split(b"\0")
    fingerprint = hashlib.sha256()
    for name in sorted(p for p in paths if p):
        path = ROOT / name.decode("utf-8")
        fingerprint.update(name + b"\0" + (digest(path).encode() if path.is_file() else b"missing") + b"\0")
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, capture_output=True, text=True, check=True).stdout)
    return {"base_commit": commit, "tracked_dirty": dirty, "source_sha256": fingerprint.hexdigest(),
            "captured_at_utc": datetime.now(timezone.utc).isoformat()}


def publication_id(strategy, folder, run_folder):
    return "reclean-" + strategy + "-" + run_folder.name + ("-" + folder.name if folder != run_folder else "")


def net_daily_metrics(daily):
    returns = daily.portfolio_net_return
    equity = (1 + returns).cumprod()
    volatility = returns.std(ddof=0)
    return {"total_return": float(equity.iloc[-1] - 1),
            "sharpe_ratio": float(returns.mean() / volatility * np.sqrt(252)) if volatility > 0 else None,
            "maximum_drawdown": float((equity / equity.cummax().clip(lower=1) - 1).min()),
            "total_transaction_cost": float(daily.transaction_cost.sum()), "total_turnover": float(daily.turnover.sum())}


def publish(strategy, folder, summary_path, daily_path, scope, label, baseline=None, run_folder=None):
    run_folder = run_folder or folder
    if (folder / "publication.json").exists():
        raise FileExistsError("Preserve published evidence; create a new publication folder")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    metrics = summary.get("out_of_sample", summary) if scope == "oos" else summary
    if "full" in metrics:
        metrics = metrics["full"]
    daily = pd.read_csv(daily_path, parse_dates=["date"]).set_index("date")
    if scope == "oos":
        daily = daily.loc["2021-08-23":]
    config = metrics.get("config", summary.get("config", summary.get("execution_config", {})))
    if not config and strategy == "arc":
        # Older ARC summaries omitted the execution config; these are the
        # fixed caller defaults, not estimates from realized cost/turnover.
        config = {"transaction_cost_bps": 5.0, "rebalance_band": .10, "route_cooldown_days": 10,
                  "cost_contract_source": "legacy_fixed_arc_adx_cli"}
    cost_bps = config.get("transaction_cost_bps")
    if cost_bps is None:
        raise ValueError("Publication needs the actual fixed transaction-cost contract")
    annual = annualize_net_returns(daily.portfolio_net_return, scope, f"原固定参数；按成交权重收取{cost_bps:g}bp成本，沿用原成交模型；未另计市场冲击")
    selected = {name: float(metrics[name]) for name in ["total_return", "annualized_return", "sharpe_ratio", "maximum_drawdown", "total_transaction_cost", "total_turnover"] if metrics.get(name) is not None}
    # The defensive CLI exports a full-history summary, not an OOS summary.
    # All displayed OOS statistics must come from the very same daily slice.
    if scope == "oos" and "out_of_sample" not in summary:
        selected.update(net_daily_metrics(daily))
    selected["annualized_return"] = annual["value"]
    refs = summary.get("data_versions", [])
    if not refs and (folder / "manifest.json").exists():
        refs = json.loads((folder / "manifest.json").read_text(encoding="utf-8")).get("data_versions", [])
    comparison = {}
    if baseline and baseline.exists():
        old = json.loads(baseline.read_text(encoding="utf-8"))
        if scope == "oos":
            old = old.get("out_of_sample", old)
        comparison = {k: {"old": old[k], "new": v, "delta": v - float(old[k])} for k, v in selected.items() if isinstance(old.get(k), (float, int))}
    report = f"# 清洗版本固定回测：{label}\n\n区间 {annual['data_start']} 至 {annual['data_end']}；{annual['sessions']} 交易日，范围 {scope}。\n\n参数、成本、候选集均不按新OOS结果选择。旧报告保留。\n\n年化 {annual['value']:.2%}。\n\n输入版本 {len(refs)} 个；缺失行情没有插值补造。\n\n旧结果对照：\n\n```json\n{json.dumps(comparison, ensure_ascii=False, indent=2)}\n```\n"
    provenance_path = run_folder / "code_provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8")) if provenance_path.exists() else {"status": "legacy_start_not_captured", "reason": "本轮迁移回测未在计算前冻结代码指纹；不追认当前提交为回测代码"}
    if provenance.get("source_sha256"):
        provenance["changed_during_run"] = provenance["source_sha256"] != capture_code_provenance()["source_sha256"]
    report += "\n\n代码来源：\n\n```json\n" + json.dumps(provenance, ensure_ascii=False, indent=2) + "\n```\n"
    value = {"id": publication_id(strategy, folder, run_folder), "strategy_id": strategy, "status": "complete", "scope": scope,
             "as_of_date": datetime.now(timezone.utc).date().isoformat(), "data_start": annual["data_start"], "data_end": annual["data_end"],
             "annualization": annual, "metrics": selected, "data_versions": refs, "config": config,
             "config_hash": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
             "code_version": provenance.get("base_commit"), "code_provenance": provenance,
             "source_summary": str(summary_path.relative_to(ROOT)), "source_daily": str(daily_path.relative_to(ROOT)),
             "source_summary_sha256": digest(summary_path), "source_daily_sha256": digest(daily_path),
             "summary": label + "：固定参数、清洗版本、成本后结果", "comparison": comparison, "report_md": report}
    write_json(folder / "publication.json", value)


def turtle_summary(source_folder, output_folder):
    table = pd.read_csv(source_folder / "parameter_results.csv")
    manifest = json.loads((source_folder / "manifest.json").read_text(encoding="utf-8"))
    if len(table) != 6 or manifest["parameter_count"] != 6:
        raise ValueError("All six fixed candidates must finish before publication")
    baseline = table[table.candidate == "baseline_55_20_5"].iloc[0]
    metrics = {k.removeprefix("standalone_out_of_sample_"): v.item() if isinstance(v, np.generic) else v
               for k, v in baseline.items() if k.startswith("standalone_out_of_sample_")}
    summary = {**metrics, "data_versions": manifest["data_versions"], "config": {
        "candidate": "baseline_55_20_5", "entry_days": 55, "exit_days": 20, "max_positions": 5,
        "min_avg_turnover": 10_000_000, "transaction_cost_bps": TurtleConfig.transaction_cost_bps, "all_candidates_preserved": True}}
    write_json(output_folder / "summary.json", summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=["adaptive_defensive_etf", "range_grid", "spy_mean_reversion", "stock_turtle", "arc"], required=True)
    parser.add_argument("--publish-existing", type=Path, help="Publish already-computed artifacts in a new folder without running engines")
    args = parser.parse_args()
    catalog = json.loads((ROOT / "config/strategy_catalog.json").read_text(encoding="utf-8"))["strategies"]
    entry = next(s for s in catalog if s["id"] == args.strategy)
    if entry.get("is_archived") or research_pause(args.strategy, ROOT):
        raise RuntimeError("Research is frozen or paused")
    run_folder = ROOT / "artifacts/reclean" / (args.strategy + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6])
    run_folder.mkdir(parents=True)
    source_folder = run_folder
    prices = "artifacts/research/norgate/etf_prices.csv"
    universe = "artifacts/research/norgate/stock-universe-sp500-liquidity"
    commands = {
        "adaptive_defensive_etf": ["scripts/run_defensive_etf_backtest.py", "--profile", "adaptive", "--prices", prices],
        "range_grid": ["scripts/run_arc_grid_backtest.py", "--prices", prices],
        "spy_mean_reversion": ["scripts/run_spy_mean_reversion_backtest.py", "--prices", prices],
        "stock_turtle": ["-m", "scripts.run_point_in_time_turtle", "--universe-dir", universe, "--etf-prices", prices, "--save-artifacts"],
        "arc": ["-m", "scripts.run_arc_adx_backtest", "--universe-dir", universe, "--etf-prices", prices, "--spy-ohlc", "artifacts/research/norgate/spy_daily_ohlc.csv"],
    }
    if args.publish_existing:
        source_folder = args.publish_existing.resolve()
        if source_folder.parent != run_folder.parent or not source_folder.name.startswith(args.strategy + "-") or args.strategy == "arc":
            raise ValueError("Existing artifacts must be a matching top-level reclean run (ARC requires a new engine run)")
    else:
        write_json(run_folder / "code_provenance.json", capture_code_provenance())
        with (run_folder / "run.log").open("w", encoding="utf-8") as log:
            process = subprocess.run([sys.executable, *commands[args.strategy], "--output-dir", str(run_folder)], cwd=ROOT, stdout=log, stderr=log, check=False)
    if not args.publish_existing and process.returncode:
        value = {"id": "reclean-" + args.strategy + "-" + run_folder.name, "strategy_id": args.strategy, "status": "blocked",
                 "as_of_date": datetime.now(timezone.utc).date().isoformat(), "summary": "清洗版本固定回测阻塞，保留上一成功结果", "metrics": {},
                 "report_md": "# 固定回测阻塞\n\n" + (run_folder / "run.log").read_text(encoding="utf-8")[-4000:]}
        write_json(run_folder / "publication.json", value)
        print(json.dumps({"strategy": args.strategy, "status": "blocked", "folder": str(run_folder)}))
        return
    if args.strategy == "arc":
        for path in run_folder.glob("*/summary.json"):
            publish(args.strategy, path.parent, path, path.with_name("daily_results.csv"), "full", path.parent.name,
                    ROOT / "artifacts/robustness/arc-adx-full-history-2026-09-30" / path.parent.name / "summary.json", run_folder=run_folder)
        # Principal is the preregistered anti-churn controller, not the best rerun.
        principal = run_folder / "adx_14_5_confirm_10_hold_20_reentry/publication.json"
        value = json.loads(principal.read_text(encoding="utf-8"))
        value["report_md"] += "\n\n三种既定控制器全部保留；主展示固定为原已登记抗抖动候选，不按收益切换。"
        write_json(run_folder / "publication.json", value)
    elif args.strategy == "stock_turtle":
        turtle_summary(source_folder, run_folder)
        publish(args.strategy, run_folder, run_folder / "summary.json", source_folder / "baseline_55_20_5_standalone_daily.csv", "oos", "个股海龟固定55/20基线；全部六候选保留", run_folder=source_folder)
    else:
        scope = "full" if args.strategy == "range_grid" else "oos"
        old = ROOT / ("artifacts/robustness/arc-grid-2026-08-22/summary.json" if args.strategy == "range_grid" else "artifacts/robustness/spy-mean-reversion-2026-08-25/summary.json")
        publish(args.strategy, run_folder, source_folder / "summary.json", source_folder / "daily_results.csv", scope, entry["display_name"], old if args.strategy != "adaptive_defensive_etf" else None, run_folder=source_folder)
    print(json.dumps({"strategy": args.strategy, "status": "complete", "folder": str(run_folder)}))


if __name__ == "__main__":
    main()
