"""Capture daily ETF prices/actions/calendar; never request minute data."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd
from deepstock.data.store import ROOT, RULE_VERSION, DataStore, read_clean_csv, input_evidence, write_json
from deepstock.strategies.cn.auction.providers import get_pro


def register(frame, path, kind, upstream):
    frame.to_csv(path, index=False)
    metadata = {"market": "CN", "provider": "Tushare", "kind": kind, "origin": "derived_provider_export",
                "timezone": "Asia/Shanghai", "upstream_versions": upstream,
                "volume_unit": "shares", "amount_unit": "CNY", "adjustment": "raw_execution_and_fund_adj_indicators"}
    DataStore().import_file(path, metadata)
    return read_clean_csv(path)


def cached_response(endpoint, start=None, end=None):
    """Reuse retained provider evidence, not an undocumented provider cache."""
    candidates = []
    for path in sorted((ROOT / "artifacts/providers/tushare" / endpoint).glob("*.csv.gz")):
        manifest = DataStore().get(json.loads(DataStore()._alias(path).read_text())["version"])
        if endpoint == "fund_div" and manifest["rules"] != RULE_VERSION:
            manifest = DataStore().import_file(path, manifest["contract"])
        if endpoint == "fund_div" and manifest["status"] != "ready":
            raise ValueError(f"Cached dividends blocked: {manifest['quality']}")
        if manifest["status"] != "ready":
            continue
        frame = read_clean_csv(path)
        if "ts_code" in frame and not frame.ts_code.eq("510300.SH").all():
            continue
        if start is not None:
            col = "cal_date" if endpoint == "trade_cal" else "trade_date"
            dates = pd.to_datetime(frame[col], format="mixed")
            frame = frame.loc[dates.between(pd.Timestamp(start), pd.Timestamp(end))]
        if not frame.empty:
            candidates.append((len(frame), str(path), frame))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[:2])[2].copy()


def download(output: Path, end="2026-09-29", reuse_captured=False):
    output.mkdir(parents=True, exist_ok=False)
    pro = get_pro()
    daily, factors = [], []
    for year in range(2012, int(end[:4]) + 1):
        start_date = f"{year}0101" if year > 2012 else "20120528"
        end_date = min(f"{year}1231", end.replace("-", ""))
        for endpoint, target in [("fund_daily", daily), ("fund_adj", factors)]:
            frame = cached_response(endpoint, start_date, end_date) if reuse_captured else None
            if frame is None:
                frame = getattr(pro, endpoint)(ts_code="510300.SH", start_date=start_date, end_date=end_date)
            if frame is None or frame.empty:
                raise ValueError(f"No {endpoint} rows in {year}; no implicit gap filling")
            target.append(frame)
    prices = pd.concat(daily, ignore_index=True)
    adjustment = pd.concat(factors, ignore_index=True)
    for frame in [prices, adjustment]:
        frame["trade_date"] = pd.to_datetime(frame.trade_date, format="mixed").dt.strftime("%Y-%m-%d")
    prices = prices.sort_values("trade_date")
    if prices.trade_date.duplicated().any() or adjustment.trade_date.duplicated().any():
        raise ValueError("Conflicting daily/adjustment dates")
    merged = prices.merge(adjustment[["trade_date", "adj_factor"]], on="trade_date", how="left", validate="one_to_one")
    if merged.adj_factor.isna().any():
        raise ValueError("Missing adjustment factors; never fill them from later dates")
    merged = merged.rename(columns={"trade_date": "date", "ts_code": "symbol", "vol": "volume"})
    merged["volume"] *= 100  # Tushare daily fund volume is hands of 100 shares.
    merged["amount"] *= 1000  # Daily fund amount is thousands of CNY.
    scale = merged.adj_factor / float(merged.adj_factor.iloc[0])
    for field in ["open", "high", "low", "close"]:
        merged["adjusted_" + field] = merged[field] * scale
    div = cached_response("fund_div") if reuse_captured else None
    if div is None:
        div = pro.fund_div(ts_code="510300.SH")
    if div is None or div.empty:
        raise ValueError("Corporate-action evidence unavailable")
    div = div.loc[div.div_proc.eq("实施")].copy()
    div["ex_date"] = pd.to_datetime(div.ex_date, format="mixed", errors="raise")
    div["pay_date"] = pd.to_datetime(div.pay_date, format="mixed", errors="raise")
    div = div.loc[div.ex_date.between(pd.Timestamp("2012-05-28"), pd.Timestamp(end))]
    if div.ex_date.duplicated().any() or div.ex_date.isna().any() or div.pay_date.isna().any() or (div.pay_date < div.ex_date).any():
        raise ValueError("Ambiguous dividend dates; do not invent payout timing")
    div = div.rename(columns={"ts_code": "symbol", "div_cash": "cash_per_share"})
    # Provider documentation: div_cash is CNY per share, NOT per base_unit.
    div = div[["symbol", "ann_date", "record_date", "ex_date", "pay_date", "cash_per_share"]].sort_values("ex_date")
    calendar = cached_response("trade_cal", "20120528", end) if reuse_captured else None
    if calendar is None or pd.to_datetime(calendar.cal_date).max() < merged.date.pipe(pd.to_datetime).max() or pd.to_datetime(calendar.cal_date).min() > merged.date.pipe(pd.to_datetime).min():
        calendar = pro.trade_cal(exchange="SSE", start_date="20120528", end_date=end.replace("-", ""), is_open="1")
    calendar = calendar.loc[pd.to_numeric(calendar.is_open).eq(1)]
    dates = pd.DataFrame({"date": calendar.cal_date}).sort_values("date")
    upstream = [r["version"] for r in input_evidence()["data_versions"]]
    bars = register(merged, output / "daily.csv", "daily_ohlc", upstream)
    dividends = register(div, output / "dividends.csv", "dividends", upstream)
    register(dates, output / "calendar.csv", "calendar", upstream)
    manifest = {"symbol": "510300.SH", "provider": "Tushare", "market": "CN", "timezone": "Asia/Shanghai",
                "origin": "derived_provider_export", "upstream_versions": upstream,
                "adjustment": "raw_execution_and_fund_adj_indicators", "volume_unit": "shares", "amount_unit": "CNY",
                "rows": len(bars), "dividends": len(dividends), "requested_end": end,
                "stock_minute_endpoint_called": False, "fund_div_definition": "https://tushare.pro/document/2?doc_id=120",
                "corporate_action_status": "cash_dividends_with_factor_date_audit_required", **input_evidence()}
    write_json(output / "manifest.json", manifest)
    return {"status": "captured", "folder": str(output), "rows": len(bars), "dividends": len(dividends), "version_count": len(manifest["data_versions"])}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/data" / ("granville-cn-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")))
    p.add_argument("--reuse-captured", action="store_true", help="Recover from retained daily/factor/dividend evidence; fetch only missing responses")
    args = p.parse_args()
    print(json.dumps(download(args.output_dir, reuse_captured=args.reuse_captured), ensure_ascii=False))


if __name__ == "__main__":
    main()
