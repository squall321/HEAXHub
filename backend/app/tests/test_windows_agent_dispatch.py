"""Dispatch-path test for :class:`WindowsAgentClient`.

Exercises *only* the hub-side dispatch logic:
    pick an online agent → ``LPUSH agent:{id}:queue {payload}`` →
    ``SET job:{id}:agent {agent_id}``.

The agent execution side (the .exe running the job, log streaming back, etc.)
is *not* covered — that needs real Windows hardware.

Skip rules:
    * No DB                → skipped (matches the rest of the suite).
    * No fakeredis *and*
      no live redis at
      ``settings.redis_url``  → skipped, never hard-fail.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.app import (
    App,
    AppStatus,
    AppType,
    AppVisibility,
    ExecutionTarget,
)
from app.db.models.job import Job, JobStatus
from app.db.models.user import AuthSource, User, UserRole, UserStatus
from app.db.session import engine
from app.runners import windows_agent_client as wac_mod
from app.runners.windows_agent_client import WindowsAgentClient
from app.services import agent_registry


# ── skip gates ────────────────────────────────────────────────────────────────


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


def _make_redis_client() -> Any | None:
    """Return *some* redis-shaped client, or None if no backend is reachable.

    Preference order: fakeredis (no infra) → real redis at settings.redis_url.
    """
    # 1) fakeredis (preferred for unit-ish coverage).
    try:
        import fakeredis  # type: ignore[import-not-found]

        return fakeredis.FakeRedis()
    except ImportError:
        pass

    # 2) Real redis at the configured URL — only if we can ping it.
    try:
        import redis

        client = redis.Redis.from_url(get_settings().redis_url, socket_connect_timeout=1)
        client.ping()
        return client
    except Exception:
        return None


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


@pytest.fixture()
def fake_redis() -> Any:
    client = _make_redis_client()
    if client is None:
        pytest.skip("fakeredis not installed and no live redis at settings.redis_url")
    return client


# ── helpers ───────────────────────────────────────────────────────────────────


def _seed_user_app(db: Session) -> tuple[Any, Any]:
    """Return (user, app) inserted into the test transaction (rolled back at teardown)."""
    user = User(
        email="winagent-dispatch@example.com",
        display_name="Win Agent Dispatch",
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
        id="pytest_winagent_app",
        name="Windows agent dispatch test app",
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
    return user, app


# ── tests ─────────────────────────────────────────────────────────────────────


def test_windows_agent_dispatch_queues_payload(
    monkeypatch: pytest.MonkeyPatch, db: Session, fake_redis: Any
) -> None:
    """Job submitted to a pool with one online agent → payload appears on that
    agent's queue and the job→agent assignment is recorded.
    """
    from contextlib import contextmanager

    # Patch the runner module's redis getter to our fake/live client.
    monkeypatch.setattr(wac_mod, "_redis", lambda: fake_redis)

    # Route the runner's `with SessionLocal() as db:` back to the test's
    # savepoint-bound session so it can see rows we just registered.
    @contextmanager
    def _scoped() -> Iterator[Session]:
        yield db

    monkeypatch.setattr(wac_mod, "SessionLocal", _scoped)

    pool = "pytest-dispatch-pool-2"
    agent, _token = agent_registry.register_agent(
        db, name="pytest-dispatch-agent", pool=pool, hostname="win-test"
    )
    agent.status = "online"
    db.commit()

    user, app = _seed_user_app(db)

    job = Job(
        id="job_test_winagent_dispatch_0001",
        app_id=app.id,
        executor_user_id=user.id,
        status=JobStatus.QUEUED,
        execution_target="windows_worker",
        params_json={"__agent_pool": pool},
        input_files=[],
        storage_path="/tmp/x",
    )
    db.add(job)
    db.flush()

    runner = WindowsAgentClient()
    runner.start(job)

    # 1) payload landed on agent:{id}:queue (LPUSH → pop from the right).
    queue_key = f"agent:{agent.id}:queue"
    raw = fake_redis.rpop(queue_key)
    assert raw is not None, f"no payload found on {queue_key}"
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    payload = json.loads(raw)
    assert payload["job_id"] == job.id
    assert payload["app_id"] == job.app_id
    assert payload["storage_path"] == job.storage_path

    # 2) job→agent assignment recorded.
    assignment_key = f"job:{job.id}:agent"
    stored = fake_redis.get(assignment_key)
    if isinstance(stored, bytes):
        stored = stored.decode("utf-8")
    assert stored == str(agent.id)


def test_windows_agent_dispatch_no_available_agent_raises(
    monkeypatch: pytest.MonkeyPatch, db: Session, fake_redis: Any
) -> None:
    """Empty pool → ``start`` raises a clear ``RuntimeError``."""
    from contextlib import contextmanager

    monkeypatch.setattr(wac_mod, "_redis", lambda: fake_redis)

    @contextmanager
    def _scoped() -> Iterator[Session]:
        yield db

    monkeypatch.setattr(wac_mod, "SessionLocal", _scoped)

    user, app = _seed_user_app(db)
    job = Job(
        id="job_test_winagent_dispatch_0002",
        app_id=app.id,
        executor_user_id=user.id,
        status=JobStatus.QUEUED,
        execution_target="windows_worker",
        params_json={"__agent_pool": "pool-with-nobody-home"},
        input_files=[],
        storage_path="/tmp/x",
    )
    db.add(job)
    db.flush()

    runner = WindowsAgentClient()
    with pytest.raises(RuntimeError, match="No available Windows agent"):
        runner.start(job)
