from __future__ import annotations

from email.message import EmailMessage

from deepstock.web.alerts import deliver_email
from deepstock.web.config import settings
from deepstock.web.models import Alert, utcnow


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.kwargs = kwargs
        self.started_tls = False
        self.login_args: tuple[str, str] | None = None
        self.message: EmailMessage | None = None
        self.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *_args) -> None:  # type: ignore[no-untyped-def]
        return None

    def starttls(self, *, context) -> None:  # type: ignore[no-untyped-def]
        assert context is not None
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def send_message(self, message: EmailMessage) -> None:
        self.message = message


def test_email_delivery_uses_starttls_and_configured_recipients(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    FakeSMTP.instances.clear()
    values = {
        "smtp_host": "smtp.example.test",
        "smtp_port": 587,
        "smtp_username": "mailer@example.test",
        "smtp_password": "app-password",
        "smtp_from": "deepstock@example.test",
        "smtp_security": "starttls",
        "alert_email_to": ("owner@example.test", "backup@example.test"),
    }
    originals = {name: getattr(settings, name) for name in values}
    for name, value in values.items():
        object.__setattr__(settings, name, value)
    monkeypatch.setattr("deepstock.web.alerts.smtplib.SMTP", FakeSMTP)
    try:
        alert = Alert(
            severity="critical",
            category="execution",
            title="Order rejected",
            message="The broker rejected the order.",
            created_at=utcnow(),
        )
        assert deliver_email(alert) is True
    finally:
        for name, value in originals.items():
            object.__setattr__(settings, name, value)

    client = FakeSMTP.instances[0]
    assert client.kwargs == {
        "host": "smtp.example.test",
        "port": 587,
        "timeout": 10,
    }
    assert client.started_tls is True
    assert client.login_args == ("mailer@example.test", "app-password")
    assert client.message is not None
    assert client.message["To"] == "owner@example.test, backup@example.test"
    assert client.message["Subject"] == "[Deepstock CRITICAL] Order rejected"


def test_email_delivery_ignores_non_severe_alerts() -> None:
    alert = Alert(
        severity="info",
        category="research",
        title="Research complete",
        message="No notification is required.",
        created_at=utcnow(),
    )
    assert deliver_email(alert) is False
