"""HWAXAgent (Windows tray launcher) service: enrollment + JWT issuance + refresh rotation.

Distinct from :mod:`app.services.agent_registry`, which manages the legacy
polling Windows Workers. Both share the ``WindowsAgent`` table; the
``device_kind`` column (alembic 0006) discriminates them.

Token shape:
    - access_token : type='access', aud='hwax-agent', sub=<WindowsAgent.id>,
                     ttl = settings.access_token_ttl_seconds (default 3600 s)
    - refresh_token: type='refresh', aud='hwax-agent', sub=<WindowsAgent.id>,
                     jti=<random>, ttl = settings.refresh_token_ttl_seconds
                     (default 7 d — the agent re-pairs after expiry).
                     Persisted in ``agent_refresh_tokens`` (alembic 0007),
                     NOT the existing ``refresh_tokens`` table (whose FK
                     points at ``users.id``).

Enrollment flow:
    1. Operator calls ``POST /api/v1/admin/agents`` (existing) with
       device_kind='launcher'. We get back ``(agent_row, plaintext_token)``.
       Operator hands the plaintext to the user installing HWAXAgent.
    2. User pastes the token into HWAXAgent. The launcher posts it to
       ``POST /api/v1/launcher-agents/enroll`` → :func:`redeem_enrollment_token`.
    3. We verify (SHA-256 hash match), rotate the hash so the plaintext can
       never be redeemed twice, then mint a JWT pair + persist the refresh
       row.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import UnauthorizedError
from app.db.models.agent_refresh_token import AgentRefreshToken
from app.db.models.windows_agent import WindowsAgent

# Audience claim that distinguishes HWAXAgent (launcher) JWTs from regular
# user JWTs. Any endpoint exclusively for the launcher MUST verify aud==this.
AGENT_AUDIENCE = "hwax-agent"


def _hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _encode(payload: dict[str, Any]) -> str:
    s = get_settings()
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def _decode_agent(token: str) -> dict[str, Any]:
    """Decode a JWT and enforce aud='hwax-agent'."""
    s = get_settings()
    try:
        payload = jwt.decode(
            token,
            s.jwt_secret,
            algorithms=[s.jwt_algorithm],
            audience=AGENT_AUDIENCE,
        )
    except JWTError as exc:
        raise UnauthorizedError("Invalid or expired agent token") from exc
    return payload


def _mint_access_token(agent_id: str) -> tuple[str, int]:
    s = get_settings()
    now = _now()
    ttl = s.access_token_ttl_seconds
    payload = {
        "sub": agent_id,
        "type": "access",
        "aud": AGENT_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }
    return _encode(payload), ttl


def _mint_refresh_token(agent_id: str) -> tuple[str, str, datetime]:
    """Return (token, jti, expires_at)."""
    s = get_settings()
    now = _now()
    jti = secrets.token_urlsafe(16)
    expires_at = now + timedelta(seconds=s.refresh_token_ttl_seconds)
    payload = {
        "sub": agent_id,
        "type": "refresh",
        "aud": AGENT_AUDIENCE,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return _encode(payload), jti, expires_at


# ── public api ─────────────────────────────────────────────────────────────────


def issue_enrollment_token(
    db: Session,
    *,
    name: str,
    pool: str,
    hostname: str | None = None,
) -> tuple[WindowsAgent, str]:
    """Create a launcher WindowsAgent row and return (row, plaintext_token).

    Thin wrapper around ``agent_registry.register_agent`` that pins
    ``device_kind='launcher'``. Kept here so callers needing the launcher
    flow don't have to know about ``device_kind`` constants.
    """
    # Import lazily to avoid a startup-time circular with agent_registry.
    from app.services import agent_registry  # noqa: PLC0415

    return agent_registry.register_agent(
        db,
        name=name,
        pool=pool,
        hostname=hostname,
        device_kind="launcher",
    )


def redeem_enrollment_token(
    db: Session,
    *,
    enrollment_token: str,
    hostname: str | None = None,
    agent_version: str | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """Verify a one-time enrollment token, mint a JWT pair, return EnrollmentResult.

    Rotates the WindowsAgent's auth_token_hash to a fresh random value so the
    plaintext enrollment_token can never be redeemed twice.

    Raises :class:`UnauthorizedError` when the token is unknown, the row is
    disabled, or the row was not registered as a launcher.
    """
    digest = _hash_token(enrollment_token)
    agent = db.execute(
        select(WindowsAgent).where(WindowsAgent.auth_token_hash == digest)
    ).scalar_one_or_none()
    if agent is None or agent.disabled:
        raise UnauthorizedError("Unknown or revoked enrollment token")
    if agent.device_kind != "launcher":
        # Pre-existing service-agent rows shouldn't accidentally be redeemed
        # as launchers — they use a different transport.
        raise UnauthorizedError("Agent is not a launcher device")

    # Rotate the hash so a leaked enrollment_token can't be redeemed again.
    agent.auth_token_hash = _hash_token(secrets.token_urlsafe(32))
    if hostname:
        agent.hostname = hostname
    if agent_version:
        agent.agent_version = agent_version
    agent.last_seen = _now()
    agent.status = "online"

    access, ttl = _mint_access_token(str(agent.id))
    refresh, jti, exp = _mint_refresh_token(str(agent.id))
    db.add(
        AgentRefreshToken(
            agent_id=agent.id,
            jti=jti,
            expires_at=exp,
            user_agent=user_agent,
            ip_address=ip_address,
        )
    )
    db.commit()
    db.refresh(agent)
    return {
        "agent_id": str(agent.id),
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": ttl,
    }


def rotate_refresh(
    db: Session,
    *,
    refresh_token: str,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """Verify a refresh token, rotate it, mint a new access+refresh pair.

    The old refresh row is marked ``revoked_at`` + ``replaced_by_jti``. The
    new row is inserted. Reuse of the old refresh raises 401.
    """
    payload = _decode_agent(refresh_token)
    if payload.get("type") != "refresh":
        raise UnauthorizedError("Token type mismatch")
    old_jti = payload.get("jti")
    if not old_jti:
        raise UnauthorizedError("Missing jti")
    old = db.execute(
        select(AgentRefreshToken).where(AgentRefreshToken.jti == old_jti)
    ).scalar_one_or_none()
    if old is None or not old.is_active:
        raise UnauthorizedError("Refresh token unknown, revoked, or expired")

    agent = db.get(WindowsAgent, old.agent_id)
    if agent is None or agent.disabled:
        raise UnauthorizedError("Agent unknown or disabled")

    access, ttl = _mint_access_token(str(agent.id))
    new_refresh, new_jti, new_exp = _mint_refresh_token(str(agent.id))

    # Revoke old, link to new.
    now = _now()
    old.revoked_at = now
    old.replaced_by_jti = new_jti
    db.add(
        AgentRefreshToken(
            agent_id=agent.id,
            jti=new_jti,
            expires_at=new_exp,
            user_agent=user_agent,
            ip_address=ip_address,
        )
    )
    agent.last_seen = now
    db.commit()
    return {
        "access_token": access,
        "refresh_token": new_refresh,
        "expires_in": ttl,
    }


def verify_agent_jwt(db: Session, access_token: str) -> WindowsAgent:
    """Decode an access_token (aud='hwax-agent'), load and return the agent.

    Raises :class:`UnauthorizedError` on bad token / unknown agent / disabled.
    """
    payload = _decode_agent(access_token)
    if payload.get("type") != "access":
        raise UnauthorizedError("Token type mismatch")
    sub = payload.get("sub")
    if not sub:
        raise UnauthorizedError("Missing subject")
    try:
        agent = db.get(WindowsAgent, sub)
    except Exception as exc:
        # SQLAlchemy raises if sub isn't a valid UUID
        raise UnauthorizedError("Invalid subject") from exc
    if agent is None or agent.disabled:
        raise UnauthorizedError("Agent unknown or disabled")
    return agent


def revoke_refresh_chain(db: Session, agent_id: Any) -> None:
    """Revoke every still-active refresh token for an agent and commit.

    Used when an operator rotates the enrollment token: re-issuing means the old
    device's JWT chain should stop working immediately.
    """
    now = _now()
    rows = db.execute(
        select(AgentRefreshToken).where(AgentRefreshToken.agent_id == agent_id)
    ).scalars()
    for row in rows:
        if row.revoked_at is None:
            row.revoked_at = now
    db.commit()
