"""Windows Worker Agent registry — token issuance, heartbeat, dispatch.

Tokens are random URL-safe strings; only their SHA256 hash is persisted. The
plaintext is returned to the operator exactly ONCE at registration time.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.job import Job
from app.db.models.windows_agent import WindowsAgent


# ── token helpers ──────────────────────────────────────────────────────────────


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def fresh_token_hash() -> str:
    """Return a SHA256 hash of a fresh random token whose plaintext is discarded.

    Used to *burn* a single-use enrollment token: after a launcher redeems its
    enrollment token for a JWT pair, we overwrite ``auth_token_hash`` with this
    so the original enrollment string can never verify again, and — because the
    plaintext is thrown away — no new bearer token exists either. The launcher
    authenticates with its JWT pair from then on.
    """
    return _hash_token(_generate_token())


# ── public api ─────────────────────────────────────────────────────────────────


def register_agent(
    db: Session,
    *,
    name: str,
    pool: str,
    hostname: str | None = None,
    capabilities: dict[str, Any] | None = None,
    device_kind: str | None = None,
) -> tuple[WindowsAgent, str]:
    """Register a new agent and return the (row, plaintext_token).

    The plaintext token is shown to the operator once; only SHA256(token) is stored.
    ``device_kind`` is 'launcher' (HWAXAgent) or 'service' (polling worker); None
    keeps the legacy behaviour.
    """
    token = _generate_token()
    agent = WindowsAgent(
        name=name,
        pool=pool,
        hostname=hostname,
        capabilities=capabilities,
        auth_token_hash=_hash_token(token),
        status="unknown",
        disabled=False,
        device_kind=device_kind,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent, token


def verify_token(db: Session, *, token: str) -> WindowsAgent | None:
    """Return the agent matching this plaintext token, or None.

    Disabled agents are rejected.
    """
    if not token:
        return None
    digest = _hash_token(token)
    stmt = select(WindowsAgent).where(WindowsAgent.auth_token_hash == digest)
    agent = db.execute(stmt).scalar_one_or_none()
    if agent is None or agent.disabled:
        return None
    return agent


def heartbeat(
    db: Session,
    *,
    agent_id: Any,
    status: str,
    agent_version: str | None = None,
) -> None:
    """Update last_seen + status (+ agent_version) for the given agent."""
    agent = db.get(WindowsAgent, agent_id)
    if agent is None:
        return
    agent.status = status
    agent.last_seen = datetime.now(timezone.utc)
    if agent_version is not None:
        agent.agent_version = agent_version
    db.commit()


def list_agents(
    db: Session, pool: str | None = None, device_kind: str | None = None
) -> list[WindowsAgent]:
    stmt = select(WindowsAgent).order_by(WindowsAgent.created_at.desc())
    if pool:
        stmt = stmt.where(WindowsAgent.pool == pool)
    if device_kind:
        stmt = stmt.where(WindowsAgent.device_kind == device_kind)
    return list(db.execute(stmt).scalars().all())


def disable(db: Session, agent_id: Any) -> WindowsAgent | None:
    agent = db.get(WindowsAgent, agent_id)
    if agent is None:
        return None
    agent.disabled = True
    agent.status = "offline"
    db.commit()
    db.refresh(agent)
    return agent


def dispatch_job_to_pool(
    db: Session, *, job: Job, pool: str
) -> WindowsAgent | None:
    """Pick an online, non-busy, non-disabled agent in the given pool, mark busy, return.

    Selection is best-effort: oldest `last_seen` wins (round-robin-ish).
    Returns None if no eligible agent is available.
    """
    stmt = (
        select(WindowsAgent)
        .where(WindowsAgent.pool == pool)
        .where(WindowsAgent.disabled.is_(False))
        .where(WindowsAgent.status == "online")
        .order_by(WindowsAgent.last_seen.asc().nulls_last())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    agent = db.execute(stmt).scalar_one_or_none()
    if agent is None:
        return None
    agent.status = "busy"
    db.commit()
    db.refresh(agent)
    return agent
