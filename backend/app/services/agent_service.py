"""HWAXAgent launcher authentication — enrollment, refresh rotation, JWT verify.

The launcher (``device_kind='launcher'``) authenticates with a JWT access/refresh
pair rather than the long-lived bearer token used by polling ``service`` agents:

  1. An admin registers a launcher agent (``agent_registry.register_agent`` with
     ``device_kind='launcher'``) and hands the operator the one-time *enrollment
     token* (the agent's ``auth_token``).
  2. The launcher calls ``redeem_enrollment_token`` once: we verify the token,
     mint a JWT pair (access carries ``aud='hwax-agent'``), persist a refresh
     revocation record, and **burn** the enrollment token so it can never be
     replayed.
  3. Thereafter the launcher refreshes via ``rotate_refresh`` (single-use,
     rotating refresh tokens with reuse detection) and authenticates API/WS
     calls via ``verify_agent_jwt``.

Audience isolation (``aud='hwax-agent'`` on access tokens, enforced in
``security.decode_token``) keeps a launcher token from ever authenticating a
plain user route, and vice-versa.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import security
from app.core.errors import ForbiddenError, UnauthorizedError
from app.db.models.agent_refresh_token import AgentRefreshToken
from app.db.models.windows_agent import WindowsAgent
from app.services import agent_registry

# Access tokens minted for launchers carry this audience; user tokens carry none.
AGENT_AUDIENCE = "hwax-agent"


@dataclass(frozen=True)
class EnrollmentResult:
    agent: WindowsAgent
    access_token: str
    refresh_token: str
    access_expires_in: int
    refresh_expires_at: datetime


@dataclass(frozen=True)
class RefreshResult:
    agent: WindowsAgent
    access_token: str
    refresh_token: str
    access_expires_in: int
    refresh_expires_at: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_active_agent(db: Session, subject: str) -> WindowsAgent:
    """Resolve a token ``sub`` (stringified UUID) to a live, enabled agent."""
    try:
        agent_id = uuid.UUID(subject)
    except (ValueError, TypeError) as exc:
        raise UnauthorizedError("Malformed token subject") from exc
    agent = db.get(WindowsAgent, agent_id)
    if agent is None or agent.disabled:
        raise UnauthorizedError("Agent not found or disabled")
    return agent


def _issue_pair(
    db: Session,
    agent: WindowsAgent,
    *,
    user_agent: str | None,
    ip_address: str | None,
) -> tuple[str, str, str, datetime]:
    """Mint an access+refresh pair and persist the refresh revocation record.

    Returns ``(access_token, refresh_token, jti, refresh_expires_at)``. Does NOT
    commit — the caller owns the transaction boundary.
    """
    settings = get_settings()
    access_token = security.create_access_token(
        str(agent.id), extra={"aud": AGENT_AUDIENCE}
    )
    refresh_token, jti, refresh_expires_at = security.create_refresh_token(
        str(agent.id), ttl_seconds=settings.agent_refresh_token_ttl_seconds
    )
    db.add(
        AgentRefreshToken(
            agent_id=agent.id,
            jti=jti,
            expires_at=refresh_expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )
    )
    return access_token, refresh_token, jti, refresh_expires_at


def redeem_enrollment_token(
    db: Session,
    *,
    enrollment_token: str,
    hostname: str | None = None,
    agent_version: str | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> EnrollmentResult:
    """Exchange a one-time launcher enrollment token for a JWT pair.

    The enrollment token is burned on success (single-use): the original string
    can never be redeemed again. Raises ``UnauthorizedError`` if the token does
    not match a live agent, ``ForbiddenError`` if the agent is not a launcher.
    """
    agent = agent_registry.verify_token(db, token=enrollment_token)
    if agent is None:
        raise UnauthorizedError("Invalid or already-used enrollment token")
    if agent.device_kind != "launcher":
        raise ForbiddenError("Enrollment is only available to launcher agents")

    if hostname is not None:
        agent.hostname = hostname
    if agent_version is not None:
        agent.agent_version = agent_version
    agent.last_seen = _now()

    access_token, refresh_token, _jti, refresh_expires_at = _issue_pair(
        db, agent, user_agent=user_agent, ip_address=ip_address
    )

    # Burn the single-use enrollment token: overwrite the bearer hash with a
    # fresh random one whose plaintext is discarded. From now on the launcher
    # authenticates exclusively with its JWT pair.
    agent.auth_token_hash = agent_registry.fresh_token_hash()

    db.commit()
    db.refresh(agent)
    return EnrollmentResult(
        agent=agent,
        access_token=access_token,
        refresh_token=refresh_token,
        access_expires_in=get_settings().access_token_ttl_seconds,
        refresh_expires_at=refresh_expires_at,
    )


def rotate_refresh(
    db: Session,
    *,
    refresh_token: str,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> RefreshResult:
    """Rotate a launcher refresh token, returning a fresh access+refresh pair.

    Refresh tokens are single-use: the presented token is revoked and a new one
    issued. Presenting an *already-rotated* token (revoked, replaced) is treated
    as reuse — every active refresh token for that agent is revoked and the
    request rejected.
    """
    # Refresh tokens carry no audience, so the default (audience=None) branch of
    # decode_token accepts them and would reject an access token (aud-scoped).
    payload = security.decode_token(refresh_token, expected_type="refresh")
    jti = payload.get("jti")
    if not jti:
        raise UnauthorizedError("Refresh token missing jti")

    record = db.execute(
        select(AgentRefreshToken).where(AgentRefreshToken.jti == jti)
    ).scalar_one_or_none()
    if record is None:
        raise UnauthorizedError("Unknown refresh token")

    if record.revoked_at is not None:
        # A rotated (already-replaced) token is being replayed → revoke the chain.
        _revoke_all_active(db, record.agent_id)
        db.commit()
        raise UnauthorizedError("Refresh token reuse detected")
    if not record.is_active:
        raise UnauthorizedError("Refresh token expired")

    agent = _load_active_agent(db, payload["sub"])
    if agent.id != record.agent_id:
        # Subject and revocation record disagree — refuse rather than guess.
        raise UnauthorizedError("Refresh token subject mismatch")

    access_token, new_refresh, new_jti, refresh_expires_at = _issue_pair(
        db, agent, user_agent=user_agent, ip_address=ip_address
    )
    record.revoked_at = _now()
    record.replaced_by_jti = new_jti

    db.commit()
    db.refresh(agent)
    return RefreshResult(
        agent=agent,
        access_token=access_token,
        refresh_token=new_refresh,
        access_expires_in=get_settings().access_token_ttl_seconds,
        refresh_expires_at=refresh_expires_at,
    )


def verify_agent_jwt(db: Session, *, access_token: str) -> WindowsAgent:
    """Resolve a launcher access token to its agent, or raise ``UnauthorizedError``.

    Enforces ``aud='hwax-agent'`` so user tokens cannot authenticate here.
    """
    payload = security.decode_token(
        access_token, expected_type="access", expected_audience=AGENT_AUDIENCE
    )
    return _load_active_agent(db, payload["sub"])


def _revoke_all_active(db: Session, agent_id: uuid.UUID) -> None:
    """Revoke every still-active refresh token for an agent (reuse response)."""
    now = _now()
    rows = db.execute(
        select(AgentRefreshToken).where(AgentRefreshToken.agent_id == agent_id)
    ).scalars()
    for row in rows:
        if row.revoked_at is None:
            row.revoked_at = now
