from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from deepstock.web.alerts import create_alert, deliver_wechat
from deepstock.web.config import settings
from deepstock.web.database import SessionLocal, get_session
from deepstock.web.ingestion import ingest_all
from deepstock.web.models import (
    AccountSnapshot,
    Alert,
    AuditLog,
    AuthSession,
    DataSourceStatus,
    ExecutionAuthorization,
    ExecutionPlan,
    JobStatus,
    Metric,
    OrderRecord,
    PositionSnapshot,
    ProgressEvent,
    ResearchNote,
    ResearchReport,
    ResearchRun,
    Strategy,
    StrategyVersion,
    SystemSetting,
    User,
    utcnow,
)
from deepstock.web.security import (
    change_password,
    create_auth_session,
    ensure_bootstrap_user,
    login_rate_limited,
    record_login_attempt,
    resolve_auth_session,
    revoke_auth_session,
    token_hash,
    verify_password,
)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)


class AlertAckRequest(BaseModel):
    acknowledged: bool = True


class ExecutionSettingsRequest(BaseModel):
    password: str
    live_trading_enabled: bool | None = None
    global_kill_switch: bool | None = None
    confirmation: str = ""


class AuthorizationRequest(BaseModel):
    strategy_id: str
    mode: str
    notional_cap_usd: float = Field(gt=0)
    expires_on: date
    password: str
    confirmation: str


class PlanOrder(BaseModel):
    symbol: str = Field(pattern=r"^[A-Z0-9.\-]{1,20}$")
    action: str
    quantity: float = Field(gt=0)
    order_type: str = "LMT"
    limit_price: float = Field(gt=0)
    currency: str = "USD"
    exchange: str = "SMART"


class PlanRequest(BaseModel):
    strategy_id: str
    mode: str
    data_date: date
    config_hash: str
    authorization_id: str | None = None
    orders: list[PlanOrder] = Field(min_length=1)


class PositionInput(BaseModel):
    symbol: str = Field(pattern=r"^[A-Z0-9.\-]{1,20}$")
    quantity: float
    market_price: float | None = None
    market_value: float | None = None
    average_cost: float | None = None
    currency: str = Field(default="USD", max_length=10)


class AgentSnapshotRequest(BaseModel):
    mode: str
    account_hash: str = Field(min_length=16, max_length=64)
    net_liquidation: float | None = None
    cash: float | None = None
    buying_power: float | None = None
    currency: str = "USD"
    captured_at: datetime
    source_node: str = "quant-computer"
    positions: list[PositionInput] = Field(default_factory=list)


class AgentOrderRequest(BaseModel):
    order_id: str
    plan_id: str
    broker_order_id: str | None = None
    symbol: str
    action: str
    quantity: float
    order_type: str = "LMT"
    limit_price: float | None = None
    status: str
    filled_quantity: float = 0
    average_fill_price: float | None = None
    last_error: str | None = None


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _get_setting(session: Session, key: str, default: Any = None) -> Any:
    row = session.get(SystemSetting, key)
    return row.value if row is not None else default


def _set_setting(session: Session, key: str, value: Any) -> None:
    row = session.get(SystemSetting, key)
    if row is None:
        session.add(SystemSetting(key=key, value=value))
    else:
        row.value = value


def _audit(
    session: Session,
    actor: str,
    action: str,
    object_type: str,
    object_id: str | None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            actor=actor,
            action=action,
            object_type=object_type,
            object_id=object_id,
            details=details or {},
        )
    )


def _latest_run(session: Session, strategy_id: str) -> ResearchRun | None:
    return session.scalar(
        select(ResearchRun)
        .where(ResearchRun.strategy_id == strategy_id)
        .order_by(desc(ResearchRun.as_of_date), desc(ResearchRun.created_at))
        .limit(1)
    )


def _metric_payload(session: Session, run_id: str | None) -> list[dict[str, Any]]:
    if not run_id:
        return []
    metrics = session.scalars(
        select(Metric).where(Metric.run_id == run_id).order_by(Metric.id)
    ).all()
    return [
        {
            "name": metric.name,
            "scope": metric.scope,
            "value": metric.value,
            "text_value": metric.text_value,
            "unit": metric.unit,
            "benchmark": metric.benchmark,
        }
        for metric in metrics
    ]


def _strategy_summary(session: Session, strategy: Strategy) -> dict[str, Any]:
    run = _latest_run(session, strategy.id)
    return {
        "id": strategy.id,
        "display_name": strategy.display_name,
        "code": strategy.code,
        "market": strategy.market,
        "asset_class": strategy.asset_class,
        "summary": strategy.summary,
        "status": strategy.status,
        "execution_status": strategy.execution_status,
        "current_version": strategy.current_version,
        "live_eligible": strategy.live_eligible,
        "updated_at": _iso(strategy.updated_at),
        "latest_run": None
        if run is None
        else {
            "id": run.id,
            "run_type": run.run_type,
            "status": run.status,
            "as_of_date": run.as_of_date,
            "summary": run.summary,
            "metrics": _metric_payload(session, run.id),
        },
    }


def _auth_dependency(
    session: Session = Depends(get_session),
    raw_token: str | None = Cookie(default=None, alias=settings.session_cookie),
) -> AuthSession:
    auth_session = resolve_auth_session(session, raw_token)
    if auth_session is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return auth_session


def _csrf_dependency(
    auth_session: AuthSession = Depends(_auth_dependency),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> AuthSession:
    if not csrf_token or not secrets.compare_digest(auth_session.csrf_token, csrf_token):
        raise HTTPException(status_code=403, detail="invalid CSRF token")
    return auth_session


def _node_dependency(authorization: str | None = Header(default=None)) -> str:
    if not settings.node_token:
        raise HTTPException(status_code=503, detail="execution node token is not configured")
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="node authentication required")
    supplied = authorization[len(prefix) :]
    if not secrets.compare_digest(token_hash(supplied), token_hash(settings.node_token)):
        raise HTTPException(status_code=401, detail="invalid node token")
    return "quant-computer"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    with SessionLocal() as session:
        ensure_bootstrap_user(session)
        ingest_all(session)
    yield


app = FastAPI(title="Deepstock", version="1.0.0", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "time": utcnow().isoformat()}


@app.post("/api/auth/login")
def login(payload: LoginRequest, request: Request, response: Response, session: Session = Depends(get_session)):
    remote_addr = request.client.host if request.client else "unknown"
    if login_rate_limited(session, payload.username, remote_addr):
        raise HTTPException(status_code=429, detail="too many failed login attempts")
    user = session.scalar(select(User).where(User.username == payload.username))
    if user is None or not user.active or not verify_password(user.password_hash, payload.password):
        record_login_attempt(session, payload.username, remote_addr, False)
        raise HTTPException(status_code=401, detail="invalid username or password")
    record_login_attempt(session, payload.username, remote_addr, True)
    raw_token, auth_session = create_auth_session(
        session,
        user,
        user_agent=request.headers.get("user-agent"),
        remote_addr=remote_addr,
    )
    response.set_cookie(
        settings.session_cookie,
        raw_token,
        max_age=settings.session_hours * 3600,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/deepstock",
    )
    return {
        "user": {"username": user.username, "role": user.role},
        "csrf_token": auth_session.csrf_token,
        "expires_at": _iso(auth_session.expires_at),
    }


@app.get("/api/auth/me")
def me(auth_session: AuthSession = Depends(_auth_dependency)) -> dict[str, Any]:
    return {
        "user": {"username": auth_session.user.username, "role": auth_session.user.role},
        "csrf_token": auth_session.csrf_token,
        "expires_at": _iso(auth_session.expires_at),
    }


@app.post("/api/auth/logout")
def logout(
    response: Response,
    session: Session = Depends(get_session),
    raw_token: str | None = Cookie(default=None, alias=settings.session_cookie),
    _auth: AuthSession = Depends(_csrf_dependency),
) -> dict[str, str]:
    revoke_auth_session(session, raw_token)
    response.delete_cookie(settings.session_cookie, path="/deepstock")
    return {"status": "logged_out"}


@app.post("/api/auth/change-password")
def password_change(
    payload: PasswordChangeRequest,
    response: Response,
    session: Session = Depends(get_session),
    auth_session: AuthSession = Depends(_csrf_dependency),
) -> dict[str, str]:
    try:
        change_password(
            session, auth_session.user, payload.current_password, payload.new_password
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    response.delete_cookie(settings.session_cookie, path="/deepstock")
    return {"status": "password_changed", "reauthenticate": "true"}


@app.get("/api/dashboard")
def dashboard(
    session: Session = Depends(get_session),
    _auth: AuthSession = Depends(_auth_dependency),
) -> dict[str, Any]:
    strategies = session.scalars(select(Strategy).order_by(Strategy.display_name)).all()
    jobs = session.scalars(select(JobStatus).order_by(JobStatus.display_name)).all()
    alerts = session.scalars(
        select(Alert)
        .where(Alert.acknowledged.is_(False))
        .order_by(desc(Alert.created_at))
        .limit(8)
    ).all()
    account = session.scalar(
        select(AccountSnapshot).order_by(desc(AccountSnapshot.captured_at)).limit(1)
    )
    return {
        "strategies": [_strategy_summary(session, strategy) for strategy in strategies],
        "counts": {
            "strategies": len(strategies),
            "shadow": sum("shadow" in strategy.execution_status for strategy in strategies),
            "paper": sum("paper_active" == strategy.execution_status for strategy in strategies),
            "live": sum("live_active" == strategy.execution_status for strategy in strategies),
            "unacknowledged_alerts": len(alerts),
        },
        "jobs": [
            {
                "id": job.id,
                "display_name": job.display_name,
                "status": job.status,
                "message": job.message,
                "last_finished_at": _iso(job.last_finished_at),
                "next_run_at": _iso(job.next_run_at),
            }
            for job in jobs
        ],
        "alerts": [
            {
                "id": alert.id,
                "severity": alert.severity,
                "title": alert.title,
                "message": alert.message,
                "created_at": _iso(alert.created_at),
            }
            for alert in alerts
        ],
        "account": None
        if account is None
        else {
            "mode": account.mode,
            "net_liquidation": account.net_liquidation,
            "cash": account.cash,
            "currency": account.currency,
            "captured_at": _iso(account.captured_at),
        },
        "execution": {
            "global_kill_switch": _get_setting(session, "global_kill_switch", True),
            "live_trading_enabled": _get_setting(session, "live_trading_enabled", False),
            "live_notional_cap_usd": _get_setting(session, "live_notional_cap_usd", 1000),
            "wechat_configured": _get_setting(session, "wechat_configured", False),
            "wechat_test_passed": _get_setting(session, "wechat_test_passed", False),
        },
    }


@app.get("/api/strategies")
def strategies(
    session: Session = Depends(get_session),
    _auth: AuthSession = Depends(_auth_dependency),
) -> list[dict[str, Any]]:
    rows = session.scalars(select(Strategy).order_by(Strategy.display_name)).all()
    return [_strategy_summary(session, row) for row in rows]


@app.get("/api/strategies/{strategy_id}")
def strategy_detail(
    strategy_id: str,
    session: Session = Depends(get_session),
    _auth: AuthSession = Depends(_auth_dependency),
) -> dict[str, Any]:
    strategy = session.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    runs = session.scalars(
        select(ResearchRun)
        .where(ResearchRun.strategy_id == strategy_id)
        .order_by(desc(ResearchRun.as_of_date), desc(ResearchRun.created_at))
    ).all()
    progress = session.scalars(
        select(ProgressEvent)
        .where(ProgressEvent.strategy_id == strategy_id)
        .order_by(ProgressEvent.occurred_at)
    ).all()
    reports = session.scalars(
        select(ResearchReport)
        .where(ResearchReport.strategy_id == strategy_id)
        .order_by(desc(ResearchReport.as_of_date), desc(ResearchReport.created_at))
    ).all()
    notes = session.scalars(
        select(ResearchNote)
        .where(ResearchNote.strategy_id == strategy_id)
        .order_by(desc(ResearchNote.received_at))
    ).all()
    payload = _strategy_summary(session, strategy)
    payload.update(
        {
            "thesis_md": strategy.thesis_md,
            "spec_path": strategy.spec_path,
            "progress": [
                {
                    "id": row.id,
                    "stage": row.stage,
                    "title": row.title,
                    "detail": row.detail,
                    "status": row.status,
                    "occurred_at": _iso(row.occurred_at),
                }
                for row in progress
            ],
            "runs": [
                {
                    "id": run.id,
                    "run_type": run.run_type,
                    "status": run.status,
                    "as_of_date": run.as_of_date,
                    "data_start": run.data_start,
                    "data_end": run.data_end,
                    "summary": run.summary,
                    "metrics": _metric_payload(session, run.id),
                }
                for run in runs
            ],
            "reports": [
                {
                    "id": report.id,
                    "title": report.title,
                    "report_type": report.report_type,
                    "as_of_date": report.as_of_date,
                    "format": report.format,
                }
                for report in reports
            ],
            "notes": [
                {
                    "id": note.id,
                    "original_text": note.original_text,
                    "received_at": _iso(note.received_at),
                    "assessment": note.assessment,
                    "recommendation": note.recommendation,
                    "user_decision": note.user_decision,
                    "status": note.status,
                }
                for note in notes
            ],
        }
    )
    return payload


@app.get("/api/reports")
def reports(
    strategy_id: str | None = None,
    session: Session = Depends(get_session),
    _auth: AuthSession = Depends(_auth_dependency),
) -> list[dict[str, Any]]:
    statement = select(ResearchReport)
    if strategy_id:
        statement = statement.where(ResearchReport.strategy_id == strategy_id)
    rows = session.scalars(
        statement.order_by(desc(ResearchReport.as_of_date), desc(ResearchReport.created_at))
    ).all()
    return [
        {
            "id": row.id,
            "strategy_id": row.strategy_id,
            "title": row.title,
            "report_type": row.report_type,
            "as_of_date": row.as_of_date,
            "format": row.format,
            "artifact_path": row.artifact_path,
        }
        for row in rows
    ]


@app.get("/api/reports/{report_id}")
def report_detail(
    report_id: str,
    session: Session = Depends(get_session),
    _auth: AuthSession = Depends(_auth_dependency),
) -> dict[str, Any]:
    report = session.get(ResearchReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="report not found")
    return {
        "id": report.id,
        "strategy_id": report.strategy_id,
        "title": report.title,
        "report_type": report.report_type,
        "as_of_date": report.as_of_date,
        "format": report.format,
        "content": report.content,
        "artifact_path": report.artifact_path,
        "content_hash": report.content_hash,
    }


@app.get("/api/notes")
def notes(
    session: Session = Depends(get_session),
    _auth: AuthSession = Depends(_auth_dependency),
) -> list[dict[str, Any]]:
    rows = session.scalars(select(ResearchNote).order_by(desc(ResearchNote.received_at))).all()
    return [
        {
            "id": row.id,
            "original_text": row.original_text,
            "received_at": _iso(row.received_at),
            "scope_type": row.scope_type,
            "strategy_id": row.strategy_id,
            "assessment": row.assessment,
            "recommendation": row.recommendation,
            "user_decision": row.user_decision,
            "status": row.status,
        }
        for row in rows
    ]


@app.get("/api/live/overview")
def live_overview(
    session: Session = Depends(get_session),
    _auth: AuthSession = Depends(_auth_dependency),
) -> dict[str, Any]:
    account = session.scalar(
        select(AccountSnapshot).order_by(desc(AccountSnapshot.captured_at)).limit(1)
    )
    positions = []
    if account:
        positions = session.scalars(
            select(PositionSnapshot).where(PositionSnapshot.snapshot_id == account.id)
        ).all()
    orders = session.scalars(
        select(OrderRecord).order_by(desc(OrderRecord.updated_at)).limit(100)
    ).all()
    authorizations = session.scalars(
        select(ExecutionAuthorization).order_by(desc(ExecutionAuthorization.created_at))
    ).all()
    sources = session.scalars(
        select(DataSourceStatus).order_by(DataSourceStatus.provider)
    ).all()
    return {
        "account": None
        if account is None
        else {
            "mode": account.mode,
            "net_liquidation": account.net_liquidation,
            "cash": account.cash,
            "buying_power": account.buying_power,
            "currency": account.currency,
            "captured_at": _iso(account.captured_at),
        },
        "positions": [
            {
                "symbol": position.symbol,
                "quantity": position.quantity,
                "market_price": position.market_price,
                "market_value": position.market_value,
                "average_cost": position.average_cost,
                "currency": position.currency,
            }
            for position in positions
        ],
        "orders": [
            {
                "id": order.id,
                "plan_id": order.plan_id,
                "symbol": order.symbol,
                "action": order.action,
                "quantity": order.quantity,
                "limit_price": order.limit_price,
                "status": order.status,
                "filled_quantity": order.filled_quantity,
                "average_fill_price": order.average_fill_price,
                "updated_at": _iso(order.updated_at),
            }
            for order in orders
        ],
        "authorizations": [
            {
                "id": row.id,
                "strategy_id": row.strategy_id,
                "mode": row.mode,
                "notional_cap_usd": row.notional_cap_usd,
                "starts_at": _iso(row.starts_at),
                "expires_at": _iso(row.expires_at),
                "status": row.status,
            }
            for row in authorizations
        ],
        "data_sources": [
            {
                "id": row.id,
                "provider": row.provider,
                "node": row.node,
                "status": row.status,
                "data_date": row.data_date,
                "checked_at": _iso(row.checked_at),
                "details": row.details,
            }
            for row in sources
        ],
        "settings": {
            "global_kill_switch": _get_setting(session, "global_kill_switch", True),
            "live_trading_enabled": _get_setting(session, "live_trading_enabled", False),
            "live_notional_cap_usd": _get_setting(session, "live_notional_cap_usd", 1000),
            "wechat_configured": _get_setting(session, "wechat_configured", False),
            "wechat_test_passed": _get_setting(session, "wechat_test_passed", False),
        },
    }


@app.get("/api/alerts")
def alert_list(
    session: Session = Depends(get_session),
    _auth: AuthSession = Depends(_auth_dependency),
) -> list[dict[str, Any]]:
    rows = session.scalars(select(Alert).order_by(desc(Alert.created_at)).limit(200)).all()
    return [
        {
            "id": row.id,
            "severity": row.severity,
            "category": row.category,
            "title": row.title,
            "message": row.message,
            "strategy_id": row.strategy_id,
            "acknowledged": row.acknowledged,
            "delivered_wechat": row.delivered_wechat,
            "created_at": _iso(row.created_at),
        }
        for row in rows
    ]


@app.post("/api/alerts/{alert_id}/acknowledge")
def acknowledge_alert(
    alert_id: str,
    payload: AlertAckRequest,
    session: Session = Depends(get_session),
    auth_session: AuthSession = Depends(_csrf_dependency),
) -> dict[str, str]:
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    alert.acknowledged = payload.acknowledged
    alert.acknowledged_at = utcnow() if payload.acknowledged else None
    _audit(
        session,
        auth_session.user.username,
        "alert_acknowledged",
        "alert",
        alert.id,
        {"acknowledged": payload.acknowledged},
    )
    session.commit()
    return {"status": "ok"}


@app.post("/api/execution/settings")
def update_execution_settings(
    payload: ExecutionSettingsRequest,
    session: Session = Depends(get_session),
    auth_session: AuthSession = Depends(_csrf_dependency),
) -> dict[str, Any]:
    if not verify_password(auth_session.user.password_hash, payload.password):
        raise HTTPException(status_code=403, detail="password verification failed")
    if payload.live_trading_enabled is True:
        cap = float(_get_setting(session, "live_notional_cap_usd", 1000))
        expected = f"ENABLE LIVE TRADING {cap:.2f}"
        if payload.confirmation != expected:
            raise HTTPException(status_code=400, detail=f"confirmation must equal: {expected}")
        if not _get_setting(session, "wechat_test_passed", False):
            raise HTTPException(status_code=409, detail="enterprise-WeChat alert test has not passed")
    if payload.live_trading_enabled is not None:
        _set_setting(session, "live_trading_enabled", payload.live_trading_enabled)
    if payload.global_kill_switch is not None:
        _set_setting(session, "global_kill_switch", payload.global_kill_switch)
    _audit(
        session,
        auth_session.user.username,
        "execution_settings_updated",
        "system",
        None,
        {
            "live_trading_enabled": payload.live_trading_enabled,
            "global_kill_switch": payload.global_kill_switch,
        },
    )
    session.commit()
    return {
        "live_trading_enabled": _get_setting(session, "live_trading_enabled", False),
        "global_kill_switch": _get_setting(session, "global_kill_switch", True),
    }


@app.post("/api/execution/test-wechat")
def test_wechat(
    session: Session = Depends(get_session),
    auth_session: AuthSession = Depends(_csrf_dependency),
) -> dict[str, Any]:
    if not settings.wechat_webhook_url:
        raise HTTPException(status_code=409, detail="WECHAT_WEBHOOK_URL is not configured")
    alert = Alert(
        severity="error",
        category="system_test",
        title="企业微信告警测试",
        message="Deepstock 企业微信告警通道测试成功后才允许开启有限实盘。",
    )
    session.add(alert)
    session.commit()
    try:
        alert.delivered_wechat = deliver_wechat(alert)
    except Exception as error:
        alert.delivered_wechat = False
        session.commit()
        raise HTTPException(status_code=502, detail=f"WeChat delivery failed: {error}") from error
    _set_setting(session, "wechat_test_passed", alert.delivered_wechat)
    _audit(
        session,
        auth_session.user.username,
        "wechat_alert_tested",
        "system",
        None,
        {"passed": alert.delivered_wechat},
    )
    session.commit()
    return {"status": "ok", "delivered": alert.delivered_wechat}


@app.post("/api/execution/authorizations")
def create_authorization(
    payload: AuthorizationRequest,
    session: Session = Depends(get_session),
    auth_session: AuthSession = Depends(_csrf_dependency),
) -> dict[str, Any]:
    if not verify_password(auth_session.user.password_hash, payload.password):
        raise HTTPException(status_code=403, detail="password verification failed")
    if payload.mode not in {"paper", "live"}:
        raise HTTPException(status_code=400, detail="mode must be paper or live")
    strategy = session.get(Strategy, payload.strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    if payload.mode == "live" and not strategy.live_eligible:
        raise HTTPException(status_code=409, detail="strategy is not eligible for live review")
    today = date.today()
    duration = (payload.expires_on - today).days
    if duration < 1 or duration > 30:
        raise HTTPException(status_code=400, detail="authorization must expire within 30 days")
    global_cap = float(_get_setting(session, "live_notional_cap_usd", 1000))
    if payload.mode == "live" and payload.notional_cap_usd > global_cap:
        raise HTTPException(status_code=409, detail="authorization exceeds global live cap")
    expected = (
        f"{payload.mode.upper()} {payload.strategy_id} "
        f"{payload.notional_cap_usd:.2f} {payload.expires_on.isoformat()}"
    )
    if payload.confirmation != expected:
        raise HTTPException(status_code=400, detail=f"confirmation must equal: {expected}")
    version = session.scalar(
        select(StrategyVersion).where(
            StrategyVersion.strategy_id == strategy.id,
            StrategyVersion.version == strategy.current_version,
            StrategyVersion.active.is_(True),
        )
    )
    if version is None:
        raise HTTPException(status_code=409, detail="active strategy version not found")
    authorization = ExecutionAuthorization(
        strategy_id=strategy.id,
        mode=payload.mode,
        config_hash=version.config_hash,
        notional_cap_usd=payload.notional_cap_usd,
        starts_at=utcnow(),
        expires_at=datetime.combine(
            payload.expires_on, datetime.max.time(), timezone.utc
        ),
        status="active",
        approved_by=auth_session.user.id,
    )
    session.add(authorization)
    session.flush()
    _audit(
        session,
        auth_session.user.username,
        "execution_authorized",
        "authorization",
        authorization.id,
        {
            "strategy_id": strategy.id,
            "mode": payload.mode,
            "notional_cap_usd": payload.notional_cap_usd,
            "expires_on": payload.expires_on.isoformat(),
            "config_hash": version.config_hash,
        },
    )
    session.commit()
    return {"id": authorization.id, "status": authorization.status}


@app.post("/api/execution/authorizations/{authorization_id}/revoke")
def revoke_authorization(
    authorization_id: str,
    session: Session = Depends(get_session),
    auth_session: AuthSession = Depends(_csrf_dependency),
) -> dict[str, str]:
    authorization = session.get(ExecutionAuthorization, authorization_id)
    if authorization is None:
        raise HTTPException(status_code=404, detail="authorization not found")
    authorization.status = "revoked"
    authorization.revoked_at = utcnow()
    _audit(
        session,
        auth_session.user.username,
        "execution_authorization_revoked",
        "authorization",
        authorization.id,
    )
    session.commit()
    return {"status": "revoked"}


def _validate_plan(session: Session, payload: PlanRequest) -> tuple[Strategy, float]:
    if payload.mode not in {"paper", "live"}:
        raise HTTPException(status_code=400, detail="mode must be paper or live")
    strategy = session.get(Strategy, payload.strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    total_notional = 0.0
    for order in payload.orders:
        if order.action not in {"BUY", "SELL"}:
            raise HTTPException(status_code=400, detail="orders must be BUY or SELL")
        if order.order_type != "LMT":
            raise HTTPException(status_code=400, detail="only LMT orders are accepted")
        total_notional += order.quantity * order.limit_price
    if payload.mode == "live":
        if not strategy.live_eligible:
            raise HTTPException(status_code=409, detail="strategy is not eligible for live execution")
        if _get_setting(session, "global_kill_switch", True):
            raise HTTPException(status_code=409, detail="global kill switch is enabled")
        if not _get_setting(session, "live_trading_enabled", False):
            raise HTTPException(status_code=409, detail="live trading is disabled")
        if not _get_setting(session, "wechat_test_passed", False):
            raise HTTPException(status_code=409, detail="enterprise-WeChat alert test has not passed")
        cap = float(_get_setting(session, "live_notional_cap_usd", 1000))
        latest_live = session.scalar(
            select(AccountSnapshot)
            .where(AccountSnapshot.mode == "live")
            .order_by(desc(AccountSnapshot.captured_at))
            .limit(1)
        )
        current_exposure = 0.0
        if latest_live is not None:
            current_exposure = sum(
                max(float(value or 0), 0.0)
                for value in session.scalars(
                    select(PositionSnapshot.market_value).where(
                        PositionSnapshot.snapshot_id == latest_live.id
                    )
                ).all()
            )
        outstanding = float(
            session.scalar(
                select(func.sum(ExecutionPlan.total_notional_usd)).where(
                    ExecutionPlan.mode == "live",
                    ExecutionPlan.status.in_(("ready", "claimed")),
                )
            )
            or 0.0
        )
        if current_exposure + outstanding + total_notional > cap:
            raise HTTPException(status_code=409, detail="plan exceeds global live cap")
    authorization = session.get(ExecutionAuthorization, payload.authorization_id)
    if authorization is None:
        raise HTTPException(status_code=409, detail="active authorization is required")
    expires = authorization.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if (
        authorization.status != "active"
        or authorization.strategy_id != strategy.id
        or authorization.mode != payload.mode
        or authorization.config_hash != payload.config_hash
        or expires <= utcnow()
        or total_notional > authorization.notional_cap_usd
    ):
        raise HTTPException(status_code=409, detail="authorization does not cover this plan")
    return strategy, total_notional


def _stored_plan_is_current(session: Session, plan: ExecutionPlan) -> bool:
    authorization = session.get(ExecutionAuthorization, plan.authorization_id)
    strategy = session.get(Strategy, plan.strategy_id)
    if authorization is None or strategy is None:
        return False
    expires = authorization.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if (
        authorization.status != "active"
        or expires <= utcnow()
        or authorization.strategy_id != plan.strategy_id
        or authorization.mode != plan.mode
        or authorization.config_hash != plan.config_hash
        or plan.total_notional_usd > authorization.notional_cap_usd
    ):
        return False
    version = session.scalar(
        select(StrategyVersion).where(
            StrategyVersion.strategy_id == plan.strategy_id,
            StrategyVersion.version == strategy.current_version,
            StrategyVersion.active.is_(True),
        )
    )
    if version is None or version.config_hash != plan.config_hash:
        return False
    if plan.mode == "live":
        return bool(
            strategy.live_eligible
            and not _get_setting(session, "global_kill_switch", True)
            and _get_setting(session, "live_trading_enabled", False)
            and _get_setting(session, "wechat_test_passed", False)
            and plan.total_notional_usd
            <= float(_get_setting(session, "live_notional_cap_usd", 1000))
        )
    return True


def _attach_order_ids(plan_id: str, orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for index, order in enumerate(orders):
        canonical = json.dumps(order, sort_keys=True, separators=(",", ":"))
        order_id = hashlib.sha256(
            f"{plan_id}:{index}:{canonical}".encode("utf-8")
        ).hexdigest()[:32]
        result.append({**order, "order_id": order_id})
    return result


@app.post("/api/execution/plans")
def create_plan(
    payload: PlanRequest,
    session: Session = Depends(get_session),
    auth_session: AuthSession = Depends(_csrf_dependency),
) -> dict[str, Any]:
    canonical = payload.model_dump(mode="json")
    plan_id = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    plan = session.get(ExecutionPlan, plan_id)
    if plan is not None:
        return {
            "id": plan.id,
            "status": plan.status,
            "total_notional_usd": plan.total_notional_usd,
        }
    strategy, total_notional = _validate_plan(session, payload)
    stored_orders = _attach_order_ids(
        plan_id, [order.model_dump() for order in payload.orders]
    )
    plan = ExecutionPlan(
        id=plan_id,
        strategy_id=strategy.id,
        authorization_id=payload.authorization_id,
        mode=payload.mode,
        config_hash=payload.config_hash,
        data_date=payload.data_date.isoformat(),
        status="ready",
        total_notional_usd=total_notional,
        orders=stored_orders,
    )
    session.add(plan)
    _audit(
        session,
        auth_session.user.username,
        "execution_plan_created",
        "plan",
        plan.id,
        {"mode": plan.mode, "total_notional_usd": total_notional},
    )
    session.commit()
    return {"id": plan.id, "status": plan.status, "total_notional_usd": total_notional}


@app.get("/api/agent/plans")
def agent_plans(
    mode: str,
    session: Session = Depends(get_session),
    _node: str = Depends(_node_dependency),
) -> list[dict[str, Any]]:
    if mode not in {"paper", "live"}:
        raise HTTPException(status_code=400, detail="invalid mode")
    rows = session.scalars(
        select(ExecutionPlan)
        .where(
            ExecutionPlan.mode == mode,
            ExecutionPlan.status.in_(("ready", "claimed")),
        )
        .order_by(ExecutionPlan.created_at)
    ).all()
    rows = [row for row in rows if _stored_plan_is_current(session, row)]
    return [
        {
            "id": row.id,
            "strategy_id": row.strategy_id,
            "authorization_id": row.authorization_id,
            "mode": row.mode,
            "config_hash": row.config_hash,
            "data_date": row.data_date,
            "status": row.status,
            "total_notional_usd": row.total_notional_usd,
            "orders": row.orders,
        }
        for row in rows
    ]


@app.get("/api/agent/control")
def agent_control(
    mode: str,
    session: Session = Depends(get_session),
    _node: str = Depends(_node_dependency),
) -> dict[str, Any]:
    if mode not in {"paper", "live"}:
        raise HTTPException(status_code=400, detail="invalid mode")
    account = session.scalar(
        select(AccountSnapshot)
        .where(AccountSnapshot.mode == mode)
        .order_by(desc(AccountSnapshot.captured_at))
        .limit(1)
    )
    return {
        "mode": mode,
        "global_kill_switch": _get_setting(session, "global_kill_switch", True),
        "live_trading_enabled": _get_setting(session, "live_trading_enabled", False),
        "live_notional_cap_usd": _get_setting(session, "live_notional_cap_usd", 1000),
        "wechat_test_passed": _get_setting(session, "wechat_test_passed", False),
        "latest_account_hash": account.account_hash if account else None,
        "latest_account_at": _iso(account.captured_at) if account else None,
    }


@app.post("/api/agent/plans/{plan_id}/claim")
def claim_plan(
    plan_id: str,
    session: Session = Depends(get_session),
    node: str = Depends(_node_dependency),
) -> dict[str, str]:
    plan = session.get(ExecutionPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    if plan.status not in {"ready", "claimed"}:
        raise HTTPException(status_code=409, detail="plan is no longer claimable")
    if not _stored_plan_is_current(session, plan):
        raise HTTPException(status_code=409, detail="plan authorization or configuration is no longer valid")
    plan.status = "claimed"
    plan.claimed_at = plan.claimed_at or utcnow()
    _audit(session, node, "execution_plan_claimed", "plan", plan.id)
    session.commit()
    return {"status": "claimed"}


@app.post("/api/agent/snapshot")
def agent_snapshot(
    payload: AgentSnapshotRequest,
    session: Session = Depends(get_session),
    node: str = Depends(_node_dependency),
) -> dict[str, Any]:
    if payload.mode not in {"paper", "live"}:
        raise HTTPException(status_code=400, detail="invalid mode")
    snapshot = AccountSnapshot(
        mode=payload.mode,
        account_hash=payload.account_hash,
        net_liquidation=payload.net_liquidation,
        cash=payload.cash,
        buying_power=payload.buying_power,
        currency=payload.currency,
        captured_at=payload.captured_at,
        source_node=payload.source_node,
    )
    session.add(snapshot)
    session.flush()
    for row in payload.positions:
        session.add(
            PositionSnapshot(
                snapshot_id=snapshot.id,
                symbol=row.symbol,
                quantity=row.quantity,
                market_price=row.market_price,
                market_value=row.market_value,
                average_cost=row.average_cost,
                currency=row.currency,
            )
        )
    _audit(
        session,
        node,
        "account_snapshot_recorded",
        "account_snapshot",
        snapshot.id,
        {"mode": payload.mode, "position_count": len(payload.positions)},
    )
    session.commit()
    return {"id": snapshot.id, "status": "recorded"}


@app.post("/api/agent/orders")
def agent_order(
    payload: AgentOrderRequest,
    session: Session = Depends(get_session),
    node: str = Depends(_node_dependency),
) -> dict[str, str]:
    plan = session.get(ExecutionPlan, payload.plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    expected_order = next(
        (row for row in plan.orders if row.get("order_id") == payload.order_id), None
    )
    if expected_order is None:
        raise HTTPException(status_code=409, detail="order does not belong to this plan")
    comparable = {
        "symbol": payload.symbol,
        "action": payload.action,
        "quantity": payload.quantity,
        "order_type": payload.order_type,
        "limit_price": payload.limit_price,
    }
    if any(expected_order.get(key) != value for key, value in comparable.items()):
        raise HTTPException(status_code=409, detail="order report does not match the plan")
    order = session.get(OrderRecord, payload.order_id)
    values = payload.model_dump()
    values["id"] = values.pop("order_id")
    if order is None:
        order = OrderRecord(**values)
        session.add(order)
    else:
        for key, value in values.items():
            if key != "id":
                setattr(order, key, value)
    terminal = {"FILLED", "CANCELLED", "REJECTED", "ERROR"}
    if payload.status.upper() in terminal:
        session.flush()
        related = session.scalars(
            select(OrderRecord).where(OrderRecord.plan_id == payload.plan_id)
        ).all()
        if related and all(row.status.upper() in terminal for row in related):
            plan.status = "completed"
            plan.completed_at = utcnow()
    if payload.status.upper() in {"REJECTED", "ERROR"}:
        create_alert(
            session,
            severity="error",
            category="broker_order",
            title=f"订单失败 · {payload.symbol}",
            message=payload.last_error or payload.status,
            strategy_id=plan.strategy_id,
            commit=False,
        )
    _audit(session, node, "order_status_recorded", "order", payload.order_id)
    session.commit()
    return {"status": "recorded"}


@app.get("/api/events")
async def events(_auth: AuthSession = Depends(_auth_dependency)) -> StreamingResponse:
    async def stream():
        last_signature = ""
        while True:
            with SessionLocal() as session:
                signature_payload = {
                    "strategy_updated": session.scalar(select(func.max(Strategy.updated_at))),
                    "latest_alert": session.scalar(select(func.max(Alert.created_at))),
                    "latest_order": session.scalar(select(func.max(OrderRecord.updated_at))),
                    "latest_account": session.scalar(select(func.max(AccountSnapshot.captured_at))),
                }
                signature = hashlib.sha256(
                    json.dumps(signature_payload, default=str, sort_keys=True).encode("utf-8")
                ).hexdigest()
            if signature != last_signature:
                last_signature = signature
                yield f"event: update\ndata: {json.dumps({'signature': signature})}\n\n"
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


if settings.frontend_dist.exists():
    assets = settings.frontend_dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    index = settings.frontend_dist / "index.html"
    requested = settings.frontend_dist / path
    if path and requested.is_file() and settings.frontend_dist in requested.resolve().parents:
        return FileResponse(requested)
    if index.exists():
        return FileResponse(index)
    return JSONResponse(
        status_code=503,
        content={"detail": "frontend build is unavailable; run npm run build"},
    )
