"""Local securities and versioned daily history; no external application database."""
from __future__ import annotations
import hashlib
import os
from datetime import date
from pathlib import Path
import pandas as pd
from deepstock.data.store import ROOT, read_clean_csv, DataStore
from deepstock.data.inventory import metadata_for


class SessionLocal:
    """Compatibility context for the migrated research code's local repository."""
    def close(self):
        pass


def load_members(codes):
    from .data import ShortTermMember
    path = ROOT / "artifacts/auction_history_tushare/security_master.csv"
    if not path.exists():
        raise RuntimeError("Local security master missing; complete the one-time auction handover")
    frame = read_clean_csv(path, dtype={"stock_code": str})
    missing = {str(value)[:6].zfill(6) for value in codes}.difference(frame.stock_code)
    if missing and os.getenv("DEEPSTOCK_AUCTION_OFFLINE") != "true":
        from .providers import get_pro
        response = get_pro().stock_basic(exchange="", list_status="L", fields="ts_code,name,industry")
        additions = []
        for _, row in response.iterrows():
            code = str(row.ts_code)[:6]
            if code in missing:
                additions.append({"stock_code": code, "company_id": "CN_" + code,
                                  "security_id": hashlib.md5(("CN_" + code).encode()).hexdigest()[:16],
                                  "name": row["name"], "industry_name": row.get("industry")})
        if additions:
            frame = pd.concat([frame, pd.DataFrame(additions)], ignore_index=True)
            frame.to_csv(path, index=False)
            DataStore().import_file(path, {**metadata_for(path), "origin": "local_reference_update"})
            frame = read_clean_csv(path, dtype={"stock_code": str})
    frame = frame.set_index("stock_code")
    members = []
    for code in sorted({str(value)[:6].zfill(6) for value in codes}):
        if code not in frame.index:
            continue
        row = frame.loc[code]
        members.append(ShortTermMember(company_id=str(row.company_id), security_id=str(row.security_id), stock_code=code,
                                       name=str(row["name"]), industry_name=None if pd.isna(row.industry_name) else str(row.industry_name)))
    return tuple(members)


def load_bars(members, start: date, end: date | None):
    frame = read_clean_csv(ROOT / "artifacts/auction_history_tushare/base_daily_bars.csv.gz", dtype={"stock_code": str})
    frame["trade_date"] = pd.to_datetime(frame.trade_date).dt.date
    frame = frame[frame.stock_code.isin({m.stock_code for m in members}) & (frame.trade_date >= start)]
    if end:
        frame = frame[frame.trade_date <= end]
    lookup = {m.stock_code: m for m in members}
    for column, attr in [("company_id", "company_id"), ("security_id", "security_id"), ("company_name", "name"), ("industry_name", "industry_name")]:
        frame[column] = frame.stock_code.map(lambda code: getattr(lookup[code], attr))
    return frame
