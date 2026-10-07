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
    return read_clean_csv(path), DataStore().resolve(path)["id"]


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
                                ref, ref_version = reference(symbol)
                                item["reference_version"] = ref_version
                                result, diff = compare_daily(f, ref, symbol)
                                item["cross_source"] = result
                                comparisons.extend([{**d, "comparison": "Tushare_daily"} for d in diff])
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


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    print(json.dumps({k: v for k, v in run(a.output_dir).items() if k in {"status", "actual_requests", "threshold_exceedances"}}))


if __name__ == "__main__":
    main()
