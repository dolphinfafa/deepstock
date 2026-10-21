"""Deepstock-owned credentials and provider evidence capture."""
from __future__ import annotations
import os
from pathlib import Path
import re
import sqlite3
import uuid
from datetime import datetime, timezone
import pandas as pd
from deepstock.massive import load_env_value
from deepstock.data.store import ROOT, DataStore, read_clean_csv

_pro = None


def permission_denied(error):
    # Rate limits/credits, timeouts and invalid tokens are not endpoint permissions.
    return bool(re.search(r'没有访问该接口的权限|没有.*接口.*权限|权限不足|无权访问该接口|'
                          r'permission denied|insufficient permissions?|no permission to access',
                          str(error), re.I))


class CapturedTushare:
    def __init__(self, client, *, state_path=None):
        self.client = client
        self.state_path = Path(state_path) if state_path is not None else ROOT / 'artifacts/providers/tushare/endpoint_state.sqlite3'

    def _disabled(self, endpoint):
        if not self.state_path.exists():
            return False
        with sqlite3.connect(self.state_path, timeout=30) as db:
            db.execute('CREATE TABLE IF NOT EXISTS disabled_endpoint (endpoint TEXT PRIMARY KEY, disabled_at TEXT NOT NULL, reason TEXT NOT NULL)')
            return db.execute('SELECT 1 FROM disabled_endpoint WHERE endpoint=?', (endpoint,)).fetchone() is not None

    def _disable(self, endpoint):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.state_path, timeout=30) as db:
            db.execute('CREATE TABLE IF NOT EXISTS disabled_endpoint (endpoint TEXT PRIMARY KEY, disabled_at TEXT NOT NULL, reason TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO disabled_endpoint VALUES (?, ?, ?)',
                       (endpoint, datetime.now(timezone.utc).isoformat(), 'explicit_permission_denied'))

    def __getattr__(self, endpoint):
        def fetch(*args, **kwargs):
            if os.getenv("DEEPSTOCK_AUCTION_OFFLINE") == "true":
                raise RuntimeError("Offline auction study cannot call providers")
            # Tushare exposes both pro.stk_mins(...) and
            # pro.query("stk_mins", ...); neither may bypass the shared quota.
            actual_endpoint = (args[0] if args else kwargs.get("api_name", endpoint)) if endpoint == "query" else endpoint
            if self._disabled(actual_endpoint):
                raise PermissionError(f'Tushare endpoint {actual_endpoint} disabled: explicit_permission_denied')
            if actual_endpoint == "stk_mins":
                from .quota import consume_minute_request
                consume_minute_request()
            try:
                frame = getattr(self.client, endpoint)(*args, **kwargs)
            except Exception as error:
                if permission_denied(error):
                    self._disable(actual_endpoint)
                    raise PermissionError(f'Tushare endpoint {actual_endpoint} disabled: explicit_permission_denied') from error
                raise
            if frame is None or frame.empty:
                if actual_endpoint == 'stk_mins':
                    from deepstock.data.capture import capture_response
                    version = capture_response('Tushare', 'stk_mins-empty', {
                        'response_kind': 'none' if frame is None else 'dataframe',
                        'columns': [] if frame is None else list(frame.columns), 'rows': [],
                        'request': {'args': list(args), **kwargs},
                    }, 'CN', restricted=True)
                    if frame is None:
                        frame = pd.DataFrame()
                    frame.attrs['raw_version'] = version
                return frame
            now = datetime.now(timezone.utc)
            path = ROOT / f"artifacts/providers/tushare/{actual_endpoint}/{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex}.csv.gz"
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(path, index=False, compression="gzip")
            result = DataStore().import_file(path, {"market": "CN", "provider": "Tushare", "endpoint": actual_endpoint,
                                                   "origin": "provider_response", "retrieved_at_utc": now.isoformat(),
                                                   "timezone": "Asia/Shanghai", "adjustment": "none"})
            partial = actual_endpoint == "stk_auction"
            if result["status"] != "ready" and not partial:
                raise RuntimeError(f"Provider dataset blocked: {endpoint}: {result['quality']['issues']}")
            clean = read_clean_csv(path, allow_quarantine=partial)
            if actual_endpoint == 'stk_mins':
                clean.attrs['raw_version'] = result['id']
                clean.attrs['provider_rows'] = len(frame)
            if clean.empty:
                raise RuntimeError(f"Provider dataset has no valid rows: {actual_endpoint}")
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
