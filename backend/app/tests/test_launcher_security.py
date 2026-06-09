"""Launcher security hardening tests:
  - agent_service audits enroll + refresh-reuse (chain-revoke).
  - /installers download routes gate on publishable (non-archived) app status.

Savepoint roll-back; skips when Postgres is unreachable.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.errors import UnauthorizedError
from app.core.security import create_access_token
from app.db.models.agent_refresh_token import AgentRefreshToken
from app.db.models.app import App, AppStatus, AppType, AppVisibility, ExecutionTarget
from app.db.models.audit_log import AuditLog
from app.db.models.installer_package import InstallerPackage
from app.db.models.user import AuthSource, User, UserRole, UserStatus
from app.db.session import engine, get_db
from app.main import app as fastapi_app
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
        pytest.skip("database unreachable")
    conn = engine.connect()
    txn = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield s
    finally:
        s.close()
        txn.rollback()
        conn.close()


@pytest.fixture()
def ctx() -> Iterator[tuple[Session, TestClient]]:
    if not _db_reachable():
        pytest.skip("database unreachable")
    conn = engine.connect()
    txn = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    fastapi_app.dependency_overrides[get_db] = lambda: s
    client = TestClient(fastapi_app)
    try:
        yield s, client
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)
        s.close()
        txn.rollback()
        conn.close()


def _launcher(session: Session, name: str):
    agent, token = agent_registry.register_agent(
        session, name=name, pool="hwax-launcher", device_kind="launcher"
    )
    return agent, token


def _agent_audit(session: Session, agent_id, action: str) -> list[AuditLog]:
    return list(session.execute(
        select(AuditLog).where(
            AuditLog.target_type == "windows_agent",
            AuditLog.target_id == str(agent_id),
            AuditLog.action == action,
        )
    ).scalars().all())


# ── audit lifecycle ──────────────────────────────────────────────────────────


def test_enroll_writes_audit(db: Session) -> None:
    agent, token = _launcher(db, "sec-enroll-1")
    agent_service.redeem_enrollment_token(
        db, enrollment_token=token, hostname="ws-1", ip_address="1.2.3.4"
    )
    rows = _agent_audit(db, agent.id, "agent.enroll")
    assert len(rows) == 1
    assert rows[0].actor_user_id is None
    assert rows[0].meta["actor"] == "system:hwax-agent"
    assert rows[0].ip_address == "1.2.3.4"


def test_refresh_reuse_audits_and_revokes_chain(db: Session) -> None:
    agent, token = _launcher(db, "sec-reuse-1")
    first = agent_service.redeem_enrollment_token(db, enrollment_token=token)
    agent_service.rotate_refresh(db, refresh_token=first["refresh_token"])  # rotate once
    with pytest.raises(UnauthorizedError):
        agent_service.rotate_refresh(db, refresh_token=first["refresh_token"])  # replay

    rows = _agent_audit(db, agent.id, "agent.refresh.reuse_detected")
    assert len(rows) == 1 and rows[0].meta["severity"] == "error"
    # Whole chain revoked.
    active = db.execute(
        select(AgentRefreshToken).where(AgentRefreshToken.agent_id == agent.id)
    ).scalars().all()
    assert all(r.revoked_at is not None for r in active)


# ── download publishable gate ────────────────────────────────────────────────


def _make_app(session: Session, app_id: str, *, status: AppStatus) -> None:
    owner = User(
        email=f"sec-owner-{app_id}@example.com", display_name="O", organization="t",
        password_hash="x", auth_source=AuthSource.LOCAL, email_verified=True,
        status=UserStatus.ACTIVE, role=UserRole.ADMIN,
    )
    session.add(owner)
    session.flush()
    session.add(App(
        id=app_id, name=app_id, owner_user_id=owner.id, app_type=AppType.WINDOWS_GUI,
        execution_target=ExecutionTarget.LOCAL_PC, status=status,
        visibility=AppVisibility.TEAM, upstream_repo_url="https://e.test/x.git",
        workspace_path="/tmp/x",
    ))
    session.flush()


def _pkg(session: Session, app_id: str) -> InstallerPackage:
    row = InstallerPackage(
        app_id=app_id, version="1.0.0", os="windows-x64",
        installer_url="https://store.test/x.exe", sha256="a" * 64, signed=True,
    )
    session.add(row)
    session.flush()
    return row


def _launcher_token(session: Session, name: str) -> str:
    agent, _ = agent_registry.register_agent(
        session, name=name, pool="hwax-launcher", device_kind="launcher"
    )
    return create_access_token(str(agent.id), extra={"aud": "hwax-agent"})


def test_download_servable_app_redirects(ctx) -> None:
    session, client = ctx
    tok = _launcher_token(session, "sec-dl-ok")
    _make_app(session, "sec_dl_ok", status=AppStatus.STABLE)
    pkg = _pkg(session, "sec_dl_ok")
    resp = client.get(
        f"/api/v1/installers/{pkg.id}/download",
        headers={"Authorization": f"Bearer {tok}"},
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text


def test_download_archived_app_404(ctx) -> None:
    session, client = ctx
    tok = _launcher_token(session, "sec-dl-arch")
    _make_app(session, "sec_dl_arch", status=AppStatus.ARCHIVED)
    pkg = _pkg(session, "sec_dl_arch")
    resp = client.get(
        f"/api/v1/installers/{pkg.id}/download",
        headers={"Authorization": f"Bearer {tok}"},
        follow_redirects=False,
    )
    assert resp.status_code == 404, resp.text
