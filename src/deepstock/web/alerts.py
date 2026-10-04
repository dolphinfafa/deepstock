from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from sqlalchemy.orm import Session

from deepstock.web.config import settings
from deepstock.web.models import Alert


SEVERE_LEVELS = {"critical", "error"}


def deliver_email(alert: Alert) -> bool:
    if alert.severity not in SEVERE_LEVELS or not settings.email_configured:
        return False
    title = " ".join(alert.title.splitlines())
    message = EmailMessage()
    message["Subject"] = f"[Deepstock {alert.severity.upper()}] {title}"
    message["From"] = settings.smtp_from
    message["To"] = ", ".join(settings.alert_email_to)
    message.set_content(
        f"{alert.title}\n\n{alert.message}\n\n"
        f"Category: {alert.category}\n"
        f"Created at (UTC): {alert.created_at.isoformat()}\n"
        f"Deepstock: {settings.public_base_url}/alerts\n"
    )

    smtp_class = smtplib.SMTP_SSL if settings.smtp_security == "ssl" else smtplib.SMTP
    kwargs = {"host": settings.smtp_host, "port": settings.smtp_port, "timeout": 10}
    if settings.smtp_security == "ssl":
        kwargs["context"] = ssl.create_default_context()
    with smtp_class(**kwargs) as client:
        if settings.smtp_security == "starttls":
            client.starttls(context=ssl.create_default_context())
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(message)
    return True


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
            alert.delivered_email = deliver_email(alert)
        except Exception:
            alert.delivered_email = False
    if commit:
        session.commit()
        session.refresh(alert)
    return alert
