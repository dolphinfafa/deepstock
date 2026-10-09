"""Append-only aggregate report linked to an existing run; creates no metrics."""
from __future__ import annotations

import hashlib
import json

from deepstock.web.models import ResearchReport, ResearchRun

REPORT_TYPE = "granville_us_ledger_history_audit"
TOP_KEYS = {"id", "strategy_id", "report_type", "as_of_date", "source_run_id", "source_run_sha256", "source_summary_sha256",
            "config", "code_provenance", "cycles_sha256", "captured_at_utc", "ledgers", "history", "diagnostic_only",
            "new_backtest", "paper_authorized", "live_authorized"}
SECURITY_KEYS = {"symbol", "assetid", "first_quote", "last_quote", "quote_lifetime_outside_scope", "outside_quote_lifetime_sessions",
                 "pre_member_warmup_outside_lifetime_sessions", "effective_member_sessions", "observed_price_sessions",
                 "missing_price_sessions", "member_missing", "pre_member_warmup_missing", "other_internal_missing", "unknown_membership",
                 "status", "blocking_reason", "member_version", "price_version", "metadata_version", "price_raw_sha256", "price_clean_sha256", "member_raw_sha256", "member_clean_sha256"}


def validate_publication(value, cfg):
    if (set(value) != TOP_KEYS or value["strategy_id"] != "granville_stock_portfolio" or value["report_type"] != REPORT_TYPE or
            value["config"] != cfg or not value["diagnostic_only"] or value["new_backtest"] or
            value["paper_authorized"] or value["live_authorized"]):
        raise ValueError("Invalid diagnostic-only fixed publication boundary")
    for key in ["source_run_sha256", "source_summary_sha256", "cycles_sha256"]:
        if len(value[key]) != 64 or any(c not in "0123456789abcdef" for c in value[key]):
            raise ValueError("Invalid immutable audit hash")
    cases = value["ledgers"]["cases"]
    expected = {(v, e, c) for v in cfg["variants"] for e in cfg["exit_policies"] for c in ["base", "stress"]}
    if len(cases) != 12 or {(c["variant"], c["exit_policy"], c["cost_case"]) for c in cases} != expected:
        raise ValueError("All twelve original cases required")
    for case in cases:
        if set(case).difference({"variant", "exit_policy", "cost_case", "source_status", "status", "blocking_reason", "diagnostics", "artifact_hashes"}):
            raise ValueError("Nonaggregate ledger publication")
        if case["status"] not in {"diagnosed", "source_blocked", "diagnostic_failed"}:
            raise ValueError("Invalid diagnostic status")
        if case["status"] != "diagnosed" and (not case.get("blocking_reason") or "diagnostics" in case):
            raise ValueError("Failed audit cannot fabricate statistics")
        if case["source_status"] not in {"completed", "blocked"} or (case["source_status"] == "blocked") != (case["status"] == "source_blocked"):
            raise ValueError("Original case status changed")
        if case["status"] == "diagnosed":
            stats = case["diagnostics"]
            if set(stats["periods"]) != {"full", "2025", "2026_ytd"} or not stats["verification"]["marks_verified"]:
                raise ValueError("Full/year/marked-account audit required")
    pairs = value["ledgers"]["cost_path_pairs"]
    if len(pairs) != 6 or {(p["variant"], p["exit_policy"]) for p in pairs} != {(v, e) for v in cfg["variants"] for e in cfg["exit_policies"]}:
        raise ValueError("All actual-path cost pairs required")
    history = value["history"]
    history_keys = {"start", "end", "calendar", "sessions", "calendar_sha256", "historical_list_count", "membership_version",
                    "membership_manifest_sha256", "inventory_version", "inventory_manifest_sha256", "membership_contract", "coverage_totals",
                    "securities", "verification_failures", "outside_quote_lifetime_count", "terminal_evidence_gaps", "plan", "research_input_status",
                    "execution_evidence_gaps", "full_strategy_admitted", "strategy_run", "prices_filled"}
    if set(history).difference(history_keys):
        raise ValueError("Nonaggregate long-history publication")
    if (history["start"] != "2005-01-01" or history["end"] != "2026-09-29" or history["historical_list_count"] != 1305 or
            history["strategy_run"] or history["full_strategy_admitted"] or history["prices_filled"]):
        raise ValueError("History scope/admission boundary changed")
    rows = history["securities"]
    if len(rows) != 1305 or len({r["symbol"] for r in rows}) != 1305 or any(set(r).difference(SECURITY_KEYS) for r in rows):
        raise ValueError("Full historical identity or aggregate security schema differs")
    plan = history["plan"]
    if [plan[k] for k in ["warmup_sessions", "history_sessions", "test_sessions", "step_sessions"]] != [252, 504, 252, 252] or plan["strategy_run"]:
        raise ValueError("Fixed unexecuted history windows required")
    for window in [*plan["windows"], *([plan["tail"]] if plan["tail"] else [])]:
        if window["strategy_run"] or window["terminal_holdings_verified"]:
            raise ValueError("Unexecuted window cannot claim terminal-holding validation")
    # No licensed rows, cycles, per-entry audits or price payloads, even nested.
    forbidden = {"trades", "daily", "cycles", "sizing_audit", "analytical_execution_price", "analytical_units", "cash_after",
                 "reference_entry_price", "adjusted_close", "adjusted_open", "open", "high", "low", "close", "volume"}
    def walk(item):
        if isinstance(item, dict):
            if forbidden.intersection(item):
                raise ValueError("Restricted ledger/price details cannot be published")
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
    walk(value)
    def no_rows(item):
        if isinstance(item, list):
            raise ValueError("Only aggregate diagnostic fields may be published")
        if isinstance(item, dict):
            for child in item.values():
                no_rows(child)
    for case in cases:
        if case["status"] == "diagnosed":
            no_rows(case["diagnostics"])
    json.dumps(value, allow_nan=False)


def render_report(value):
    def pct(x):
        return "—" if x is None else f"{x:.2%}"
    def num(x):
        return "—" if x is None else f"{x:.2f}"
    label = {"time_exit": "时间退出", "signal_exit": "趋势/回归信号退出", "close_stop": "收盘止损"}
    cases = value["ledgers"]["cases"]
    history = value["history"]
    lines = ["# 美股葛兰威尔：成交成本、退出归因与长历史准入检查", "",
             "**仅诊断，未做新回测。** 原12组全部保留，主展示仍为趋势回踩＋7日退出；A股未处理。固定参数、旧年化、旧报告和交易授权不变。", "",
             f"关联修正运行：`{value['source_run_id']}`。账户、费用和盈亏允许最多USD0.01误差；失败案例不补造统计。", "",
             "## 12组审计状态", "", "|入场|退出|成本|原运行|本轮诊断|阻塞原因|", "|---|---|---|---|---|---|"]
    for c in cases:
        lines.append(f"|{c['variant']}|{c['exit_policy']}|{c['cost_case']}|{c['source_status']}|{c['status']}|{c.get('blocking_reason', '—')}|")
    lines += ["", "## 账户表现与现金日期费用（USD）", "",
              "分段收益由连续账户净值计算，2026年从2025年末净值开始；已实现盈亏不替代账户收益。分段年化仅为原账本诊断，其中不足252日为短样本参考。", "",
              "|候选/退出/成本|区间|交易日|期初净值|期末净值|累计收益|年化|回撤|佣金|滑点|", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for c in cases:
        if c["status"] != "diagnosed":
            continue
        for name, part in c["diagnostics"]["periods"].items():
            a, f = part["account"], part["fees_paid"]
            lines.append(f"|{c['variant']}/{c['exit_policy']}/{c['cost_case']}|{name}: {a['start']}—{a['end']}|{a['trading_days']}|{num(part['opening_nav'])}|{num(part['closing_nav'])}|{pct(a['total_return'])}|{pct(a['annualized_return'])}|{pct(a['maximum_drawdown'])}|{num(f['commission'])}|{num(f['slippage'])}|")
    lines += ["", "## 完整持仓周期的退出归因（USD）", "",
              "部分卖出按实际卖出数量分摊入场佣金和滑点；每个完整周期按最终平仓日及最终退出原因只计一次。跨年周期的全部费用归于最终退出分段，因此这里的费用贡献不同于上表按现金日期扣款的费用。扣费前盈亏使用实际路径的未加滑点参考成交额，扣费后包括入场/退出佣金与滑点；美股个人税未计。", "",
              "|候选/退出/成本|分段|最终退出原因|周期数|平均/中位持有日|扣费前/后胜率|扣费前盈亏|扣费后盈亏|佣金贡献|滑点贡献|费用使盈利转非盈利次数|",
              "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for c in cases:
        if c["status"] != "diagnosed":
            continue
        for name, part in c["diagnostics"]["periods"].items():
            for reason, s in part["exit_reasons"].items():
                lines.append(f"|{c['variant']}/{c['exit_policy']}/{c['cost_case']}|{name}|{label[reason]}|{s['closed_positions']}|{num(s['mean_holding_sessions'])}/{num(s['median_holding_sessions'])}|{pct(s['gross_win_fraction'])}/{pct(s['net_win_fraction'])}|{num(s['gross_realized_pnl'])}|{num(s['net_realized_pnl'])}|{num(s['commission'])}|{num(s['slippage'])}|{s['gross_profit_to_nonpositive_net_count']}|")
    lines += ["", "## 期末未平仓（单独列示，不强制清仓）", "",
              "未平仓已发生的全部费用包含尚未分摊给卖出的入场费用；部分卖出已实现盈亏和剩余仓位毛浮盈分别展示。", "",
              "|候选/退出/成本|未平仓数|部分卖出笔数|已实现净盈亏|剩余毛浮盈|已发生佣金|已发生滑点|未分摊入场佣金/滑点|", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for c in cases:
        if c["status"] == "diagnosed":
            s = c["diagnostics"]["open_positions"]
            lines.append(f"|{c['variant']}/{c['exit_policy']}/{c['cost_case']}|{s['count']}|{s['sell_fills']}|{num(s['net_realized_pnl'])}|{num(s['gross_unrealized_pnl'])}|{num(s['commission'])}|{num(s['slippage'])}|{num(s['unallocated_entry_commission'])}/{num(s['unallocated_entry_slippage'])}|")
    lines += ["", "## 基础与10bp压力：各自真实路径", "",
              "累计净收益差＝持仓路径的已实现＋期末标记毛盈亏变化－直接费用变化。以下均为初始资金比例；不把全部差额解释为滑点扣款，不估算零成本策略年化。", "",
              "|入场/退出|压力－基础净收益|额外直接费用|路径毛盈亏变化|状态|", "|---|---:|---:|---:|---|"]
    for p in value["ledgers"]["cost_path_pairs"]:
        lines.append(f"|{p['variant']}/{p['exit_policy']}|{pct(p.get('stress_minus_base_net_return'))}|{pct(p.get('additional_direct_cost_initial_capital_ratio'))}|{pct(p.get('changed_position_path_gross_pnl_initial_capital_ratio'))}|{p['status']}|")
    totals = history["coverage_totals"]
    lines += ["", "## 2005-01-01—2026-09-29长历史准入", "",
              f"全部{history['historical_list_count']}代码保留，{history['sessions']}个XNYS交易日；{history['outside_quote_lifetime_count']}只报价生命期在区间外。按历史身份逐证券核对；未使用当前538只股票作为长历史固定池。研究输入状态：**{history['research_input_status']}**。", "",
              f"缺报价证券日期：有效成员期间{totals['member_missing']}，入池前暖期{totals['pre_member_warmup_missing']}，其他生命期内缺报价{totals['other_internal_missing']}；未知成员状态{totals['unknown_membership']}。这些是证券日期数，允许同一市场日期对应多只证券。报价生命期之外另列，不制造价格或延长成员状态。", "",
              "研究输入阻塞包括有效成员缺报价、未知成员身份和核验失败。暖期缺口仍保留，未来只能在连续指标就绪后产生信号；本轮未计算信号。行业、上市资格及终止兑付属于独立执行证据不足，不能因覆盖核对或库存清洗通过而解除。", "",
              "## 固定窗口清单（仅覆盖计划）", "",
              f"最初252个交易日暖期：{history['plan']['warmup']['start']}—{history['plan']['warmup']['end']}。之后504日历史、252日测试、252日步进；末尾不足252日仅列尾段。", "",
              "|类型|504日历史|测试区间|测试日数|历史/测试成员缺报价|历史/测试未知成员|覆盖状态|", "|---|---|---|---:|---:|---:|---|"]
    windows = [("完整", w) for w in history["plan"]["windows"]] + ([("尾段", history["plan"]["tail"])] if history["plan"]["tail"] else [])
    for kind, w in windows:
        h, t = w["history"], w["test"]
        lines.append(f"|{kind}|{h['start']}—{h['end']}|{t['start']}—{t['end']}|{t['sessions']}|{h['coverage']['member_missing']}/{t['coverage']['member_missing']}|{h['coverage']['unknown_membership']}/{t['coverage']['unknown_membership']}|{w['status']}|")
    lines += ["", "未运行长历史策略，不能断言任一窗口实际持有终止证券，也不能声称窗口已通过策略或执行验证。", "", "## 独立证据缺口", ""]
    lines += ["- " + gap for gap in history["execution_evidence_gaps"]]
    lines += ["", "历史有效成员中报价已终止、兑付未验证的证券（只是证据清单，不代表实际持有）：", "",
              "|证券|最后报价|实际持有检查|兑付检查|", "|---|---|---|---|"]
    lines += [f"|{r['symbol']}|{r['last_quote']}|未运行策略|缺独立完整证据|" for r in history["terminal_evidence_gaps"]]
    lines += ["", "WBA现金＋或有权利的完整估值/结算缺口继续保留，现有两组WBA阻塞不由长历史检查替代。", "",
              "## 全部证券覆盖清单", "", "价格与逐日成员序列留在量化电脑；这里只列身份、计数、阻塞和版本。成员/暖期/其他三列均为生命期内缺报价。", "",
              "|证券/assetid|报价生命期|有效成员日|观察报价日|成员缺价|暖期缺价|其他缺价|未知成员|状态|成员版本|价格版本|",
              "|---|---|---:|---:|---:|---:|---:|---:|---|---|---|"]
    for r in history["securities"]:
        def ref(key):
            version = r.get(key)
            return f"[{version[:12]}](/deepstock/data?version={version})" if version else "—"
        lines.append(f"|{r['symbol']}/{r['assetid']}|{r['first_quote']}—{r['last_quote'] or '未终止'}|{r.get('effective_member_sessions', '—')}|{r.get('observed_price_sessions', '—')}|{r.get('member_missing', '—')}|{r.get('pre_member_warmup_missing', '—')}|{r.get('other_internal_missing', '—')}|{r.get('unknown_membership', '—')}|{r['status']}: {r.get('blocking_reason', '')}|{ref('member_version')}|{ref('price_version')}|")
    lines += ["", "## 不可变版本与哈希", "",
              f"原运行发布SHA256：`{value['source_run_sha256']}`；原始修正结果SHA256：`{value['source_summary_sha256']}`。",
              f"成员版本：`{history['membership_version']}`；清单SHA256：`{history['membership_manifest_sha256']}`。",
              f"原始/总收益库存版本：`{history['inventory_version']}`；清单SHA256：`{history['inventory_manifest_sha256']}`。",
              f"XNYS日期清单SHA256：`{history['calendar_sha256']}`；受限持仓周期明细SHA256：`{value['cycles_sha256']}`。",
              f"诊断源码：`{value['code_provenance']['base_commit']}`；源码指纹：`{value['code_provenance']['source_sha256']}`。",
              "完整逐日净值、逐笔成交、OHLC、日度成员、持仓周期均留量化电脑。聚合发布文件另绑定逐案例账本及逐证券快照哈希。",
              "费用贡献供后续研究审阅，本轮不选择新退出规则或优化赢家；研究状态和实盘锁保持不变。", ""]
    return "\n".join(lines)


def ingest_granville_audit_reports(session, root):
    cfg = json.loads((root / "config/granville_portfolio_v1.json").read_text(encoding="utf-8"))
    count = 0
    for path in sorted((root / "artifacts/research/granville-us-audits").glob("*/publication.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        validate_publication(value, cfg)
        run = session.get(ResearchRun, value["source_run_id"])
        if (run is None or run.strategy_id != value["strategy_id"] or run.run_type != "granville_stock_us_effective_correction" or
                run.source_hash != value["source_run_sha256"]):
            raise ValueError("Existing immutable US correction run must reconcile")
        original = run.details["market_results"]["US"]
        if original.get("original_summary_sha256") != value["source_summary_sha256"]:
            raise ValueError("Original corrected summary hash differs")
        old_cases = {(c["variant"], c["exit_policy"], c["cost_case"]): c for c in original["cases"]}
        for case in value["ledgers"]["cases"]:
            old = old_cases[(case["variant"], case["exit_policy"], case["cost_case"])]
            if case["source_status"] != old["status"] or (old["status"] == "blocked" and case["blocking_reason"] != old["blocking_reason"]):
                raise ValueError("Original status/blocking reason changed")
            if case["status"] == "diagnosed":
                if case["diagnostics"]["periods"]["full"]["account"] != old["periods"]["full"]:
                    # Floating recomputation can differ at machine precision.
                    for name, observed in case["diagnostics"]["periods"]["full"]["account"].items():
                        expected = old["periods"]["full"][name]
                        if isinstance(observed, float) and expected is not None:
                            if abs(observed - expected) > 1e-9:
                                raise ValueError("Audited original performance differs")
                        elif observed != expected:
                            raise ValueError("Audited original performance differs")
                if any(original["artifact_hashes"].get(k) != h for k, h in case["artifact_hashes"].items()):
                    raise ValueError("Original ledger hashes differ")
        for key, name in [("membership_version", "fresh_membership_capture_version"), ("inventory_version", "price_inventory_version")]:
            if value["history"][key] != original[name]:
                raise ValueError("Long-history source identity differs from correction")
        # Preserve a canonical envelope alongside the visible Markdown, so an
        # unchanged report cannot hide mutated aggregate fields on reimport.
        envelope_hash = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        content = render_report(value) + f"\n聚合发布内容SHA256：`{envelope_hash}`。\n"
        checksum = hashlib.sha256(content.encode()).hexdigest()
        existing = session.get(ResearchReport, value["id"])
        if existing:
            if existing.content_hash != checksum or existing.run_id != run.id or existing.report_type != REPORT_TYPE:
                raise ValueError("Immutable aggregate audit changed; add a new report")
            continue
        session.add(ResearchReport(id=value["id"], strategy_id=value["strategy_id"], run_id=run.id,
                                   title="美股葛兰威尔：成本与退出归因、长历史准入检查（仅诊断）", report_type=REPORT_TYPE,
                                   as_of_date=value["as_of_date"], format="markdown", content=content,
                                   content_hash=checksum, artifact_path=str(path.relative_to(root))))
        count += 1
    session.commit()
    return {"reports": count}
