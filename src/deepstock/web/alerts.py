from __future__ import annotations

import httpx
from sqlalchemy.orm import Session

from deepstock.web.config import settings
from deepstock.web.models import Alert


SEVERE_LEVELS = {"critical", "error"}


def deliver_wechat(alert: Alert) -> bool:
    if alert.severity not in SEVERE_LEVELS or not settings.wechat_webhook_url:
        return False
    message = f"【Deepstock {alert.severity.upper()}】{alert.title}\n{alert.message}"
    response = httpx.post(
        settings.wechat_webhook_url,
        json={"msgtype": "text", "text": {"content": message}},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("errcode") == 0


def create_alert(
    session: Session,
    *,
    severity: str,
    category: str,
    title: str,
    message: str,
    strategy_id: str | None = None,
    notify: bool = True,
    commit: bool = True,
) -> Alert:
    alert = Alert(
        severity=severity,
        category=category,
        title=title,
        message=message,
        strategy_id=strategy_id,
    )
    session.add(alert)
    session.flush()
    if notify:
        try:
            alert.delivered_wechat = deliver_wechat(alert)
        except Exception:
            alert.delivered_wechat = False
    if commit:
        session.commit()
        session.refresh(alert)
    return alert
