"""Fixed US/CN multi-stock diagnostics; all cases including failures retained."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import shutil
import sys
import uuid
import numpy as np
import pandas as pd

from deepstock.data.store import ROOT, digest, input_evidence, read_clean_csv, write_json
from deepstock.strategies.both.granville import VARIANTS, run_granville, summarize, walk_forward
from deepstock.strategies.both.granville_portfolio import PortfolioDataError, make_panel, run_stock_portfolio
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.rerun_clean_research import capture_code_provenance
from scripts.run_granville_backtest import period_metrics


def fixed_config(path):
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if tuple(cfg["variants"]) != VARIANTS or cfg["exit_policies"] != ["time_7", "trend_only"] or cfg["principal_variant"] != "trend_pullback" or cfg["principal_exit_policy"] != "time_7" or cfg["paper_authorized"] or cfg["live_authorized"]:
        raise ValueError("Fixed candidate/principal/authorization boundary changed")
    return cfg


def dataset_csv(data_dir, manifest, name):
    if "input_versions" in manifest:
        # Missing/invalid pins never fall back to a mutable discovery alias.
        if name not in manifest["input_versions"]:
            raise ValueError("Required fixed dataset pin missing: " + name)
        return read_clean_csv(data_dir / name, version=manifest["input_versions"][name])
    return read_clean_csv(data_dir / name)


def load_stock_inputs(market, data_dir, cfg, rule, config_path):
    """Reuse registered evidence; experiments never rewrite collection identity."""
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest["market"] != market or manifest["config_hash"] != digest(config_path):
        raise ValueError("Dataset/config identity differs")
    calendar = pd.DatetimeIndex(pd.to_datetime(dataset_csv(data_dir, manifest, "calendar.csv").date))
    membership = dataset_csv(data_dir, manifest, "membership.csv.gz")
    if manifest["failures"]:
        raise PortfolioDataError(f"Required constituent evidence blocked: {manifest['failures']}")
    bars = pd.concat([dataset_csv(data_dir, manifest, "prices/" + s + ".csv.gz") for s in manifest["symbols"]], ignore_index=True)
    dividends = suspensions = None
    if market == "CN":
        div_dir = data_dir / manifest.get("dividend_audit_directory", "dividends")
        dividends = pd.concat([read_clean_csv(div_dir / (s + ".csv.gz")) for s in manifest["symbols"]], ignore_index=True)
        verified = data_dir / "verified_disclosures.csv.gz"
        if verified.exists():
            dividends = pd.concat([dividends, read_clean_csv(verified)], ignore_index=True)
        suspensions = pd.concat([read_clean_csv(data_dir / "suspensions" / (s + ".csv.gz")) for s in manifest["symbols"]], ignore_index=True)
    terminal = {s: v["last_quote_date"] for s, v in manifest.get("terminal_securities", {}).items()}
    panel = make_panel(bars, calendar, membership, market, rule, cfg, dividends=dividends, suspensions=suspensions, terminal=terminal)
    return panel, manifest


def run(market, data_dir, benchmark_path, output, config_path, benchmark_version=None):
    cfg = fixed_config(config_path)
    output.mkdir(parents=True, exist_ok=False)
    code = capture_code_provenance()
    if code["tracked_dirty"]:
        raise ValueError("Committed clean source required")
    dataset_hash = digest(data_dir / "manifest.json")
    config_hash = digest(config_path)
    source_names = ["scripts/run_granville_portfolio.py", "scripts/prepare_granville_stock_data.py",
                    "src/deepstock/strategies/both/granville_portfolio.py", "src/deepstock/strategies/both/granville.py",
                    "scripts/prepare_granville_us_inventory.py", "scripts/download_norgate_membership.py", "src/deepstock/data/membership_contract.py",
                    "src/deepstock/data/store.py", "src/deepstock/data/stock_actions.py", "src/deepstock/data/membership.py", cfg["signal_config"], "pyproject.toml"]
    code["source_file_hashes"] = {name: digest(ROOT / name) for name in source_names}
    for name in source_names:
        target = output / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    shutil.copyfile(config_path, output / "fixed_config.json")
    write_json(output / "start_provenance.json", code)
    rule = json.loads((ROOT / cfg["signal_config"]).read_text(encoding="utf-8"))
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest["market"] != market or manifest["config_hash"] != digest(config_path):
        raise ValueError("Dataset/config identity differs")
    calendar = pd.DatetimeIndex(pd.to_datetime(dataset_csv(data_dir, manifest, "calendar.csv").date))
    membership = dataset_csv(data_dir, manifest, "membership.csv.gz")
    result = {"market": market, "symbol": cfg["markets"][market]["universe"], "currency": cfg["markets"][market]["currency"],
              "principal_variant": cfg["principal_variant"], "principal_exit_policy": cfg["principal_exit_policy"],
              "cost_basis": {**cfg["markets"][market], "price_model": cfg["price_model"]}, "cases": [], "benchmarks": {},
              "metrics": None, "benchmark_metrics": None, "matched_benchmark_metrics": None,
              "universe_count": manifest["symbol_count"], "membership_model": manifest["membership_model"],
              "diagnostics": {"walk_forward_windows": 0, "negative_windows": 0,
                              "walk_forward_status": "insufficient_history_for_504_plus_252_sessions"}}
    load_error = None
    try:
        if manifest["failures"]:
            raise PortfolioDataError(f"Required constituent evidence blocked: {manifest['failures']}")
        bars = pd.concat([dataset_csv(data_dir, manifest, "prices/" + s + ".csv.gz") for s in manifest["symbols"]], ignore_index=True)
        dividends = suspensions = None
        if market == "CN":
            div_dir = data_dir / manifest.get("dividend_audit_directory", "dividends")
            dividends = pd.concat([read_clean_csv(div_dir / (s + ".csv.gz")) for s in manifest["symbols"]], ignore_index=True)
            verified = data_dir / "verified_disclosures.csv.gz"
            if verified.exists():
                dividends = pd.concat([dividends, read_clean_csv(verified)], ignore_index=True)
            suspensions = pd.concat([read_clean_csv(data_dir / "suspensions" / (s + ".csv.gz")) for s in manifest["symbols"]], ignore_index=True)
        terminal = {s: v["last_quote_date"] for s, v in manifest.get("terminal_securities", {}).items()}
        panel = make_panel(bars, calendar, membership, market, rule["signal"], cfg, dividends=dividends, suspensions=suspensions, terminal=terminal)
        result["data_audit"] = panel.diagnostics
    except (PortfolioDataError, ValueError) as error:
        load_error = str(error)
        result["data_audit"] = {"status": "blocked", "reason": load_error}
    # Benchmarks use the approved ETF accounting, not stock stamp/dividend taxes.
    bench_cfg = json.loads((ROOT / cfg["signal_config"]).read_text(encoding="utf-8"))
    for field in ["evaluation_start", "evaluation_end", "diagnostic_split", "initial_capital"]:
        bench_cfg[field] = cfg[field]
    if market == "CN":
        benchmark_bars = read_clean_csv(benchmark_path / "daily.csv")
        benchmark_div = read_clean_csv(benchmark_path / "dividends.csv")
    else:
        benchmark_bars = read_clean_csv(benchmark_path, version=benchmark_version) if benchmark_version is not None else read_clean_csv(benchmark_path)
        for field in ["open", "high", "low", "close"]:
            benchmark_bars[field] = benchmark_bars["adjusted_" + field]
        benchmark_div = None
    benchmark_bars.index = pd.DatetimeIndex(pd.to_datetime(benchmark_bars.date))
    # The existing ETF export is longer than this experiment. Explicitly trim
    # outside-source history, not missing/internal market sessions.
    benchmark_bars = benchmark_bars.loc[(benchmark_bars.index >= calendar[0]) & (benchmark_bars.index <= calendar[-1])].copy()
    bench_calendar = calendar[(calendar >= pd.Timestamp(cfg["evaluation_start"])) & (calendar <= pd.Timestamp(cfg["evaluation_end"]))]
    for stress in [False, True]:
        cost = "stress" if stress else "base"
        result["benchmarks"][cost] = {}
        benchmark_daily = None
        for exposure in [1., .8]:
            name = f"buy_hold_{round(exposure * 100)}"
            benchmark = run_granville(benchmark_bars, calendar, bench_cfg, market, "buy_hold", stress=stress,
                                     dividends=benchmark_div, benchmark_exposure=exposure)
            if not benchmark.daily.index.equals(bench_calendar):
                raise ValueError("Benchmark session mismatch")
            result["benchmarks"][cost][name] = period_metrics(benchmark.daily, cfg["diagnostic_split"])
            benchmark.daily.to_csv(output / f"{name}-{cost}-daily.csv")
            benchmark.trades.to_csv(output / f"{name}-{cost}-trades.csv", index=False)
            if exposure == 1.:
                benchmark_daily = benchmark.daily
        for variant in cfg["variants"]:
            for exit_policy in cfg["exit_policies"]:
                case = {"variant": variant, "exit_policy": exit_policy, "cost_case": cost, "status": "blocked",
                        "periods": {}, "walk_forward": []}
                try:
                    if load_error:
                        raise PortfolioDataError(load_error)
                    daily, trades, summary = run_stock_portfolio(panel, cfg, rule["signal"], market, variant, exit_policy, stress)
                    if not daily.index.equals(bench_calendar):
                        raise ValueError("Continuous stock/benchmark scope mismatch")
                    daily.to_csv(output / f"{variant}-{exit_policy}-{cost}-daily.csv")
                    trades.to_csv(output / f"{variant}-{exit_policy}-{cost}-trades.csv", index=False)
                    case.update(status="completed", periods=period_metrics(daily, cfg["diagnostic_split"]),
                                walk_forward=walk_forward(daily, benchmark_daily, cfg["walk_forward"]), diagnostics=summary)
                except PortfolioDataError as error:
                    case["blocking_reason"] = str(error)
                result["cases"].append(case)
                print(json.dumps({"market": market, "candidate": f"{variant}/{exit_policy}/{cost}", "status": case["status"],
                                  "reason": case.get("blocking_reason"), "metrics": case.get("periods", {}).get("full")}), flush=True)
                if variant == cfg["principal_variant"] and exit_policy == cfg["principal_exit_policy"] and not stress:
                    result["principal_status"] = case["status"]
                    result["metrics"] = case["periods"].get("full")
                    result["holdout"] = case["periods"].get("retrospective_holdout")
                    result["benchmark_metrics"] = result["benchmarks"][cost]["buy_hold_100"]["full"]
                    result["matched_benchmark_metrics"] = result["benchmarks"][cost]["buy_hold_80"]["full"]
                    if case["status"] == "completed":
                        result["diagnostics"].update(case["diagnostics"])
                        result["diagnostics"].update(walk_forward_windows=len(case["walk_forward"]),
                                                      negative_windows=sum(w["total_return"] < 0 for w in case["walk_forward"]))
                    else:
                        result["diagnostics"]["blocking_reason"] = case["blocking_reason"]
    result["status"] = "completed" if all(c["status"] == "completed" for c in result["cases"]) else "completed_with_blocks"
    result["config"] = cfg
    result["config_hash"] = digest(config_path)
    result["code_provenance"] = code
    result["dataset_manifest_sha256"] = dataset_hash
    result["fresh_membership_capture_version"] = manifest.get("fresh_membership_capture_version")
    result["fresh_membership_manifest_sha256"] = manifest.get("fresh_membership_manifest_sha256")
    result["price_inventory_version"] = manifest.get("price_inventory_version")
    result["price_inventory_manifest_sha256"] = manifest.get("price_inventory_manifest_sha256")
    result["membership_contract"] = manifest.get("membership_contract")
    result["benchmark_version"] = benchmark_bars.attrs.get("data_version")
    result["artifact_hashes"] = {p.name: digest(p) for p in output.glob("*.csv")}
    result["runtime"] = {"numpy": np.__version__, "pandas": pd.__version__}
    refs = [*manifest.get("data_versions", []), *input_evidence()["data_versions"]]
    result["data_versions"] = list({r["version"]: r for r in refs}.values())
    result["input_exclusions"] = input_evidence()["input_exclusions"]
    if (capture_code_provenance()["source_sha256"] != code["source_sha256"]
            or digest(data_dir / "manifest.json") != dataset_hash or digest(config_path) != config_hash):
        raise ValueError("Source/dataset/config changed during fixed run")
    write_json(output / "market_summary.json", result)
    return result


def render_report(value):
    correction = value.get("publication_scope") == "US_effective_membership_correction_v1"
    lines = ["# 葛兰威尔多股票组合：美股有效日成员修正" if correction else "# 葛兰威尔多股票组合：美股与A股", "", value["summary"], "",
             "预先指定主展示 trend_pullback / time_7。最多5只，每个新仓目标16%，开仓总暴露上限80%；价格漂移不强制调仓。",
             "按前收盘126日强度排序，只补空位，不按每日排名换仓。先卖后买，失败卖出不释放仓位或现金。",
             "三类入场 × 两类固定退出 × 基础/10bp滑点压力 = 每市场12组，全部保留，不按收益挑选赢家。",
             "MA20/60/200、ATR14；前收盘信号下一合法开盘成交，8%收盘止损。time_7第7日收盘发退出，trend_only不设时间退出。",
             "普通退出最少持有3日，逐股冷却3日。入场原始整手参考US1股/CN100股，前日成交量1%容量。",
             "US：佣金2.5bp/最低USD1、滑点2.5bp；CN：佣金3bp/最低CNY5、滑点5bp、股票卖出印花税5bp、双边过户0.1bp。现金利息0。",
             "使用复权经济收益分析单位，不是实际股数/分红到账/税务结算执行复刻。CN固定扣除持仓除息现金分红20%，不重复添加复权分红现金；US个人预扣/资本利得税未计。",
             "US历史S&P500区间；CN滞后月末CSI300快照代理，不能声称精确每日成员或首见发布时间。无历史行业上限证据。",
             "共同评估2025-01-02—2026-09-29；2026-01-02后仅回溯诊断，不是前瞻OOS。样本不足504+252，不虚构Walk-Forward窗口。", ""]
    if correction:
        us = value["market_results"]["US"]
        lines += ["本轮仅重跑美股原v1的12组，不重跑或复制A股，不重跑v2/v3优化。旧报告/账本及其数据缺陷告警仍保留；本报告不是对旧绩效的追认。",
                  "新版成员为供应商按生效日评估的ALLMARKETDAYS序列，不含公告日期；股票价格仍为NONE，不填充报价。",
                  f"成员版本 `{us['fresh_membership_capture_version']}`；价格库存版本 `{us['price_inventory_version']}`。",
                  f"新固定区间输入SHA `{us['dataset_manifest_sha256']}`；起始代码 `{us['code_provenance']['base_commit']}`。", ""]
    for market, result in value["market_results"].items():
        lines += [f"## {market} · {result['universe_count']}只历史成员 · {result['currency']}", "",
                  "|入场 / 退出 / 成本|累计|年化|Sharpe|回撤|年化换手|平均暴露|成交成本金额（含税）|", "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for case in result["cases"]:
            label = f"{case['variant']} / {case['exit_policy']} / {case['cost_case']}"
            if case["status"] != "completed":
                lines.append(f"|{label}|阻塞：{case['blocking_reason']}|—|—|—|—|—|—|")
                continue
            m = case["periods"]["full"]
            sharpe = "—" if m["sharpe_ratio"] is None else f"{m['sharpe_ratio']:.2f}"
            charges = m["commission"] + m["slippage"] + case["diagnostics"]["dividend_tax"]
            lines.append(f"|{label}|{m['total_return']:.2%}|{m['annualized_return']:.2%}|{sharpe}|{m['maximum_drawdown']:.2%}|{m['annualized_turnover']:.2f}|{m['average_exposure']:.2%}|{charges:.2f}|")
        for cost, benchmarks in result["benchmarks"].items():
            for name, periods in benchmarks.items():
                m = periods["full"]
                lines.append(f"|{result['cost_basis']['benchmark']} {name} / {cost}|{m['total_return']:.2%}|{m['annualized_return']:.2%}|{m['sharpe_ratio']:.2f}|{m['maximum_drawdown']:.2%}|{m['annualized_turnover']:.2f}|{m['average_exposure']:.2%}|{m['commission']+m['slippage']:.2f}|")
        lines += ["", "### 回溯留出：全部候选", "", "|入场 / 退出 / 成本|累计|年化|回撤|", "|---|---:|---:|---:|"]
        for case in result["cases"]:
            m = case["periods"].get("retrospective_holdout")
            if m:
                lines.append(f"|{case['variant']} / {case['exit_policy']} / {case['cost_case']}|{m['total_return']:.2%}|{m['annualized_return']:.2%}|{m['maximum_drawdown']:.2%}|")
        diag = {k: v for k, v in result["diagnostics"].items() if k not in {"quantity_unit"}}
        lines += ["", "### 固定主展示诊断", "", "```json", json.dumps(diag, ensure_ascii=False, indent=2), "```", "",
                  "基准采用原ETF账户口径：US总收益分析单位；CN原始价、现金分红、ETF税费及整手，不与股票所得税口径混同。", ""]
    lines += ["## 证据与后续", "", f"配置SHA256 `{value['config_hash']}`；各节点起始代码、输入版本及账本哈希在结构化结果内。",
              "所有未完成候选保留阻塞原因；未知价不插值，真实终止证券缺兑付证据不得虚构成交。",
              "下一步需更长点时历史、退市/合并兑付、历史行业分类、真实股数/结算账本以及前瞻验证；新实验另行固定，不能从本轮反选参数。",
              "本次仅研究，未创建影子任务、Paper订单或实盘权限。"]
    if correction:
        us = value["market_results"]["US"]
        completed = [c for c in us["cases"] if c["status"] == "completed"]
        negative = sum(c["periods"]["full"]["total_return"] < 0 for c in completed)
        lines += ["", "## 本轮判断（不选参）", "",
                  f"12组中{len(completed)}组完成、{12-len(completed)}组阻塞；完成组中{negative}组累计为负。失败组没有补造净值。",
                  "报价入口通过不等于公司行动/实盘账本全部完备。允许的入池前暖期缺报价保持NaN，连续指标就绪前不产生信号。"]
        for gap in us.get("data_audit", {}).get("pre_eligibility_incomplete_warmup", []):
            lines += [f"{gap['symbol']}：{gap['missing_sessions']}个入池前暖期缺报价日（{gap['first']}—{gap['last']}），首个成员生效日{gap['first_eligible']}；没有补造价格。"]
        if us["metrics"]:
            m = us["metrics"]
            stress = next(c for c in us["cases"] if (c["variant"], c["exit_policy"], c["cost_case"]) == ("trend_pullback", "time_7", "stress"))
            full, matched = us["benchmark_metrics"], us["matched_benchmark_metrics"]
            lines += [f"固定主候选年化{m['annualized_return']:.2%}，100% SPY基准{full['annualized_return']:.2%}，80%初始暴露基准{matched['annualized_return']:.2%}；",
                      f"相应回撤为{m['maximum_drawdown']:.2%} / {full['maximum_drawdown']:.2%} / {matched['maximum_drawdown']:.2%}，Sharpe为{m['sharpe_ratio']:.2f} / {full['sharpe_ratio']:.2f} / {matched['sharpe_ratio']:.2f}。",
                      f"主候选年化换手{m['annualized_turnover']:.2f}、平均暴露{m['average_exposure']:.2%}，累计佣金与滑点{m['commission']+m['slippage']:.2f} USD。成本金额不等于复利收益损失。"]
            if stress["status"] == "completed":
                lines += [f"固定10bp压力滑点下，主候选年化降至{stress['periods']['full']['annualized_return']:.2%}，不能忽略成交成本敏感性。"]
        lines += ["WBA现金/或有权利及结算仍不完整；504+252日窗口为0，2026年留出已见，不是前瞻OOS。",
                  "后续先补终止兑付、历史行业/上市资格与长历史固定滚动验证，再讨论单项改进；当前不改参数、不晋升、不进入Paper。",
                  "完整逐日/逐笔账本和定仓明细留在量化电脑。网页只发布聚合指标、计数、固定输入版本及源结果哈希。"]
    return "\n".join(lines) + "\n"


def publish(us_path, cn_path, output, config_path):
    cfg = fixed_config(config_path)
    results = {"US": json.loads(us_path.read_text(encoding="utf-8")), "CN": json.loads(cn_path.read_text(encoding="utf-8"))}
    for market, result in results.items():
        # Git's Windows CRLF checkout changes byte hashes without changing any
        # parameter. Compare the full parsed contract, retain every raw hash.
        if result["market"] != market or result["config"] != cfg:
            raise ValueError("Cannot merge different market/config versions")
    output.mkdir(parents=True, exist_ok=False)
    value = {"id": "granville-stocks-" + output.name, "strategy_id": cfg["strategy_id"],
             "status": "completed" if all(r["status"] == "completed" for r in results.values()) else "completed_with_blocks",
             "as_of_date": datetime.now(timezone.utc).date().isoformat(), "data_start": cfg["evaluation_start"], "data_end": cfg["evaluation_end"],
             "config": cfg, "config_hash": digest(config_path), "code_version": None, "market_results": results,
             "config_semantic_hash": hashlib.sha256(json.dumps(cfg, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
             "source_config_hashes": {market: result["config_hash"] for market, result in results.items()},
             "summary": "双市场多股票组合固定诊断：最多5只、卖出后补位，24组全部保留；回溯诊断，未授权交易。",
             "source_summary_hashes": {"US": digest(us_path), "CN": digest(cn_path)},
             "data_versions": list({v["version"]: v for r in results.values() for v in r["data_versions"]}.values()),
             "input_exclusions": [v for r in results.values() for v in r["input_exclusions"]]}
    for market, source in [("US", us_path), ("CN", cn_path)]:
        shutil.copyfile(source, output / (market + "-market_summary.json"))
    value["report_md"] = render_report(value)
    (output / "report.md").write_text(value["report_md"], encoding="utf-8")
    write_json(output / "publication.json", value)
    return value


def publish_corrected_us(us_path, output, config_path):
    """Separate US evidence; never attach stale CN or pick another principal."""
    cfg = fixed_config(config_path)
    us = json.loads(us_path.read_text(encoding="utf-8"))
    from deepstock.data.membership_contract import load_effective_membership_contract
    contract, _ = load_effective_membership_contract()
    if (us.get("market") != "US" or us.get("config") != cfg or us.get("membership_contract") != contract or
            not us.get("fresh_membership_capture_version") or not us.get("price_inventory_version")):
        raise ValueError("Corrected US publication needs fixed config and effective/inventory provenance")
    if any("sizing_audit" in d for d in [us.get("diagnostics", {}), *(c.get("diagnostics", {}) for c in us["cases"])]):
        raise ValueError("Export aggregate summary first; per-entry audits stay on licensed node")
    output.mkdir(parents=True, exist_ok=False)
    value = {"id": "granville-stocks-" + output.name, "strategy_id": cfg["strategy_id"], "status": us["status"],
             "publication_scope": "US_effective_membership_correction_v1", "market_results": {"US": us},
             "as_of_date": datetime.now(timezone.utc).date().isoformat(), "data_start": cfg["evaluation_start"], "data_end": cfg["evaluation_end"],
             "config": cfg, "config_hash": digest(config_path), "code_version": us["code_provenance"]["base_commit"],
             "config_semantic_hash": hashlib.sha256(json.dumps(cfg, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
             "source_config_hashes": {"US": us["config_hash"]}, "source_summary_hashes": {"US": digest(us_path)},
             "summary": "仅美股有效日成员修正：原v1的12组及真实阻塞全部保留，主展示不变；A股未重跑，旧报告不改写。仅回溯研究，未授权交易。",
             "data_versions": us["data_versions"], "input_exclusions": us["input_exclusions"]}
    shutil.copyfile(us_path, output / "US-market_summary.json")
    value["report_md"] = render_report(value)
    (output / "report.md").write_text(value["report_md"], encoding="utf-8")
    write_json(output / "publication.json", value)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=["US", "CN"])
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--benchmark", type=Path)
    parser.add_argument("--benchmark-version", help="Pin a registered US benchmark; no alias fallback")
    parser.add_argument("--us-summary", type=Path)
    parser.add_argument("--cn-summary", type=Path)
    parser.add_argument("--corrected-us-summary", type=Path, help="Separate effective-member v1 correction; do not merge stale CN")
    parser.add_argument("--config", type=Path, default=ROOT / "config/granville_portfolio_v1.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/research/granville-stocks" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]))
    args = parser.parse_args()
    if args.corrected_us_summary:
        if args.us_summary or args.cn_summary or args.market:
            parser.error("US correction cannot merge markets or launch a new run")
        result = publish_corrected_us(args.corrected_us_summary, args.output_dir, args.config)
    elif args.us_summary and args.cn_summary:
        result = publish(args.us_summary, args.cn_summary, args.output_dir, args.config)
    elif args.market and args.data_dir and args.benchmark:
        result = run(args.market, args.data_dir, args.benchmark, args.output_dir, args.config, args.benchmark_version)
    else:
        parser.error("Need market/data-dir/benchmark, or both market summaries")
    print(json.dumps({"status": result["status"], "output": str(args.output_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
