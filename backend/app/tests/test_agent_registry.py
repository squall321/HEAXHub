"""Tests for windows agent_registry (SA5).

DB tests use the same savepoint-roll-back trick as test_common_infra; if the
database is unreachable they skip.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.models.app import App, AppStatus, AppType, AppVisibility, ExecutionTarget
from app.db.models.job import Job, JobStatus
from app.db.models.user import AuthSource, User, UserRole, UserStatus
from app.db.models.windows_agent import WindowsAgent
from app.db.session import engine
from app.services import agent_registry


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


# ── token roundtrip ────────────────────────────────────────────────────────────


def test_register_agent_returns_plaintext_and_stores_hash(db: Session) -> None:
    agent, token = agent_registry.register_agent(
        db, name="pytest-agent-1", pool="pytest-pool", hostname="ws01"
    )
    assert isinstance(token, str) and len(token) >= 32
    # Plaintext is NOT stored.
    assert agent.auth_token_hash != token
    expected = hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert agent.auth_token_hash == expected


def test_verify_token_roundtrip(db: Session) -> None:
    _, token = agent_registry.register_agent(db, name="pytest-agent-2", pool="p")
    found = agent_registry.verify_token(db, token=token)
    assert found is not None
    assert found.name == "pytest-agent-2"

    # Wrong token returns None.
    assert agent_registry.verify_token(db, token="nope-not-a-real-token") is None

    # Disabled agent rejected even with valid token.
    agent_registry.disable(db, found.id)
    assert agent_registry.verify_token(db, token=token) is None


# ── dispatch picks online, non-busy agent ──────────────────────────────────────


def test_dispatch_job_to_pool_picks_free_agent(db: Session) -> None:
    pool = "pytest-dispatch-pool"

    free, _ = agent_registry.register_agent(db, name="dispatch-free", pool=pool)
    busy, _ = agent_registry.register_agent(db, name="dispatch-busy", pool=pool)

    free.status = "online"
    busy.status = "busy"
    db.commit()

    # Build a fake Job — only fields needed by the dispatcher.
    user = User(
        email="agent-test@example.com",
        display_name="Agent Test",
        organization="t",
        password_hash="x",
        auth_source=AuthSource.LOCAL,
        email_verified=True,
        status=UserStatus.ACTIVE,
        role=UserRole.ADMIN,
    )
    db.add(user)
    db.flush()

    app = App(
        id="pytest_agent_app",
        name="Agent test app",
        owner_user_id=user.id,
        app_type=AppType.WINDOWS_GUI,
        execution_target=ExecutionTarget.WINDOWS_WORKER,
        status=AppStatus.DRAFT,
        visibility=AppVisibility.TEAM,
        upstream_repo_url="https://example.com/x.git",
        workspace_path="/tmp/x",
    )
    db.add(app)
    db.flush()

    job = Job(
        id="job_test_dispatch_0001",
        app_id=app.id,
        executor_user_id=user.id,
        status=JobStatus.QUEUED,
        execution_target="windows_worker",
        params_json={},
        input_files=[],
        storage_path="/tmp/x",
    )
    db.add(job)
    db.flush()

    picked = agent_registry.dispatch_job_to_pool(db, job=job, pool=pool)
    assert picked is not None
    assert picked.id == free.id
    assert picked.status == "busy"

    # No more online agents — next dispatch returns None.
    nxt = agent_registry.dispatch_job_to_pool(db, job=job, pool=pool)
    assert nxt is None
