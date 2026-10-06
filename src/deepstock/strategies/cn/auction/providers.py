"""Deepstock-owned credentials and provider evidence capture."""
from __future__ import annotations
import os
import uuid
from datetime import datetime, timezone
import pandas as pd
from deepstock.massive import load_env_value
from deepstock.data.store import ROOT, DataStore, read_clean_csv

_pro = None


class CapturedTushare:
    def __init__(self, client):
        self.client = client

    def __getattr__(self, endpoint):
        def fetch(*args, **kwargs):
            if os.getenv("DEEPSTOCK_AUCTION_OFFLINE") == "true":
                raise RuntimeError("Offline auction study cannot call providers")
            if endpoint == "stk_mins":
                from .quota import consume_minute_request
                consume_minute_request()
            # The inherited rt_min adapter calls pro.query("rt_min", ...).
            actual_endpoint = args[0] if endpoint == "query" and args else endpoint
            frame = getattr(self.client, endpoint)(*args, **kwargs)
            if frame is None or frame.empty:
                return frame
            now = datetime.now(timezone.utc)
            path = ROOT / f"artifacts/providers/tushare/{actual_endpoint}/{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex}.csv.gz"
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(path, index=False, compression="gzip")
            result = DataStore().import_file(path, {"market": "CN", "provider": "Tushare", "endpoint": endpoint,
                                                   "origin": "provider_response", "retrieved_at_utc": now.isoformat(),
                                                   "timezone": "Asia/Shanghai", "adjustment": "none"})
            if result["status"] != "ready":
                raise RuntimeError(f"Provider dataset blocked: {endpoint}: {result['quality']['issues']}")
            clean = read_clean_csv(path)
            # Adapter view restores provider date spelling; persisted clean version stays canonical.
            for col in frame:
                if col in clean and (col.endswith("date") or col == "cal_date") and frame[col].astype(str).str.fullmatch(r"\d{8}").all():
                    clean[col] = clean[col].astype(str).str.replace("-", "", regex=False)
            return clean
        return fetch


def get_pro():
    global _pro
    if _pro is None:
        import tushare as ts
        token = os.getenv("TUSHARE_TOKEN") or load_env_value(str(ROOT / ".env"), "TUSHARE_TOKEN")
        if not token:
            raise RuntimeError("TUSHARE_TOKEN not configured in Deepstock .env")
        _pro = CapturedTushare(ts.pro_api(token))
    return _pro
