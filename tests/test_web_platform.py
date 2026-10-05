from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path
import shutil
import importlib
import json
from dataclasses import replace


_TEST_DIR = Path(tempfile.mkdtemp(prefix="deepstock-web-tests-"))
_DATABASE_PATH = _TEST_DIR / "deepstock.sqlite3"
os.environ["DEEPSTOCK_DATABASE_URL"] = f"sqlite:///{_DATABASE_PATH}"
os.environ["DEEPSTOCK_NODE_TOKEN"] = "test-node-token"
os.environ["DEEPSTOCK_BOOTSTRAP_USERNAME"] = "admin"
os.environ["DEEPSTOCK_BOOTSTRAP_PASSWORD"] = "admin"
os.environ["DEEPSTOCK_LIVE_TRADING_ENABLED"] = "false"
os.environ["DEEPSTOCK_GLOBAL_KILL_SWITCH"] = "true"

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from deepstock.web.app import app, events
from deepstock.web.config import settings
from deepstock.web.database import SessionLocal, configure_database
from deepstock.web.ingestion import ingest_all
from deepstock.web.models import (
    ExecutionAuthorization,
    ExecutionPlan,
    Metric,
    ProgressEvent,
    ResearchReport,
    Strategy,
    StrategyVersion,
    SystemSetting,
    User,
    utcnow,
)
from deepstock.web.security import ensure_bootstrap_user


_TEST_DATABASE_URL = f"sqlite:///{_DATABASE_PATH}"
object.__setattr__(settings, "database_url", _TEST_DATABASE_URL)
object.__setattr__(settings, "node_token", "test-node-token")
configure_database(_TEST_DATABASE_URL)


@pytest.fixture(scope="module", autouse=True)
def initialized_database() -> None:
    config = Config(str(settings.project_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", _TEST_DATABASE_URL)
    command.upgrade(config, "head")
    with SessionLocal() as session:
        ensure_bootstrap_user(session)
        ingest_all(session)


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app, base_url="https://testserver") as value:
        yield value


def _login(client: TestClient) -> tuple[dict[str, str], str]:
    response = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200
    token = response.cookies.get("deepstock_session")
    assert token
    return {"Cookie": f"deepstock_session={token}"}, response.json()["csrf_token"]


def _strategy_hash(strategy_id: str = "adaptive_defensive_etf") -> str:
    with SessionLocal() as session:
        strategy = session.get(Strategy, strategy_id)
        assert strategy is not None
        version = session.scalar(
            select(StrategyVersion).where(
                StrategyVersion.strategy_id == strategy_id,
                StrategyVersion.version == strategy.current_version,
            )
        )
        assert version is not None
        return version.config_hash


def _authorize_paper(client: TestClient, headers: dict[str, str], csrf: str) -> str:
    expires = date.today() + timedelta(days=5)
    response = client.post(
        "/api/execution/authorizations",
        headers={**headers, "X-CSRF-Token": csrf},
        json={
            "strategy_id": "adaptive_defensive_etf",
            "mode": "paper",
            "notional_cap_usd": 900,
            "expires_on": expires.isoformat(),
            "password": "admin",
            "confirmation": (
                f"PAPER adaptive_defensive_etf 900.00 {expires.isoformat()}"
            ),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_catalog_ingestion_is_idempotent() -> None:
    catalog = json.loads((settings.project_root / "config/strategy_catalog.json").read_text())["strategies"]
    with SessionLocal() as session:
        first = ingest_all(session)
        second = ingest_all(session)
        assert first["catalog"] == second["catalog"] == {
            "strategies": len(catalog),
            "metrics": sum(len(s["metrics"]) for s in catalog),
        }
        assert session.scalar(select(func.count(Strategy.id))) == len(catalog)
        assert session.scalar(select(func.count(Metric.id))) >= 32
        assert session.scalar(select(func.count(ResearchReport.id))) >= 7
        assert session.scalar(select(func.count(ProgressEvent.id)).where(ProgressEvent.source == "catalog")) == sum(len(s["progress"]) for s in catalog)


def test_frozen_strategy_is_separate_and_cannot_be_authorized(client: TestClient) -> None:
    headers, csrf = _login(client)
    active = client.get("/api/strategies", headers=headers).json()
    frozen = client.get("/api/strategies?archived=true", headers=headers).json()
    assert len(active) == 7
    assert len(frozen) == 1
    ahl = frozen[0]
    assert ahl["is_archived"] is True
    assert all(row["id"] != ahl["id"] for row in active)
    assert client.get(f"/api/strategies/{ahl['id']}", headers=headers).status_code == 200
    dashboard = client.get("/api/dashboard", headers=headers).json()
    assert dashboard["counts"]["strategies"] == 7
    assert dashboard["counts"]["archived"] == 1
    assert len(dashboard["strategies"]) == 7
    expires = date.today() + timedelta(days=5)
    response = client.post("/api/execution/authorizations", headers={**headers, "X-CSRF-Token": csrf}, json={
        "strategy_id": ahl["id"], "mode": "paper", "notional_cap_usd": 100,
        "expires_on": expires.isoformat(), "password": "admin",
        "confirmation": f"PAPER {ahl['id']} 100.00 {expires.isoformat()}",
    })
    assert response.status_code == 409
    response = client.post("/api/execution/plans", headers={**headers, "X-CSRF-Token": csrf}, json={
        "strategy_id": ahl["id"], "mode": "paper", "data_date": date.today().isoformat(),
        "config_hash": _strategy_hash(ahl["id"]),
        "orders": [{"symbol": "ES", "action": "BUY", "quantity": 1, "limit_price": 100}],
    })
    assert response.status_code == 409
    with SessionLocal() as session:
        version = session.scalar(select(StrategyVersion).where(StrategyVersion.strategy_id == ahl["id"]))
        assert version.active is False


def test_strategy_markets_filter_exact_categories_and_preserve_freeze(client: TestClient) -> None:
    headers, _ = _login(client)
    us = client.get("/api/strategies?market=US", headers=headers).json()
    cn = client.get("/api/strategies?market=CN", headers=headers).json()
    both = client.get("/api/strategies?market=Both", headers=headers).json()
    assert len(us) == 5 and all(row["market"] == "US" for row in us)
    assert {row["id"] for row in cn} == {"cn_etf_tail_momentum", "csi300_opening_auction"}
    assert all(row["market"] == "CN" for row in cn)
    assert both == []
    frozen = client.get("/api/strategies?market=US&archived=true", headers=headers).json()
    assert [row["id"] for row in frozen] == ["ahl_global_futures_trend"]
    assert frozen[0]["execution_status"] == "frozen_research_no_orders"
    assert client.get("/api/strategies?market=Global", headers=headers).status_code == 422
    assert client.get("/api/strategies?market=CN").status_code == 401


def test_tail_ingestion_publishes_preregistered_case_not_best_and_handles_blocked_data(tmp_path, monkeypatch):
    from test_tail_momentum import write_unit_inputs
    from scripts.run_cn_etf_tail_momentum import run_research
    from deepstock.web.ingestion import ingest_tail_momentum
    from deepstock.web.models import ResearchRun
    module = importlib.import_module("deepstock.web.ingestion")
    (tmp_path / "config").mkdir()
    (tmp_path / "config/strategy_registry.json").write_text(json.dumps({"strategies": [
        {"strategy_id": "cn_etf_tail_momentum", "execution_status": "research_only_no_orders"}
    ]}))  # Isolated active unit fixture; production stays paused.
    monkeypatch.setattr(module, "settings", replace(settings, project_root=tmp_path))
    data = tmp_path / "fixture-data"
    write_unit_inputs(data)
    output = tmp_path / "artifacts/research/cn-etf-tail-momentum/latest"
    report = run_research(data, output)
    # Deliberately make a control seem much better. The published primary is fixed.
    report["cases"][-1]["summary"]["total_return"] = 999
    (output / "research.json").write_text(json.dumps(report))
    with SessionLocal() as session:
        result = ingest_tail_momentum(session)
        assert ingest_tail_momentum(session)["run_id"] == result["run_id"]
        run = session.get(ResearchRun, result["run_id"])
        assert run.run_type == "tail_momentum_pilot"
        assert len(run.details["cases"]) == 12
        metrics = session.scalars(select(Metric).where(Metric.run_id == run.id)).all()
        total = next(m for m in metrics if m.name == "total_return")
        assert total.value == report["cases"][0]["summary"]["total_return"]
        assert total.scope == "short_sample_pilot"
        assert all(m.name not in {"annualized_return", "sharpe_ratio"} for m in metrics)
        report["cases"].pop()
        (output / "research.json").write_text(json.dumps(report))
        with pytest.raises(ValueError, match="twelve"):
            ingest_tail_momentum(session)
        run_research(tmp_path / "missing", output)
        blocked = ingest_tail_momentum(session)
        assert blocked["status"] == "blocked_data"
        assert session.scalars(select(Metric).where(Metric.run_id == blocked["run_id"])).all() == []
        assert session.get(Strategy, "cn_etf_tail_momentum").live_eligible is False


def test_node_observation_publication_is_authenticated_validated_and_replay_safe(client, tmp_path, monkeypatch):
    from test_observation_reporting import valid_bundle
    source_root = settings.project_root
    (tmp_path / "config").mkdir()
    shutil.copy2(source_root / "config/strategy_registry.json", tmp_path / "config/strategy_registry.json")
    isolated_settings = replace(settings, project_root=tmp_path)
    monkeypatch.setattr(importlib.import_module("deepstock.web.app"), "settings", isolated_settings)
    monkeypatch.setattr(importlib.import_module("deepstock.web.ingestion"), "settings", isolated_settings)
    payload = valid_bundle()
    endpoint = "/api/agent/research/defensive-observation"
    assert client.post(endpoint, json=payload).status_code == 401
    node_headers = {"Authorization": "Bearer test-node-token"}
    response = client.post(endpoint, headers=node_headers, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["shadow_sessions"] == 2
    assert (tmp_path / "artifacts/defensive_node/latest.json").exists()
    assert client.post(endpoint, headers=node_headers, json=payload).status_code == 409
    payload["snapshot"]["shadow_sessions"] = 99
    assert client.post(endpoint, headers=node_headers, json=payload).status_code == 400
    with SessionLocal() as session:
        run = session.get(__import__("deepstock.web.models", fromlist=["ResearchRun"]).ResearchRun, response.json()["run_id"])
        assert run.data_end == "2026-09-01"
        assert run.details["assessment"]["paper_authorized"] is False


def test_authentication_session_csrf_and_rate_limit(client: TestClient) -> None:
    assert client.get("/api/strategies").status_code == 401
    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.username == "admin"))
        assert user is not None
        assert user.password_hash.startswith("$argon2id$")

    for _ in range(5):
        response = client.post(
            "/api/auth/login",
            json={"username": "rate-limit-probe", "password": "incorrect"},
        )
        assert response.status_code == 401
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "rate-limit-probe", "password": "incorrect"},
        ).status_code
        == 429
    )

    headers, csrf = _login(client)
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    assert client.get("/api/strategies", headers=headers).status_code == 200
    assert (
        client.post(
            "/api/execution/settings",
            headers=headers,
            json={"password": "admin", "global_kill_switch": True},
        ).status_code
        == 403
    )
    response = client.post(
        "/api/execution/settings",
        headers={**headers, "X-CSRF-Token": csrf},
        json={
            "password": "admin",
            "live_trading_enabled": True,
            "confirmation": "ENABLE LIVE TRADING 1000.00",
        },
    )
    assert response.status_code == 409


def test_reports_and_notes_are_authenticated_read_only(client: TestClient) -> None:
    headers, _ = _login(client)
    reports = client.get("/api/reports", headers=headers)
    assert reports.status_code == 200
    assert len(reports.json()) >= 7
    detail = client.get(f"/api/reports/{reports.json()[0]['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["content"]
    assert client.get("/api/notes", headers=headers).status_code == 200
    assert client.post("/api/notes", headers=headers, json={}).status_code == 405


def test_paper_plan_is_deterministic_and_node_reports_are_validated(
    client: TestClient,
) -> None:
    headers, csrf = _login(client)
    authorization_id = _authorize_paper(client, headers, csrf)
    plan_payload = {
        "strategy_id": "adaptive_defensive_etf",
        "mode": "paper",
        "data_date": date.today().isoformat(),
        "config_hash": _strategy_hash(),
        "authorization_id": authorization_id,
        "orders": [
            {
                "symbol": "SGOV",
                "action": "BUY",
                "quantity": 1,
                "order_type": "LMT",
                "limit_price": 100,
                "currency": "USD",
                "exchange": "SMART",
            }
        ],
    }
    write_headers = {**headers, "X-CSRF-Token": csrf}
    first = client.post("/api/execution/plans", headers=write_headers, json=plan_payload)
    second = client.post("/api/execution/plans", headers=write_headers, json=plan_payload)
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    assert (
        client.get(
            "/api/agent/plans?mode=paper",
            headers={"Authorization": "Bearer wrong-token"},
        ).status_code
        == 401
    )
    node_headers = {"Authorization": "Bearer test-node-token"}
    plans = client.get("/api/agent/plans?mode=paper", headers=node_headers)
    assert plans.status_code == 200
    plan = next(row for row in plans.json() if row["id"] == first.json()["id"])
    order = plan["orders"][0]
    assert len(order["order_id"]) == 32
    assert (
        client.post(
            f"/api/agent/plans/{plan['id']}/claim", headers=node_headers
        ).status_code
        == 200
    )
    snapshot = client.post(
        "/api/agent/snapshot",
        headers=node_headers,
        json={
            "mode": "paper",
            "account_hash": "0123456789abcdef",
            "net_liquidation": 10000,
            "cash": 9900,
            "buying_power": 19800,
            "currency": "USD",
            "captured_at": utcnow().isoformat(),
            "positions": [
                {
                    "symbol": "SGOV",
                    "quantity": 1,
                    "market_price": 100,
                    "market_value": 100,
                    "average_cost": 100,
                    "currency": "USD",
                }
            ],
        },
    )
    assert snapshot.status_code == 200

    report = {
        "order_id": order["order_id"],
        "plan_id": plan["id"],
        "broker_order_id": "42",
        "symbol": "SGOV",
        "action": "BUY",
        "quantity": 1,
        "order_type": "LMT",
        "limit_price": 100,
        "status": "FILLED",
        "filled_quantity": 1,
        "average_fill_price": 99.99,
    }
    mismatched = client.post(
        "/api/agent/orders", headers=node_headers, json={**report, "symbol": "SPY"}
    )
    assert mismatched.status_code == 409
    assert client.post("/api/agent/orders", headers=node_headers, json=report).status_code == 200
    overview = client.get("/api/live/overview", headers=headers)
    assert overview.status_code == 200
    assert overview.json()["positions"][0]["symbol"] == "SGOV"
    with SessionLocal() as session:
        stored = session.get(ExecutionPlan, plan["id"])
        assert stored is not None and stored.status == "completed"


def test_expired_and_hash_mismatched_authorizations_are_rejected(
    client: TestClient,
) -> None:
    headers, csrf = _login(client)
    authorization_id = _authorize_paper(client, headers, csrf)
    with SessionLocal() as session:
        authorization = session.get(ExecutionAuthorization, authorization_id)
        assert authorization is not None
        authorization.expires_at = utcnow() - timedelta(seconds=1)
        session.commit()
    base = {
        "strategy_id": "adaptive_defensive_etf",
        "mode": "paper",
        "data_date": date.today().isoformat(),
        "authorization_id": authorization_id,
        "orders": [
            {
                "symbol": "SGOV",
                "action": "BUY",
                "quantity": 2,
                "order_type": "LMT",
                "limit_price": 100,
            }
        ],
    }
    write_headers = {**headers, "X-CSRF-Token": csrf}
    expired = client.post(
        "/api/execution/plans",
        headers=write_headers,
        json={**base, "config_hash": _strategy_hash()},
    )
    assert expired.status_code == 409

    valid_authorization = _authorize_paper(client, headers, csrf)
    mismatch = client.post(
        "/api/execution/plans",
        headers=write_headers,
        json={
            **base,
            "authorization_id": valid_authorization,
            "config_hash": "0" * 64,
        },
    )
    assert mismatch.status_code == 409


def test_live_execution_is_fail_closed_and_capped(client: TestClient) -> None:
    headers, csrf = _login(client)
    write_headers = {**headers, "X-CSRF-Token": csrf}
    expires = date.today() + timedelta(days=5)
    with SessionLocal() as session:
        strategy = session.get(Strategy, "adaptive_defensive_etf")
        assert strategy is not None
        strategy.live_eligible = True
        for key, value in (
            ("live_trading_enabled", True),
            ("global_kill_switch", False),
            ("email_configured", True),
            ("email_test_passed", True),
        ):
            setting = session.get(SystemSetting, key)
            assert setting is not None
            setting.value = value
        session.commit()
    authorization = client.post(
        "/api/execution/authorizations",
        headers=write_headers,
        json={
            "strategy_id": "adaptive_defensive_etf",
            "mode": "live",
            "notional_cap_usd": 1000,
            "expires_on": expires.isoformat(),
            "password": "admin",
            "confirmation": (
                f"LIVE adaptive_defensive_etf 1000.00 {expires.isoformat()}"
            ),
        },
    )
    assert authorization.status_code == 200
    payload = {
        "strategy_id": "adaptive_defensive_etf",
        "mode": "live",
        "data_date": date.today().isoformat(),
        "config_hash": _strategy_hash(),
        "authorization_id": authorization.json()["id"],
        "orders": [
            {
                "symbol": "SGOV",
                "action": "BUY",
                "quantity": 11,
                "order_type": "LMT",
                "limit_price": 100,
            }
        ],
    }
    over_cap = client.post("/api/execution/plans", headers=write_headers, json=payload)
    assert over_cap.status_code == 409
    assert "global live cap" in over_cap.text

    with SessionLocal() as session:
        setting = session.get(SystemSetting, "global_kill_switch")
        assert setting is not None
        setting.value = True
        session.commit()
    killed = client.post(
        "/api/execution/plans",
        headers=write_headers,
        json={
            **payload,
            "orders": [{**payload["orders"][0], "quantity": 1}],
        },
    )
    assert killed.status_code == 409
    assert "kill switch" in killed.text

    with SessionLocal() as session:
        strategy = session.get(Strategy, "adaptive_defensive_etf")
        assert strategy is not None
        strategy.live_eligible = False
        session.get(SystemSetting, "live_trading_enabled").value = False
        session.get(SystemSetting, "global_kill_switch").value = True
        session.get(SystemSetting, "email_configured").value = False
        session.get(SystemSetting, "email_test_passed").value = False
        session.commit()


def test_sse_endpoint_returns_event_stream() -> None:
    response = asyncio.run(events(_auth=object()))  # type: ignore[arg-type]
    assert response.media_type == "text/event-stream"
