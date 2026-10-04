from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value}")


@dataclass(frozen=True)
class Settings:
    project_root: Path
    database_url: str
    session_cookie: str
    session_hours: int
    bootstrap_username: str
    bootstrap_password: str
    node_token: str
    public_base_url: str
    live_trading_enabled: bool
    live_notional_cap_usd: float
    global_kill_switch: bool
    wechat_webhook_url: str
    frontend_dist: Path


def load_settings() -> Settings:
    _load_dotenv(PROJECT_ROOT / ".env")
    database_url = os.getenv(
        "DEEPSTOCK_DATABASE_URL", "sqlite:///artifacts/app/deepstock.sqlite3"
    )
    if database_url.startswith("sqlite:///./"):
        database_url = f"sqlite:///{PROJECT_ROOT / database_url.removeprefix('sqlite:///./')}"
    elif database_url == "sqlite:///artifacts/app/deepstock.sqlite3":
        database_url = f"sqlite:///{PROJECT_ROOT / 'artifacts/app/deepstock.sqlite3'}"
    return Settings(
        project_root=PROJECT_ROOT,
        database_url=database_url,
        session_cookie=os.getenv("DEEPSTOCK_SESSION_COOKIE", "deepstock_session"),
        session_hours=int(os.getenv("DEEPSTOCK_SESSION_HOURS", "12")),
        bootstrap_username=os.getenv("DEEPSTOCK_BOOTSTRAP_USERNAME", "admin"),
        bootstrap_password=os.getenv("DEEPSTOCK_BOOTSTRAP_PASSWORD", "admin"),
        node_token=os.getenv("DEEPSTOCK_NODE_TOKEN", ""),
        public_base_url=os.getenv(
            "DEEPSTOCK_PUBLIC_BASE_URL", "https://dev-cn-01.yios.cn/deepstock"
        ).rstrip("/"),
        live_trading_enabled=_as_bool(
            os.getenv("DEEPSTOCK_LIVE_TRADING_ENABLED"), False
        ),
        live_notional_cap_usd=float(
            os.getenv("DEEPSTOCK_LIVE_NOTIONAL_CAP_USD", "1000")
        ),
        global_kill_switch=_as_bool(
            os.getenv("DEEPSTOCK_GLOBAL_KILL_SWITCH"), True
        ),
        wechat_webhook_url=os.getenv("WECHAT_WEBHOOK_URL", ""),
        frontend_dist=PROJECT_ROOT / "frontend" / "dist",
    )


settings = load_settings()
