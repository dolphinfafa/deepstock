"""Independent, bounded A-share quality audit; not used by any strategy."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import sys
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.rerun_clean_research import capture_code_provenance

import pandas as pd
from deepstock.data.store import ROOT, DataStore, digest, input_evidence, read_clean_csv, write_json
from deepstock.data.easy_tdx import (SDK_COMMIT, HOSTS, SYMBOLS, MINUTE_SYMBOLS, RequestBudget, make_client,
                                     compare_daily, daily_coverage, minute_audit, aggregate_minutes)


def calendar(now):
    """Registered provider calendar, including holidays; never weekday guessing."""
    candidates = []
    for path in DataStore().manifests():
        m = DataStore().get(path.parent.name)
        if m.get("kind") != "calendar" or m["provider"] != "Tushare" or m["status"] != "ready":
            continue
        f = read_clean_csv(m["id"])
        if "cal_date" in f and "is_open" in f:
            dates = pd.to_datetime(f.cal_date)
            if dates.min() <= pd.Timestamp("2023-01-01") and dates.max() >= now.tz_localize(None).normalize():
                candidates.append((m["id"], f))
    if candidates:
        version, f = sorted(candidates, key=lambda x: x[0])[0]
    else:
        from deepstock.strategies.cn.auction.providers import get_pro
        f = get_pro().trade_cal(exchange="SSE", start_date="20230101", end_date=now.strftime("%Y%m%d"))
        version = None
    dates = pd.to_datetime(f.loc[pd.to_numeric(f.is_open).eq(1), "cal_date"], format="mixed")
    cutoff = now.tz_localize(None).normalize()
    complete = dates < cutoff if now.hour < 15 else dates <= cutoff
    return pd.DatetimeIndex(dates[complete].sort_values().unique()), version


def reference(symbol):
    path = ROOT / "artifacts/data/granville-stocks-cn-20261006-v1/prices" / (symbol + ".csv.gz")
    if symbol == "510300.SH":
        path = ROOT / "artifacts/data/granville-cn-20261006-v4/daily.csv"
    # Pin an already registered, hash-matching CN/Tushare version. An old
    # generic inventory alias must not turn A-share evidence into XNYS data.
    store = DataStore()
    checksum = digest(path)
    candidates = []
    for mp in store.manifests():
        m = store.get(mp.parent.name)
        if (m["market"] == "CN" and m["provider"] == "Tushare" and m["status"] == "ready"
            and m["source_sha256"] == checksum and Path(m["source_path"]).resolve() == path.resolve()):
            candidates.append(m)
    if not candidates:
        raise ValueError("No registered hash-matching independent CN/Tushare reference")
    chosen = sorted(candidates, key=lambda m: (m["rules"], m["imported_at_utc"], m["id"]))[-1]
    return read_clean_csv(chosen["id"]), chosen["id"]


def run(output):
    code = capture_code_provenance()
    if code["tracked_dirty"]:
        raise ValueError("Run source audit from committed clean code")
    output.mkdir(parents=True, exist_ok=False)
    now = pd.Timestamp.now(tz="Asia/Shanghai")
    store = DataStore()
    budget = RequestBudget()
    report = {"id": "easy-tdx-" + output.name, "scope": "independent_CN_data_audit_no_strategy",
              "as_of_date": str(now.date()), "sdk_version": importlib.metadata.version("easy-tdx"), "sdk_commit": SDK_COMMIT,
              "raw_definition": "Decoded SDK return and per-page return, not raw TCP packet capture",
              "license": "MIT software; market-data redistribution rights unverified, previews disabled",
              "daily_symbols": list(SYMBOLS), "minute_symbols": list(MINUTE_SYMBOLS),
              "request_limit": 70, "requests_include": "socket sends including setup, pagination and retries; <=1/second",
              "servers_attempted": [], "items": [], "data_versions": [], "failures": []}
    distribution = importlib.metadata.distribution("easy-tdx")
    direct_url = json.loads(distribution.read_text("direct_url.json") or "{}")
    report["code_provenance"] = code
    report["wheel_hashes"] = direct_url.get("archive_info", {}).get("hashes", {})
    if report["sdk_version"] != "1.20.8" or report["wheel_hashes"].get("sha256") != "26845c863e856e3101d7979b509dcfce925bf28a33fbba449b4e526a1a4d1347":
        raise ValueError("Pinned SDK wheel identity differs")
    client = None
    comparisons, datasets = [], {}
    try:
        sessions, cal_version = calendar(now)
        report.update(calendar_version=cal_version, latest_complete_session=str(sessions[-1].date()), minute_sessions=[str(x.date()) for x in sessions[-5:]])
        for host in HOSTS:
            report["servers_attempted"].append(host)
            candidate = make_client(host, budget, output)
            try:
                candidate.connect()
                client = candidate
                report["server"] = host
                break
            except Exception as e:
                candidate.close()
                report["failures"].append({"operation": "connect", "server": host, "error": str(e)})
        if client is None:
            raise RuntimeError("No reachable server among the three fixed candidates")
        from easy_tdx import Period, Adjust
        specs = [(s, "DAILY", a, 800) for s in SYMBOLS for a in ["NONE", "QFQ"]]
        specs += [(s, "MIN_1", "NONE", 1600) for s in MINUTE_SYMBOLS]
        specs += [(SYMBOLS[0], "DAILY", "NONE", 800)]  # repeat retrieval stability, not a new history
        for item_index, (symbol, period, adjustment, count) in enumerate(specs):
            item = {"symbol": symbol, "period": period, "adjustment": adjustment, "requested_count": count,
                    "bar_time": "start", "attempts": [], "status": "failed"}
            for attempt in range(2):
                start_requests = len(budget.events)
                try:
                    if attempt:
                        client.close()
                        client.connect()
                    data = client.get_stock_kline(1 if symbol.endswith(".SH") else 0, symbol[:6],
                                                 getattr(Period, period), count=count, adjust=getattr(Adjust, adjustment), bar_time="start")
                    path = output / f"{item_index:02d}-{symbol}-{period}-{adjustment}-{attempt}-raw.csv.gz"
                    data.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
                    meta = {"provider": "easy-tdx", "market": "CN", "origin": "provider_response", "endpoint": "easy_tdx_bars",
                            "normalizer": "easy-tdx-cn-v1", "symbol": symbol, "period": period, "adjustment": adjustment,
                            "bar_time": "start", "timezone": "Asia/Shanghai", "volume_unit": "shares", "amount_unit": "CNY",
                            "restricted": True, "sdk_commit": SDK_COMMIT, "sdk_version": report["sdk_version"], "server": report["server"],
                            "request_params": {"start": 0, "count": count, "times": 1}, "retrieved_at_utc": datetime.now(timezone.utc).isoformat()}
                    manifest = store.import_file(path, meta)
                    report["data_versions"].append(manifest["id"])
                    item.update(version=manifest["id"], rows=len(data), status=manifest["status"], quality=manifest["quality"])
                    item["attempts"].append({"attempt": attempt + 1, "requests": len(budget.events) - start_requests, "status": "received"})
                    if manifest["status"] == "ready":
                        f = read_clean_csv(manifest["id"])
                        key = (symbol, period, adjustment)
                        if key in datasets:
                            try:
                                pd.testing.assert_frame_equal(f, datasets[key], check_dtype=False)
                                item["repeat_stability"] = "identical"
                            except AssertionError:
                                item["repeat_stability"] = "changed_retain_both_versions"
                        else:
                            datasets[key] = f
                        if period == "DAILY":
                            item["coverage"] = daily_coverage(f, sessions)
                            if adjustment == "NONE":
                                try:
                                    ref, ref_version = reference(symbol)
                                    item["reference_version"] = ref_version
                                    result, diff = compare_daily(f, ref, symbol)
                                    item["cross_source"] = result
                                    comparisons.extend([{**d, "comparison": "Tushare_daily"} for d in diff])
                                except Exception as e:
                                    # A reference/analysis failure is not a failed
                                    # provider request; never redownload for it.
                                    item["cross_source"] = {"status": "blocked_reference", "error": str(e)}
                        else:
                            item["minute_audit"] = minute_audit(f, sessions[-5:])
                            daily = datasets.get((symbol, "DAILY", "NONE"))
                            if daily is not None:
                                result, diff = compare_daily(aggregate_minutes(f, sessions[-5:]), daily, symbol)
                                item["minute_vs_daily_internal_consistency"] = result
                                comparisons.extend([{**d, "comparison": "internal_minute_daily"} for d in diff])
                    break
                except Exception as e:
                    item["attempts"].append({"attempt": attempt + 1, "requests": len(budget.events) - start_requests, "error": str(e)})
                    if len(budget.events) >= budget.limit:
                        break
            report["items"].append(item)
            print(json.dumps({k: item.get(k) for k in ["symbol", "period", "adjustment", "status", "rows", "cross_source"]}), flush=True)
        # One quote sample; fields/timestamps only, no real-time trading assertion.
        try:
            quotes = client.get_stock_quotes([(0, "000001"), (1, "510300")])
            qp = output / "quote-raw.csv.gz"
            quotes.to_csv(qp, index=False, compression={"method": "gzip", "mtime": 0})
            m = store.import_file(qp, {"provider": "easy-tdx", "market": "CN", "origin": "provider_response", "restricted": True,
                                      "endpoint": "easy_tdx_quotes", "server": report["server"], "sdk_commit": SDK_COMMIT})
            report["data_versions"].append(m["id"])
            report["quote"] = {"version": m["id"], "rows": len(quotes), "fields": list(quotes),
                               "usability": "fields_only_no_independent_freshness_proof_not_real_time_trade_ready"}
        except Exception as e:
            report["quote"] = {"status": "failed", "error": str(e)}
    except Exception as e:
        report["failures"].append({"operation": "audit", "error": str(e)})
    finally:
        if client:
            client.close()
    report["requests"] = budget.events
    report["actual_requests"] = len(budget.events)
    report["threshold_exceedances"] = len(comparisons)
    report["status"] = "blocked_connectivity" if not report.get("server") else "completed_with_findings"
    report["adjustment_boundary"] = "QFQ is vendor historical forward adjustment, not point-in-time factors. No action history verification; not accepted for portfolio backtests. Repair disabled."
    report["pagination"] = []
    for page in sorted(output.glob("page-*.json")):
        p = json.loads(page.read_text(encoding="utf-8"))
        rows = p["decoded_sdk_page"]
        report["pagination"].append({"file": page.name, "command": p["command"], "params": p["params"],
                                    "returned_rows": len(rows) if isinstance(rows, list) else None,
                                    "first_datetime": rows[0].get("datetime") if rows and isinstance(rows, list) and isinstance(rows[0], dict) else None,
                                    "last_datetime": rows[-1].get("datetime") if rows and isinstance(rows, list) and isinstance(rows[-1], dict) else None})
    if capture_code_provenance()["source_sha256"] != code["source_sha256"]:
        raise ValueError("Source changed during audit; cannot publish")
    report["data_versions"] = list(dict.fromkeys([*report["data_versions"], *[r["version"] for r in input_evidence()["data_versions"]]]))
    return write_report(report, comparisons, output)


def write_report(report, comparisons, output):
    pd.DataFrame(comparisons, columns=["comparison", "symbol", "date", "field", "tdx", "reference", "difference", "tolerance"]).to_csv(output / "discrepancies.csv", index=False)
    write_json(output / "quality_report.json", report)
    lines = ["# easy-tdx：独立A股小样本数据审计", "", f"状态：{report['status']}；实际请求{report['actual_requests']}/70（含握手、分页、重试）。",
             "未接入葛兰威尔或其他策略；没有调用受限分钟接口、恢复暂停策略、创建任务或下单。软件MIT许可不代表行情可转载，原始/清洗预览均关闭。",
             "日线与已注册Tushare样本交叉核对；价格允许一个最小价位，量额允许0.1%+舍入。超阈值逐行保留，绝不拟合比例或补造缺口。",
             "分钟仅检查最近5个完整交易日和聚合内部一致性；没有本窗口独立分钟源，不宣称准确性已经独立证明。国庆休市由注册日历确认。",
             "QFQ没有点时公司行动验证；报价没有独立时效证据，不具备实盘实时数据准入。", "", "```json", json.dumps(report, ensure_ascii=False, indent=2), "```"]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    report["report_md"] = "\n".join(lines) + "\n"
    report["artifact_hashes"] = {p.name: digest(p) for p in output.glob("*") if p.is_file()}
    write_json(output / "publication.json", report)
    return report


def analyse_existing(source, output):
    """Recover comparison only, preserving the completed capture and every failure."""
    from copy import deepcopy
    code = capture_code_provenance()
    if code["tracked_dirty"]:
        raise ValueError("Committed clean analysis source required")
    original = json.loads((source / "publication.json").read_text(encoding="utf-8"))
    if original.get("scope") != "independent_CN_data_audit_no_strategy":
        raise ValueError("Not an independent easy-tdx capture")
    for name, checksum in original["artifact_hashes"].items():
        if digest(source / name) != checksum:
            raise ValueError("Immutable capture artifact differs")
    output.mkdir(parents=True, exist_ok=False)
    report = deepcopy(original)
    for key in ["report_md", "artifact_hashes"]:
        report.pop(key, None)
    report.update(id="easy-tdx-" + output.name, status="completed_with_findings",
                  capture_provenance=original["code_provenance"], code_provenance=code,
                  source_publication_hash=digest(source / "publication.json"),
                  offline_reanalysis=True, new_provider_requests=0,
                  recovery_note="Reference lookup recovery uses existing CN/Tushare registered hash-matching versions, not the misclassified generic alias. Source provider amount remains unmodified; compare normalized CNY turnover, not its auxiliary thousand-CNY column. Original capture errors/70-request ceiling/quote failure retained.")
    # Calendar was captured/registered during the original run. Recovery must
    # never acquire a new calendar (or any data) over the network.
    sessions = None
    for version in original["data_versions"]:
        m = DataStore().get(version)
        if m.get("kind") == "calendar" and m["provider"] == "Tushare":
            f = read_clean_csv(version)
            if "cal_date" not in f:
                continue
            dates = pd.to_datetime(f.loc[pd.to_numeric(f.is_open).eq(1), "cal_date"], format="mixed")
            if dates.min() <= pd.Timestamp("2023-01-01") and dates.max() >= pd.Timestamp(original["as_of_date"]):
                sessions = pd.DatetimeIndex(dates.loc[dates <= pd.Timestamp(original["latest_complete_session"])].sort_values().unique())
                report["calendar_version"] = version
                break
    if sessions is None:
        raise ValueError("Retained registered calendar required; no recovery network fallback")
    differences, datasets = [], {}
    for item in report["items"]:
        if not item.get("version") or item["status"] != "ready":
            continue
        item["capture_attempts"] = item.pop("attempts")
        f = read_clean_csv(item["version"])
        symbol, period, adjustment = item["symbol"], item["period"], item["adjustment"]
        key = (symbol, period, adjustment)
        if key in datasets:
            try:
                pd.testing.assert_frame_equal(f, datasets[key], check_dtype=False)
                item["repeat_stability"] = "identical"
            except AssertionError:
                item["repeat_stability"] = "changed_retain_both_versions"
        else:
            datasets[key] = f
        if period == "DAILY":
            item["coverage"] = daily_coverage(f, sessions)
            if adjustment == "NONE":
                ref, version = reference(symbol)
                report["data_versions"].append(version)
                item["reference_version"] = version
                result, diff = compare_daily(f, ref, symbol)
                item["cross_source"] = result
                item["reference_amount_field"] = "turnover_CNY" if "turnover" in ref else "amount_CNY"
                differences.extend([{**d, "comparison": "Tushare_daily"} for d in diff])
        else:
            item["minute_audit"] = minute_audit(f, sessions[-5:])
            q = item["minute_audit"]
            if q["missing_minutes"] or q["unexpected_minutes"]:
                # Test a label hypothesis separately; do not alter clean bars.
                shifted = f.copy()
                shifted["timestamp"] = pd.to_datetime(shifted.timestamp) - pd.Timedelta(minutes=1)
                check = minute_audit(shifted, sessions[-5:])
                item["label_diagnostic"] = {"status": "blocked_timestamp_semantics",
                                            "hypothesis": "Returned MIN_1 labels appear to be right endpoints despite SDK bar_time=start; -1min is diagnostic only, clean values unchanged.",
                                            "hypothetical_minus_one_minute_missing": len(check["missing_minutes"]),
                                            "hypothetical_minus_one_minute_unexpected": len(check["unexpected_minutes"])}
            daily = datasets.get((symbol, "DAILY", "NONE"))
            if daily is not None:
                result, diff = compare_daily(aggregate_minutes(f, sessions[-5:]), daily, symbol)
                item["minute_vs_daily_internal_consistency"] = result
                differences.extend([{**d, "comparison": "internal_minute_daily"} for d in diff])
    report["threshold_exceedances"] = len(differences)
    report["data_versions"] = list(dict.fromkeys(report["data_versions"]))
    report["usability"] = {"daily": "Only fixed-sample raw OHLCV comparisons; not long-history/action/point-in-time-universe acceptance",
                           "minute": "blocked_timestamp_semantics_and_no_independent_minute_reference",
                           "QFQ": "not_point_in_time_action_verified_no_strategy_admission",
                           "quote": "not_tested_request_budget_exhausted_no_new_requests"}
    if capture_code_provenance()["source_sha256"] != code["source_sha256"]:
        raise ValueError("Analysis source changed")
    return write_report(report, differences, output)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--analyse-existing", type=Path, help="Offline recovery: reuse immutable capture; no new requests")
    a = p.parse_args()
    result = analyse_existing(a.analyse_existing, a.output_dir) if a.analyse_existing else run(a.output_dir)
    print(json.dumps({k: v for k, v in result.items() if k in {"status", "actual_requests", "threshold_exceedances"}}))


if __name__ == "__main__":
    main()
