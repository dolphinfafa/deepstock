import pandas as pd
import pytest
from test_web_platform import client, initialized_database, _login
from deepstock.data import DataStore
from deepstock.web.database import SessionLocal
from deepstock.web.data_catalog import register_manifest


def test_data_authentication_and_limit_guards(client):
    assert client.get("/api/data").status_code == 401
    headers, _ = _login(client)
    assert client.get("/api/data?limit=101", headers=headers).status_code == 422
    assert client.get("/api/data/not-a-version/preview", headers=headers).status_code == 404
    result = client.get("/api/data", headers=headers)
    assert result.status_code == 200 and "items" in result.json()


def test_preview_versions_raw_flags_and_no_arbitrary_paths(client, tmp_path, monkeypatch):
    source = tmp_path / "preview.csv"
    pd.DataFrame([dict(date="20240102", symbol="spy", adjusted_close=100)]).to_csv(source, index=False)
    store = DataStore(tmp_path, node="deepstock-server")
    m = store.import_file(source, {"market": "US", "provider": "test fixture"})
    with SessionLocal() as s:
        register_manifest(s, m)
        s.commit()
    monkeypatch.setattr("deepstock.web.app.DataStore", lambda root: store)
    headers, _ = _login(client)
    for layer in ["raw", "clean"]:
        response = client.get(f"/api/data/{m['id']}/preview?layer={layer}&limit=1", headers=headers)
        assert response.status_code == 200 and len(response.json()["rows"]) == 1
    clean = client.get(f"/api/data/{m['id']}/preview", headers=headers).json()
    assert "_raw_row" in clean["columns"] and "_quality_flags" in clean["columns"]
    assert client.get(f"/api/data/{m['id']}/preview?layer=../../etc", headers=headers).status_code == 422
    source.write_text("new source, old snapshot still intact")
    assert client.get(f"/api/data/{m['id']}/preview?layer=raw", headers=headers).status_code == 200


def test_licensed_node_metadata_cannot_preview_or_publish_rows(client):
    body = {"id": "a" * 64, "node": "quant-computer", "market": "US", "provider": "Norgate Data", "source_name": "licensed.csv",
            "preview_allowed": False, "status": "ready", "rows": 1, "quality": {"issues": []}}
    assert client.post("/api/agent/research/data-versions", json=[body]).status_code == 401
    headers = {"Authorization": "Bearer test-node-token"}
    assert client.post("/api/agent/research/data-versions", headers=headers, json=[body]).status_code == 200
    assert client.post("/api/agent/research/data-versions", headers=headers, json=[{**body, "rows_data": [[1, 2]]}]).status_code == 422
    auth, _ = _login(client)
    response = client.get(f"/api/data/{body['id']}/preview", headers=auth)
    assert response.status_code == 200 and response.json()["rows"] == []


def test_all_strategy_annualized_slots_and_cohorts_never_fake_cagr(client):
    headers, _ = _login(client)
    result = client.get("/api/strategies", headers=headers).json()
    rows = result["strategies"] if isinstance(result, dict) else result
    for row in rows:
        assert "annualization" in row
    auction = next(r for r in rows if r["id"] == "csi300_opening_auction")
    assert auction["annualization"]["value"] is None
    tail = next(r for r in rows if r["id"] == "cn_etf_tail_momentum")
    assert tail["annualization"]["value"] is None
