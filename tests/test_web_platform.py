from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path


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
    with SessionLocal() as session:
        first = ingest_all(session)
        second = ingest_all(session)
        assert first["catalog"] == second["catalog"] == {
            "strategies": 7,
            "metrics": 32,
        }
        assert session.scalar(select(func.count(Strategy.id))) == 7
        assert session.scalar(select(func.count(Metric.id))) >= 32
        assert session.scalar(select(func.count(ResearchReport.id))) >= 7
        assert session.scalar(select(func.count(ProgressEvent.id))) == 19


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
