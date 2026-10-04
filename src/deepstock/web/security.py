from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from deepstock.web.config import settings
from deepstock.web.models import AuthSession, LoginAttempt, User, utcnow


password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    if len(password) < 5:
        raise ValueError("password must contain at least five characters")
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def ensure_bootstrap_user(session: Session) -> User:
    user = session.scalar(
        select(User).where(User.username == settings.bootstrap_username)
    )
    if user is None:
        user = User(
            username=settings.bootstrap_username,
            password_hash=hash_password(settings.bootstrap_password),
            role="admin",
            active=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
    return user


def login_rate_limited(session: Session, username: str, remote_addr: str) -> bool:
    cutoff = utcnow() - timedelta(minutes=15)
    failures = session.scalar(
        select(func.count(LoginAttempt.id)).where(
            LoginAttempt.username == username,
            LoginAttempt.remote_addr == remote_addr,
            LoginAttempt.succeeded.is_(False),
            LoginAttempt.attempted_at >= cutoff,
        )
    )
    return int(failures or 0) >= 5


def record_login_attempt(
    session: Session, username: str, remote_addr: str, succeeded: bool
) -> None:
    session.add(
        LoginAttempt(
            username=username,
            remote_addr=remote_addr,
            succeeded=succeeded,
        )
    )
    session.commit()


def create_auth_session(
    session: Session,
    user: User,
    *,
    user_agent: str | None,
    remote_addr: str | None,
) -> tuple[str, AuthSession]:
    raw_token = secrets.token_urlsafe(48)
    auth_session = AuthSession(
        user_id=user.id,
        token_hash=token_hash(raw_token),
        csrf_token=secrets.token_urlsafe(32),
        expires_at=utcnow() + timedelta(hours=settings.session_hours),
        user_agent=(user_agent or "")[:500] or None,
        remote_addr=(remote_addr or "")[:80] or None,
    )
    user.last_login_at = utcnow()
    session.add(auth_session)
    session.commit()
    session.refresh(auth_session)
    return raw_token, auth_session


def resolve_auth_session(session: Session, raw_token: str | None) -> AuthSession | None:
    if not raw_token:
        return None
    auth_session = session.scalar(
        select(AuthSession).where(AuthSession.token_hash == token_hash(raw_token))
    )
    if auth_session is None or _aware(auth_session.expires_at) <= utcnow():
        if auth_session is not None:
            session.delete(auth_session)
            session.commit()
        return None
    if not auth_session.user.active:
        return None
    auth_session.last_seen_at = utcnow()
    session.commit()
    return auth_session


def revoke_auth_session(session: Session, raw_token: str | None) -> None:
    if not raw_token:
        return
    session.execute(
        delete(AuthSession).where(AuthSession.token_hash == token_hash(raw_token))
    )
    session.commit()


def change_password(session: Session, user: User, current: str, new: str) -> None:
    if not verify_password(user.password_hash, current):
        raise ValueError("current password is incorrect")
    user.password_hash = hash_password(new)
    session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    session.commit()
