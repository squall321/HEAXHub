"""Tests for the launcher reporting endpoints (NEXT_STEPS §3.1):
POST /api/v1/launcher-agents/{installs,audit,heartbeat}.

TestClient + savepoint get_db override (DB rolls back). Skips when Postgres is
unreachable.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.models.audit_log import AuditLog
from app.db.models.install_report import InstallReport
from app.db.models.windows_agent import WindowsAgent
from app.db.session import engine, get_db
from app.main import app as fastapi_app
from app.services import agent_registry

INSTALLS = "/api/v1/launcher-agents/installs"
AUDIT = "/api/v1/launcher-agents/audit"
HEARTBEAT = "/api/v1/launcher-agents/heartbeat"


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


def _enroll(session: Session, client: TestClient, name: str) -> tuple[str, str]:
    """Register + enroll a launcher; return (access_token, agent_id)."""
    _agent, token = agent_registry.register_agent(
        session, name=name, pool="hwax-launcher", device_kind="launcher"
    )
    resp = client.post(
        "/api/v1/launcher-agents/enroll", json={"enrollment_token": token}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["access_token"], body["agent_id"]


def _auth(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def _report(agent_id: str, **over) -> dict:
    base = {
        "agent_id": agent_id,
        "app_id": "koo-tool",
        "version": "1.2.3",
        "status": "success",
        "started_at": "2026-06-09T00:00:00Z",
        "finished_at": "2026-06-09T00:01:00Z",
        "sha256_verified": True,
    }
    base.update(over)
    return base


# ── installs ──────────────────────────────────────────────────────────────────


def test_installs_persists_report(ctx) -> None:
    session, client = ctx
    tok, agent_id = _enroll(session, client, "rep-install-1")
    resp = client.post(INSTALLS, headers=_auth(tok), json=_report(agent_id))
    assert resp.status_code == 202, resp.text
    assert "id" in resp.json()

    row = session.execute(
        select(InstallReport).where(InstallReport.agent_id == uuid.UUID(agent_id))
    ).scalar_one()
    assert row.app_id == "koo-tool"
    assert row.status == "success"
    assert row.sha256_verified is True


def test_installs_agent_id_mismatch_403(ctx) -> None:
    session, client = ctx
    tok, _agent_id = _enroll(session, client, "rep-install-mismatch")
    body = _report(str(uuid.uuid4()))  # someone else's id
    resp = client.post(INSTALLS, headers=_auth(tok), json=body)
    assert resp.status_code == 403, resp.text


def test_installs_invalid_body_422(ctx) -> None:
    session, client = ctx
    tok, agent_id = _enroll(session, client, "rep-install-422")
    bad = _report(agent_id, status="exploded")  # not in the enum
    resp = client.post(INSTALLS, headers=_auth(tok), json=bad)
    assert resp.status_code == 422, resp.text


def test_installs_requires_auth_401(ctx) -> None:
    session, client = ctx
    _tok, agent_id = _enroll(session, client, "rep-install-noauth")
    resp = client.post(INSTALLS, json=_report(agent_id))  # no bearer
    assert resp.status_code == 401, resp.text


def test_installs_truncates_error(ctx) -> None:
    session, client = ctx
    tok, agent_id = _enroll(session, client, "rep-install-trunc")
    big = "x" * 3000
    resp = client.post(
        INSTALLS, headers=_auth(tok), json=_report(agent_id, status="failed", error=big)
    )
    assert resp.status_code == 202, resp.text
    row = session.execute(
        select(InstallReport).where(InstallReport.agent_id == uuid.UUID(agent_id))
    ).scalar_one()
    assert len(row.error) == 2048  # contract maxLength, truncated server-side


# ── audit ───────────────────────────────────────────────────────────────────────


def test_audit_writes_audit_log(ctx) -> None:
    session, client = ctx
    tok, agent_id = _enroll(session, client, "rep-audit-1")
    event = {
        "agent_id": agent_id,
        "kind": "install",
        "occurred_at": "2026-06-09T00:00:00Z",
        "severity": "info",
        "app_id": "koo-tool",
        "version": "1.2.3",
        "payload": {"outcome": "success"},
        "client_meta": {"os": "windows", "agent_version": "0.2.0"},
    }
    resp = client.post(AUDIT, headers=_auth(tok), json=event)
    assert resp.status_code == 202, resp.text

    rows = session.execute(
        select(AuditLog).where(AuditLog.target_id == agent_id)
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_user_id is None
    assert row.action == "agent.install"
    assert row.target_type == "windows_agent"
    assert row.meta["actor"] == "system:hwax-agent"
    assert row.meta["payload"] == {"outcome": "success"}
    assert row.meta["client_meta"]["os"] == "windows"


def test_audit_agent_id_mismatch_403(ctx) -> None:
    session, client = ctx
    tok, _agent_id = _enroll(session, client, "rep-audit-mismatch")
    event = {
        "agent_id": str(uuid.uuid4()),
        "kind": "run",
        "occurred_at": "2026-06-09T00:00:00Z",
        "severity": "info",
    }
    resp = client.post(AUDIT, headers=_auth(tok), json=event)
    assert resp.status_code == 403, resp.text


def test_audit_invalid_kind_422(ctx) -> None:
    session, client = ctx
    tok, agent_id = _enroll(session, client, "rep-audit-422")
    event = {
        "agent_id": agent_id,
        "kind": "not-a-kind",
        "occurred_at": "2026-06-09T00:00:00Z",
        "severity": "info",
    }
    resp = client.post(AUDIT, headers=_auth(tok), json=event)
    assert resp.status_code == 422, resp.text


# ── heartbeat ─────────────────────────────────────────────────────────────────


def test_heartbeat_updates_agent(ctx) -> None:
    session, client = ctx
    tok, agent_id = _enroll(session, client, "rep-hb-1")
    resp = client.post(
        HEARTBEAT,
        headers=_auth(tok),
        json={
            "agent_version": "0.3.0",
            "hostname": "ws-77",
            "modules": [{"id": "koo-tool", "version": "1.2.3"}],
        },
    )
    assert resp.status_code == 204, resp.text

    agent = session.get(WindowsAgent, uuid.UUID(agent_id))
    assert agent.last_seen is not None
    assert agent.agent_version == "0.3.0"
    assert agent.hostname == "ws-77"
    assert agent.capabilities["modules"]["koo-tool"] == "1.2.3"


def test_heartbeat_requires_auth_401(ctx) -> None:
    _session, client = ctx
    resp = client.post(HEARTBEAT, json={"agent_version": "0.3.0"})
    assert resp.status_code == 401, resp.text
