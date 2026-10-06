"""Resumable, versioned stock evidence; Windows-local Norgate, server Tushare."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import exchange_calendars as xcals
import numpy as np
import pandas as pd

from deepstock.data.capture import capture_response
from deepstock.data.store import ROOT, RULE_VERSION, DataQualityError, DataStore, clean_stock_dividends, digest, input_evidence, read_clean_csv, read_clean_json, write_json


def persist(frame, path, market, kind, upstream=(), **extra):
    if path.exists():
        return read_clean_csv(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, compression="gzip" if path.suffix == ".gz" else None)
    metadata = {"market": market, "provider": "Norgate" if market == "US" else "Tushare", "restricted": market == "US",
                "origin": "derived_provider_export", "kind": kind, "upstream_versions": list(upstream),
                "timezone": "America/New_York" if market == "US" else "Asia/Shanghai", **extra}
    result = DataStore().import_file(path, metadata)
    if result["status"] != "ready":
        raise ValueError(f"Derived {kind} blocked: {result['quality']['issues']}")
    return read_clean_csv(path)


def request_cn(endpoint, symbol, output, **kwargs):
    path = output / "responses" / f"{symbol}-{endpoint}.csv.gz"
    if path.exists():
        try:
            return read_clean_csv(path)
        except DataQualityError:
            if endpoint != "dividend":
                raise
            path = output / "responses" / f"{symbol}-{endpoint}-recovery-{RULE_VERSION}.csv.gz"
            if path.exists():
                return read_clean_csv(path)
    from deepstock.strategies.cn.auction.providers import get_pro
    if endpoint == "dividend":
        cfg = json.loads((ROOT / "config/granville_portfolio_v1.json").read_text(encoding="utf-8"))
        store = DataStore()
        retained = []
        for source in sorted((ROOT / "artifacts/providers/tushare/dividend").glob("*.csv.gz"), reverse=True):
            old = store.get(json.loads(store._alias(source).read_text())["version"])
            candidate = pd.read_csv(store.verified_path(old, "raw"))
            if not candidate.empty and candidate.ts_code.eq(symbol).all():
                retained.append((candidate, old["id"]))
                if "end_date" in candidate and "imp_ann_date" in candidate:
                    break
        frame, upstream = next(((f, v) for f, v in retained if "end_date" in f and "imp_ann_date" in f), next(((f, v) for f, v in retained if "end_date" in f), retained[0] if retained else (None, None)))
        def view(response):
            ex = pd.to_datetime(response.ex_date.astype("string").str.replace(r"\.0$", "", regex=True), errors="coerce", format="mixed")
            ann = pd.to_datetime(response.ann_date.astype("string").str.replace(r"\.0$", "", regex=True), errors="coerce", format="mixed")
            keep = ex.between(cfg["source_start"], cfg["evaluation_end"]) | (ex.isna() & (ann.isna() | ann.between(cfg["source_start"], cfg["evaluation_end"])))
            return response.loc[keep].copy()
        # Clean a declared study-period view, retaining the entire provider
        # response (including historical conflicts) as upstream raw evidence.
        if frame is None or (clean_stock_dividends(view(frame))[2]["blocking"] and ("end_date" not in frame or "imp_ann_date" not in frame)):
            frame = get_pro().client.dividend(ts_code=symbol, **kwargs)
            if frame is None:
                raise ValueError(f"Missing dividend response: {symbol}")
            upstream = capture_response("Tushare", "dividend", frame, "CN")
        selected = view(frame)
        return persist(selected, path, "CN", "stock_dividends", [upstream], endpoint="dividend",
                       source_start=cfg["source_start"], source_end=cfg["evaluation_end"], source_rows=len(frame),
                       excluded_outside_declared_interval=len(frame) - len(selected), empty_response_verified=frame.empty)
    before = {r["version"] for r in input_evidence()["data_versions"]}
    frame = getattr(get_pro(), endpoint)(ts_code=symbol, **kwargs)
    if frame is None:
        raise ValueError(f"Missing {endpoint} response: {symbol}")
    refs = [r["version"] for r in input_evidence()["data_versions"] if r["version"] not in before]
    if frame.empty:
        if endpoint not in {"suspend_d", "dividend"}:
            raise ValueError(f"Empty required {endpoint}: {symbol}")
        refs.append(capture_response("Tushare", endpoint, frame, "CN"))
    return persist(frame, path, "CN", "stock_dividends" if endpoint == "dividend" else endpoint,
                   refs, endpoint=endpoint, empty_response_verified=frame.empty)


def collect_cn(output, cfg):
    source = ROOT / "artifacts/auction_history_tushare/universe_snapshots.csv"
    universe = read_clean_csv(source, dtype={"stock_code": str})
    universe["snapshot_date"] = pd.to_datetime(universe.snapshot_date)
    universe = universe.loc[universe.snapshot_date.le(pd.Timestamp(cfg["evaluation_end"]))].copy()
    if not universe.snapshot_date.le(pd.Timestamp(cfg["evaluation_start"])).any():
        raise ValueError("No pre-evaluation historical membership")
    symbols = sorted(universe.ts_code.unique())
    membership = pd.DataFrame({"date": universe.snapshot_date, "symbol": universe.ts_code, "weight": universe.weight})
    persist(membership, output / "membership.csv.gz", "CN", "historical_membership", [DataStore().resolve(source)["id"]])
    start, end = cfg["source_start"].replace("-", ""), cfg["evaluation_end"].replace("-", "")
    from deepstock.strategies.cn.auction.providers import get_pro
    cal_path = output / "calendar.csv"
    if not cal_path.exists():
        cal = get_pro().trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
        persist(pd.DataFrame({"date": cal.cal_date}), cal_path, "CN", "calendar")
    failures = []
    for i, symbol in enumerate(symbols):
        marker = output / "complete" / (symbol + ".json")
        if marker.exists():
            for name in ["prices", "dividends", "suspensions"]:
                read_clean_csv(output / name / (symbol + ".csv.gz"))
        else:
            try:
                daily = request_cn("daily", symbol, output, start_date=start, end_date=end)
                factors = request_cn("adj_factor", symbol, output, start_date=start, end_date=end)
                limits = request_cn("stk_limit", symbol, output, start_date=start, end_date=end)
                dividend = request_cn("dividend", symbol, output, fields="ts_code,end_date,ann_date,imp_ann_date,div_proc,stk_div,stk_bo_rate,stk_co_rate,cash_div,cash_div_tax,record_date,ex_date,pay_date,div_listdate")
                susp = request_cn("suspend_d", symbol, output, start_date=start, end_date=end)
                for frame in [daily, factors, limits]:
                    frame["trade_date"] = pd.to_datetime(frame.trade_date.astype(str), format="mixed").dt.strftime("%Y-%m-%d")
                bars = daily.merge(factors[["trade_date", "adj_factor"]], on="trade_date", validate="one_to_one", how="left")
                bars = bars.merge(limits[["trade_date", "up_limit", "down_limit"]], on="trade_date", validate="one_to_one", how="left")
                if bars[["adj_factor", "up_limit", "down_limit"]].isna().any().any():
                    raise ValueError("Missing executable session factor/limit evidence")
                bars = bars.rename(columns={"trade_date": "date", "ts_code": "symbol", "vol": "volume"}).sort_values("date")
                bars["volume"] *= 100
                bars["turnover"] = bars.amount * 1000
                ratio = bars.adj_factor / bars.adj_factor.iloc[0]
                for field in ["open", "high", "low", "close"]:
                    bars["adjusted_" + field] = bars[field] * ratio
                refs = [frame.attrs["data_version"] for frame in [daily, factors, limits, dividend, susp]]
                persist(bars, output / "prices" / (symbol + ".csv.gz"), "CN", "daily_ohlc", refs,
                        adjustment="raw_ohlc_and_forward_adj_factor_analytical_return", volume_unit="shares", amount_unit="CNY")
                persist(dividend, output / "dividends" / (symbol + ".csv.gz"), "CN", "stock_dividends", refs)
                persist(susp, output / "suspensions" / (symbol + ".csv.gz"), "CN", "suspensions", refs, endpoint="suspend_d", empty_response_verified=susp.empty)
                write_json(marker, {"symbol": symbol, "status": "captured"})
            except Exception as error:
                failures.append({"symbol": symbol, "error": str(error)[:250]})
                print(json.dumps({"symbol": symbol, "status": "blocked", "error": str(error)[:250]}), flush=True)
        if (i + 1) % 10 == 0 or i + 1 == len(symbols):
            print(json.dumps({"market": "CN", "processed": i + 1, "total": len(symbols), "failures": len(failures)}), flush=True)
        time.sleep(.15)
    manifest = {"market": "CN", "symbols": symbols, "symbol_count": len(symbols), "source_start": cfg["source_start"], "source_end": cfg["evaluation_end"],
                "membership_model": "lagged_monthly_historical_snapshots_not_exact_daily_membership", "failures": failures,
                "config_hash": digest(ROOT / "config/granville_portfolio_v1.json"), "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "no_minute_requests": True, **input_evidence()}
    # Retain explicit terminal status rather than confusing an open-ended
    # suspension with a delisting based on the lack of future observations.
    terminal_path = output / "terminal_reference.csv.gz"
    if not terminal_path.exists():
        reference = get_pro().stock_basic(exchange="", list_status="D", fields="ts_code,name,list_date,delist_date,list_status")
        persist(reference, terminal_path, "CN", "security_reference", endpoint="stock_basic")
    reference = read_clean_csv(terminal_path)
    terminals = {}
    for row in reference.loc[reference.ts_code.isin(symbols)].itertuples():
        date = pd.to_datetime(str(row.delist_date).removesuffix(".0"), format="mixed")
        price_path = output / "prices" / (row.ts_code + ".csv.gz")
        if date <= pd.Timestamp(cfg["evaluation_end"]) and price_path.exists():
            last_quote = str(pd.to_datetime(read_clean_csv(price_path).date).max().date())
            terminals[row.ts_code] = {"last_quote_date": last_quote, "delist_date": str(date.date()), "verified_proceeds": False}
    manifest["terminal_securities"] = terminals
    # Re-audit every existing disclosure from retained, hash-verified provider
    # bytes. Earlier cleaner revisions must not erase an additional payout.
    # No price or dividend request is repeated here.
    sources = {}
    for source in [*(ROOT / "artifacts/providers/tushare/dividend").glob("*.csv.gz"), *(ROOT / "artifacts/providers/tushare").glob("*.csv.gz")]:
        alias = DataStore()._alias(source)
        if not alias.exists():
            continue
        raw_manifest = DataStore().get(json.loads(alias.read_text())["version"])
        frame = pd.read_csv(DataStore().verified_path(raw_manifest, "raw"))
        if frame.empty or "div_proc" not in frame or "ts_code" not in frame or frame.ts_code.nunique() != 1:
            continue
        symbol = frame.ts_code.iloc[0]
        if symbol not in symbols:
            continue
        score = (int("end_date" in frame) + int("imp_ann_date" in frame) + int("base_share" in frame), raw_manifest["imported_at_utc"])
        if symbol not in sources or score > sources[symbol][0]:
            sources[symbol] = (score, frame, raw_manifest["id"])
    audit_dir = "dividends-audit/" + RULE_VERSION
    for symbol in symbols:
        if symbol not in sources:
            failures.append({"symbol": symbol, "error": "Retained complete dividend response missing"})
            continue
        _, frame, version = sources[symbol]
        ex = pd.to_datetime(frame.ex_date.astype("string").str.replace(r"\.0$", "", regex=True), errors="coerce", format="mixed")
        ann = pd.to_datetime(frame.ann_date.astype("string").str.replace(r"\.0$", "", regex=True), errors="coerce", format="mixed")
        selected = frame.loc[ex.between(cfg["source_start"], cfg["evaluation_end"]) | (ex.isna() & (ann.isna() | ann.between(cfg["source_start"], cfg["evaluation_end"])))].copy()
        try:
            persist(selected, output / audit_dir / (symbol + ".csv.gz"), "CN", "stock_dividends", [version],
                    source_rows=len(frame), excluded_outside_declared_interval=len(frame) - len(selected),
                    source_start=cfg["source_start"], source_end=cfg["evaluation_end"])
        except ValueError as error:
            failures.append({"symbol": symbol, "error": "Dividend re-audit: " + str(error)})
    manifest["dividend_audit_directory"] = audit_dir
    manifest["failures"] = failures
    manifest.update(input_evidence())
    write_json(output / "manifest.json", manifest)
    if failures:
        raise ValueError(f"{len(failures)} securities blocked; none silently excluded; retained data is resumable")
    return manifest


def collect_us(output, cfg):
    import norgatedata as n
    base = ROOT / "artifacts/research/norgate/stock-universe-sp500-liquidity"
    mapping = {}
    for path in sorted(base.glob("membership-*.json")):
        mapping.update(read_clean_json(path))
    if not mapping:
        raise ValueError("Historical membership chunks missing")
    first, last = pd.Timestamp(cfg["evaluation_start"]), pd.Timestamp(cfg["evaluation_end"])
    wanted = {symbol: spans for symbol, spans in mapping.items() if any(pd.Timestamp(r["start"]) <= last and pd.Timestamp(r["end"]) >= first for r in spans)}
    if not wanted:
        raise ValueError("No historical eligible securities")
    rows = [{"date": r["start"], "end": r["end"], "symbol": symbol, "weight": 1} for symbol, spans in wanted.items() for r in spans]
    persist(pd.DataFrame(rows), output / "membership.csv.gz", "US", "historical_membership")
    calendar = xcals.get_calendar("XNYS", start=cfg["source_start"], end=cfg["evaluation_end"]).sessions.tz_localize(None)
    persist(pd.DataFrame({"date": calendar}), output / "calendar.csv", "US", "calendar")
    failures, terminals = [], {}
    for i, symbol in enumerate(sorted(wanted)):
        path = output / "prices" / (symbol + ".csv.gz")
        try:
            if path.exists():
                bars = read_clean_csv(path)
            else:
                raw = pd.DataFrame(n.price_timeseries(symbol, stock_price_adjustment_setting=n.StockPriceAdjustmentType.NONE, start_date=cfg["source_start"], end_date=cfg["evaluation_end"]))
                adj = pd.DataFrame(n.price_timeseries(symbol, stock_price_adjustment_setting=n.StockPriceAdjustmentType.TOTALRETURN, start_date=cfg["source_start"], end_date=cfg["evaluation_end"]))
                refs = [capture_response("Norgate", symbol + "-NONE", raw, "US", restricted=True),
                        capture_response("Norgate", symbol + "-TOTALRETURN", adj, "US", restricted=True)]
                if raw.empty or adj.empty or not np.array_equal(raw.Date, adj.Date):
                    raise ValueError("Raw/total-return session mismatch")
                bars = pd.DataFrame({"date": pd.to_datetime(raw.Date), "symbol": symbol, "volume": raw.Volume, "turnover": raw.Turnover})
                for field in ["open", "high", "low", "close"]:
                    bars[field] = raw[field.title()]
                    bars["adjusted_" + field] = adj[field.title()]
                bars = persist(bars, path, "US", "daily_ohlc", refs, adjustment="raw_NONE_and_provider_TOTALRETURN_analytical_units", volume_unit="shares", amount_unit="USD")
            final_quote = n.last_quoted_date(symbol)
            # Norgate returns None for still-listed securities; it is not a
            # malformed terminal date or a failed price capture.
            last_quote = str(final_quote)[:10] if final_quote is not None else None
            if last_quote and pd.Timestamp(last_quote) < last:
                terminals[symbol] = {"last_quote_date": last_quote, "verified_proceeds": False}
        except Exception as error:
            failures.append({"symbol": symbol, "error": str(error)[:250]})
        if (i + 1) % 10 == 0 or i + 1 == len(wanted):
            print(json.dumps({"market": "US", "processed": i + 1, "total": len(wanted), "failures": len(failures)}), flush=True)
    manifest = {"market": "US", "symbols": sorted(wanted), "symbol_count": len(wanted), "source_start": cfg["source_start"], "source_end": cfg["evaluation_end"],
                "membership_model": "historical_daily_index_intervals", "failures": failures, "terminal_securities": terminals,
                "config_hash": digest(ROOT / "config/granville_portfolio_v1.json"), "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), **input_evidence()}
    write_json(output / "manifest.json", manifest)
    if failures:
        raise ValueError(f"{len(failures)} securities blocked; not dropped from historical universe")
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--market", choices=["US", "CN"], required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args()
    cfg = json.loads((ROOT / "config/granville_portfolio_v1.json").read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result = (collect_us if args.market == "US" else collect_cn)(args.output_dir, cfg)
    print(json.dumps({"market": result["market"], "status": "captured", "symbol_count": result["symbol_count"]}))


if __name__ == "__main__":
    main()
