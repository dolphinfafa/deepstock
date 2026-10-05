import json
from datetime import date

import httpx
import pandas as pd
import pytest

from scripts import download_cn_etf_tail_minutes as downloader
from test_tail_momentum import minute_data


def install_mock(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(downloader.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv("TUSHARE_TOKEN", "unit-test-secret")


def test_permission_failure_is_persisted_redacted_and_never_calls_stock_minutes(monkeypatch, tmp_path):
    called = []
    def handler(request):
        payload = json.loads(request.content)
        called.append(payload["api_name"])
        return httpx.Response(200, json={"code": 40203, "msg": "no permission unit-test-secret"})
    install_mock(monkeypatch, handler)
    result = downloader.download(tmp_path, date(2025, 1, 2), date(2025, 1, 3), probe_only=True)
    assert called == ["etf_mins"]
    assert result["status"] == "blocked"
    assert result["stock_minute_endpoint_called"] is False
    assert "unit-test-secret" not in (tmp_path / "access-report.json").read_text()
    assert not (tmp_path / "manifest.json").exists()


def test_public_recent_mapping_uses_share_units_and_independent_calendar(monkeypatch, tmp_path):
    bars, dates = minute_data(2)
    raw = {"rc": 0, "data": {"code": "510300", "trends": [
        ",".join(map(str, [row.timestamp.strftime("%Y-%m-%d %H:%M"), row.open, row.close, row.high, row.low,
                          row.volume / 100, row.amount, row.close])) for row in bars.itertuples()
    ]}}
    called = []
    def handler(request):
        payload = json.loads(request.content)
        called.append(payload["api_name"])
        return httpx.Response(200, json={"code": 0, "data": {"fields": ["cal_date"], "items": [[d.strftime("%Y%m%d")] for d in dates]}})
    install_mock(monkeypatch, handler)
    result = downloader.download_recent(tmp_path, imported_payload=raw)
    assert result["status"] == "downloaded_recent_price_only"
    assert result["sessions"] == 2
    assert called == ["trade_cal"]
    normalized = pd.read_csv(tmp_path / "minutes.csv.gz")
    assert normalized.iloc[0]["volume"] == bars.iloc[0]["volume"]
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["coverage_limit"] == "at_most_five_recent_sessions"
    assert manifest["corporate_action_status"] == "price_only_unverified"
    assert set(manifest["sha256"]) == {"raw-eastmoney.json", "minutes.csv.gz", "calendar.csv"}


def test_public_wrong_symbol_is_rejected():
    with pytest.raises(ValueError, match="wrong instrument"):
        downloader.normalize_eastmoney({"rc": 0, "data": {"code": "600000", "trends": ["bad"]}})


def test_real_history_endpoint_normalizes_without_changing_volume():
    raw = pd.DataFrame([{"ts_code": "510300.SH", "trade_time": "2025-01-02 09:31:00",
                         "open": 10, "high": 10, "low": 10, "close": 10, "vol": 1000, "amount": 10000}])
    assert downloader.normalize(raw).iloc[0]["volume"] == 1000
