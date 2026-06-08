"""Tests for admin launcher-fleet observability:
GET /api/v1/admin/agents (device_kind filter), /{id}, /{id}/installs, /{id}/audit.

TestClient + savepoint get_db override (DB rolls back). Skips when Postgres is
unreachable.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.security import create_access_token
from app.db.models.audit_log import AuditLog
from app.db.models.install_report import InstallReport
from app.db.models.user import AuthSource, User, UserRole, UserStatus
from app.db.session import engine, get_db
from app.main import app as fastapi_app
from app.services import agent_registry


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


@pytest.fixture()
def ctx() -> Iterator[tuple[Session, TestClient]]:
    if not _db_reachable():
        pytest.skip("database unreachable; skipping DB-backed test")
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    fastapi_app.dependency_overrides[get_db] = lambda: session
    client = TestClient(fastapi_app)
    try:
        yield session, client
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)
        session.close()
        transaction.rollback()
        connection.close()


def _user(session: Session, *, role: UserRole, email: str) -> str:
    u = User(
        email=email,
        display_name="T",
        organization="t",
        password_hash="x",
        auth_source=AuthSource.LOCAL,
        email_verified=True,
        status=UserStatus.ACTIVE,
        role=role,
    )
    session.add(u)
    session.flush()
    return create_access_token(str(u.id))  # user token (no audience)


def _launcher(session: Session, name: str):
    agent, _ = agent_registry.register_agent(
        session, name=name, pool="hwax-launcher", device_kind="launcher"
    )
    return agent


def _seed_reports(session: Session, agent_id) -> None:
    now = datetime.now(timezone.utc)
    session.add_all([
        InstallReport(
            agent_id=agent_id, app_id="koo-tool", version="1.0.0",
            status="success", started_at=now, finished_at=now, sha256_verified=True,
        ),
        InstallReport(
            agent_id=agent_id, app_id="koo-tool", version="1.1.0",
            status="failed", started_at=now, finished_at=now, error="boom",
        ),
    ])
    session.add(AuditLog(
        actor_user_id=None, action="agent.install", target_type="windows_agent",
        target_id=str(agent_id),
        meta={"actor": "system:hwax-agent", "kind": "install", "severity": "info"},
    ))
    session.flush()


def _admin(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


# ── list + filter ─────────────────────────────────────────────────────────────


def test_list_filters_by_device_kind(ctx) -> None:
    session, client = ctx
    admin = _user(session, role=UserRole.ADMIN, email="obs-admin-1@example.com")
    _launcher(session, "obs-launcher-1")
    agent_registry.register_agent(session, name="obs-service-1", pool="p", device_kind="service")

    resp = client.get("/api/v1/admin/agents", params={"device_kind": "launcher"}, headers=_admin(admin))
    assert resp.status_code == 200, resp.text
    names = {a["name"] for a in resp.json()}
    assert "obs-launcher-1" in names
    assert "obs-service-1" not in names


def test_requires_admin_403(ctx) -> None:
    session, client = ctx
    user = _user(session, role=UserRole.USER, email="obs-user-1@example.com")
    resp = client.get("/api/v1/admin/agents", headers=_admin(user))
    assert resp.status_code == 403, resp.text


# ── detail + installs + audit ─────────────────────────────────────────────────


def test_agent_detail_and_history(ctx) -> None:
    session, client = ctx
    admin = _user(session, role=UserRole.ADMIN, email="obs-admin-2@example.com")
    agent = _launcher(session, "obs-launcher-2")
    _seed_reports(session, agent.id)
    aid = str(agent.id)

    # detail
    d = client.get(f"/api/v1/admin/agents/{aid}", headers=_admin(admin))
    assert d.status_code == 200, d.text
    assert d.json()["device_kind"] == "launcher"

    # install history (newest first, both rows)
    ins = client.get(f"/api/v1/admin/agents/{aid}/installs", headers=_admin(admin))
    assert ins.status_code == 200, ins.text
    rows = ins.json()
    assert len(rows) == 2
    assert {r["status"] for r in rows} == {"success", "failed"}

    # filtered by status
    failed = client.get(
        f"/api/v1/admin/agents/{aid}/installs", params={"status": "failed"}, headers=_admin(admin)
    )
    assert failed.status_code == 200
    assert [r["status"] for r in failed.json()] == ["failed"]

    # audit history
    aud = client.get(f"/api/v1/admin/agents/{aid}/audit", headers=_admin(admin))
    assert aud.status_code == 200, aud.text
    audit_rows = aud.json()
    assert len(audit_rows) == 1
    assert audit_rows[0]["action"] == "agent.install"
    assert audit_rows[0]["meta"]["actor"] == "system:hwax-agent"


def test_unknown_agent_404(ctx) -> None:
    session, client = ctx
    admin = _user(session, role=UserRole.ADMIN, email="obs-admin-3@example.com")
    missing = str(uuid.uuid4())
    assert client.get(f"/api/v1/admin/agents/{missing}", headers=_admin(admin)).status_code == 404
    assert client.get(f"/api/v1/admin/agents/{missing}/installs", headers=_admin(admin)).status_code == 404
    assert client.get(f"/api/v1/admin/agents/{missing}/audit", headers=_admin(admin)).status_code == 404
