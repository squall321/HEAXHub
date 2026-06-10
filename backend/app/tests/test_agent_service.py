"""Tests for agent_service (HWAXAgent launcher JWT enroll/refresh/verify).

DB tests use the same savepoint-roll-back trick as test_agent_registry; if the
database is unreachable they skip.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import security
from app.core.errors import ForbiddenError, UnauthorizedError
from app.db.models.agent_refresh_token import AgentRefreshToken
from app.db.models.audit_log import AuditLog
from app.db.session import engine
from app.services import agent_registry, agent_service


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


@pytest.fixture()
def db() -> Iterator[Session]:
    if not _db_reachable():
        pytest.skip("database unreachable; skipping DB-backed test")
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _enroll_launcher(db: Session, name: str) -> tuple[str, object]:
    """Register a launcher agent and return (enrollment_token, agent)."""
    agent, token = agent_registry.register_agent(
        db, name=name, pool="hwax-launcher", device_kind="launcher"
    )
    return token, agent


# ── enrollment ──────────────────────────────────────────────────────────────────


def test_redeem_mints_pair_and_burns_token(db: Session) -> None:
    token, agent = _enroll_launcher(db, "svc-enroll-1")

    result = agent_service.redeem_enrollment_token(
        db, enrollment_token=token, hostname="ws-01", agent_version="0.2.0"
    )

    # Access token resolves back to the same agent and carries the audience.
    resolved = agent_service.verify_agent_jwt(db, access_token=result.access_token)
    assert resolved.id == agent.id
    assert agent_service.AGENT_AUDIENCE == "hwax-agent"

    # hostname / version were recorded from the enrollment request.
    assert result.agent.hostname == "ws-01"
    assert result.agent.agent_version == "0.2.0"

    # A refresh revocation record was persisted and is active.
    record = db.execute(
        select(AgentRefreshToken).where(AgentRefreshToken.agent_id == agent.id)
    ).scalar_one()
    assert record.is_active

    # The single-use enrollment token is burned — it can never be redeemed again.
    assert agent_registry.verify_token(db, token=token) is None
    with pytest.raises(UnauthorizedError):
        agent_service.redeem_enrollment_token(db, enrollment_token=token)


def test_redeem_rejects_service_agent(db: Session) -> None:
    # A 'service' (polling) agent is not allowed to enroll for JWTs.
    _agent, plain = agent_registry.register_agent(
        db, name="svc-poller-1", pool="p", device_kind="service"
    )
    with pytest.raises(ForbiddenError):
        agent_service.redeem_enrollment_token(db, enrollment_token=plain)


def test_redeem_rejects_unknown_token(db: Session) -> None:
    with pytest.raises(UnauthorizedError):
        agent_service.redeem_enrollment_token(
            db, enrollment_token="not-a-real-enrollment-token"
        )


# ── verify / audience isolation ─────────────────────────────────────────────────


def test_verify_rejects_user_token(db: Session) -> None:
    # A plain user access token (no aud) must not authenticate a launcher route.
    user_token = security.create_access_token("00000000-0000-0000-0000-000000000001")
    with pytest.raises(UnauthorizedError):
        agent_service.verify_agent_jwt(db, access_token=user_token)


def test_launcher_token_rejected_on_user_route(db: Session) -> None:
    # The reverse: an aud-scoped launcher token must not pass a no-audience decode.
    token, _ = _enroll_launcher(db, "svc-aud-1")
    result = agent_service.redeem_enrollment_token(db, enrollment_token=token)
    with pytest.raises(UnauthorizedError):
        security.decode_token(result.access_token, expected_type="access")


def test_verify_rejects_refresh_token(db: Session) -> None:
    token, _ = _enroll_launcher(db, "svc-typecheck-1")
    result = agent_service.redeem_enrollment_token(db, enrollment_token=token)
    # Presenting a refresh token where an access token is required → rejected.
    with pytest.raises(UnauthorizedError):
        agent_service.verify_agent_jwt(db, access_token=result.refresh_token)


# ── refresh rotation ────────────────────────────────────────────────────────────


def test_rotate_issues_new_pair_and_revokes_old(db: Session) -> None:
    token, agent = _enroll_launcher(db, "svc-rotate-1")
    first = agent_service.redeem_enrollment_token(db, enrollment_token=token)

    second = agent_service.rotate_refresh(db, refresh_token=first.refresh_token)

    # New access token still resolves to the agent.
    assert agent_service.verify_agent_jwt(db, access_token=second.access_token).id == agent.id
    # New refresh differs from the old one.
    assert second.refresh_token != first.refresh_token

    # Old jti is now revoked and points at its replacement.
    old_jti = security.decode_token(first.refresh_token, expected_type="refresh")["jti"]
    new_jti = security.decode_token(second.refresh_token, expected_type="refresh")["jti"]
    old_record = db.execute(
        select(AgentRefreshToken).where(AgentRefreshToken.jti == old_jti)
    ).scalar_one()
    assert old_record.revoked_at is not None
    assert old_record.replaced_by_jti == new_jti


def test_rotate_reuse_detection_revokes_chain(db: Session) -> None:
    token, agent = _enroll_launcher(db, "svc-reuse-1")
    first = agent_service.redeem_enrollment_token(db, enrollment_token=token)
    second = agent_service.rotate_refresh(db, refresh_token=first.refresh_token)

    # Replaying the already-rotated (revoked) token is treated as reuse.
    with pytest.raises(UnauthorizedError):
        agent_service.rotate_refresh(db, refresh_token=first.refresh_token)

    # The reuse response revokes EVERY active refresh token for the agent,
    # including the otherwise-valid second one.
    active = db.execute(
        select(AgentRefreshToken).where(AgentRefreshToken.agent_id == agent.id)
    ).scalars().all()
    assert all(r.revoked_at is not None for r in active)
    with pytest.raises(UnauthorizedError):
        agent_service.rotate_refresh(db, refresh_token=second.refresh_token)


def test_rotate_rejects_access_token(db: Session) -> None:
    token, _ = _enroll_launcher(db, "svc-rotate-typecheck-1")
    first = agent_service.redeem_enrollment_token(db, enrollment_token=token)
    # An access token (type=access, aud-scoped) is not a valid refresh token.
    with pytest.raises(UnauthorizedError):
        agent_service.rotate_refresh(db, refresh_token=first.access_token)


def test_rotate_rejects_disabled_agent(db: Session) -> None:
    token, agent = _enroll_launcher(db, "svc-disabled-1")
    first = agent_service.redeem_enrollment_token(db, enrollment_token=token)
    agent_registry.disable(db, agent.id)
    with pytest.raises(UnauthorizedError):
        agent_service.rotate_refresh(db, refresh_token=first.refresh_token)


# ── audit lifecycle (enroll + reuse) ─────────────────────────────────────────────


def _agent_audit(db: Session, agent_id, action: str) -> list[AuditLog]:
    return list(
        db.execute(
            select(AuditLog).where(
                AuditLog.target_type == "windows_agent",
                AuditLog.target_id == str(agent_id),
                AuditLog.action == action,
            )
        ).scalars().all()
    )


def test_enroll_writes_audit(db: Session) -> None:
    token, agent = _enroll_launcher(db, "svc-audit-enroll")
    agent_service.redeem_enrollment_token(
        db, enrollment_token=token, hostname="ws-9", ip_address="1.2.3.4"
    )
    rows = _agent_audit(db, agent.id, "agent.enroll")
    assert len(rows) == 1
    assert rows[0].actor_user_id is None
    assert rows[0].meta["actor"] == "system:hwax-agent"
    assert rows[0].ip_address == "1.2.3.4"


def test_reuse_writes_audit(db: Session) -> None:
    token, agent = _enroll_launcher(db, "svc-audit-reuse")
    first = agent_service.redeem_enrollment_token(db, enrollment_token=token)
    agent_service.rotate_refresh(db, refresh_token=first.refresh_token)  # rotate once
    with pytest.raises(UnauthorizedError):
        agent_service.rotate_refresh(db, refresh_token=first.refresh_token)  # replay
    rows = _agent_audit(db, agent.id, "agent.refresh.reuse_detected")
    assert len(rows) == 1
    assert rows[0].meta["severity"] == "error"
