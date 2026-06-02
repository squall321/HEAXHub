"""Tests for SA-C ops_tasks — rotate + service recovery.

These mirror the lightweight style of ``test_common_infra.py``: the DB-backed
test takes a savepoint-rolled-back session, and the rotate test only touches
a tmp directory (never the real ``var/jobstorage``).
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.service_instance import ServiceInstance
from app.db.session import engine
from app.workers import ops_tasks


# ─── DB fixture (same shape as test_common_infra) ────────────────────────────


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


# ─── rotate_old_jobs ─────────────────────────────────────────────────────────


def test_rotate_old_jobs_archives_old_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Build a job_storage/{YYYY}/{MM}/job_xxx tree, mark it old, run the task."""
    # Point the settings at a tmp dir — NEVER touch var/jobstorage.
    storage_root = tmp_path / "job_storage"
    job_parent = storage_root / "2020" / "01"
    job_parent.mkdir(parents=True)
    job_dir = job_parent / "job_20200115_0001"
    job_dir.mkdir()
    (job_dir / "result.txt").write_text("dummy", encoding="utf-8")

    # mtime ~ 365d ago so the rotate script's -mtime +90 picks it up.
    one_year_ago = datetime.now().timestamp() - 365 * 86400
    import os as _os

    _os.utime(job_dir, (one_year_ago, one_year_ago))

    settings = get_settings()
    monkeypatch.setattr(settings, "job_storage_root", storage_root)

    # Skip if DB is unreachable — the task writes an audit row.
    if not _db_reachable():
        pytest.skip("database unreachable; skipping rotate task")

    result = ops_tasks.rotate_old_jobs(days_keep=90)

    assert result["ok"] is True, result
    # Original directory should be gone …
    assert not job_dir.exists(), f"original not removed: {job_dir}"
    # … and an archive should exist next to it.
    archives = list(job_parent.glob("job_20200115_0001.tar*"))
    assert archives, f"no archive produced in {job_parent}"


def test_rotate_old_jobs_handles_missing_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If job_storage_root does not exist, the task reports the error gracefully."""
    settings = get_settings()
    monkeypatch.setattr(settings, "job_storage_root", tmp_path / "does_not_exist")

    result = ops_tasks.rotate_old_jobs(days_keep=90)
    assert result["ok"] is False
    assert "missing" in str(result.get("error", "")).lower()


# ─── recover_service_instances ──────────────────────────────────────────────


def test_recover_service_instances_marks_bogus_pid_stopped(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Insert a row with a clearly-dead PID; the task should mark it stopped."""
    # Make the ops_tasks module reach our savepoint-bound session instead of
    # opening a new SessionLocal (which would not see our row).
    from contextlib import contextmanager

    @contextmanager
    def _scoped() -> Iterator[Session]:
        yield db

    monkeypatch.setattr(ops_tasks, "SessionLocal", _scoped)
    # The recovery task also tries to restart via service_manager. We stub that
    # out — we are only validating the bookkeeping path here.
    import app.services.service_manager as service_manager

    def _noop_restart(_db: Session, *, instance_id):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(service_manager, "restart_service", _noop_restart)

    # The App row must exist because ServiceInstance has a FK. We use a raw
    # insert so we don't need to build a full App + User graph.
    app_id = f"opsrec_{uuid.uuid4().hex[:8]}"
    db.execute(
        text(
            """
            INSERT INTO apps (
                id, name, owner_user_id, app_type, execution_target,
                status, visibility, upstream_repo_url, workspace_path
            )
            SELECT :id, :name, u.id, 'cli_tool', 'linux_runner',
                   'stable', 'team', 'https://example.com/x.git', :ws
            FROM users u
            ORDER BY u.created_at
            LIMIT 1
            """
        ),
        {"id": app_id, "name": "ops-recover-test", "ws": f"/tmp/{app_id}"},
    )
    db.commit()

    inst = ServiceInstance(
        id=uuid.uuid4(),
        app_id=app_id,
        version_id=None,
        pid=999999,  # virtually guaranteed to be dead
        port=None,
        status="healthy",
        workdir="/tmp",
        started_at=datetime.now(timezone.utc),
        restart_count=0,
    )
    db.add(inst)
    db.commit()

    result = ops_tasks.recover_service_instances()
    assert result["inspected"] >= 1
    assert result["marked_stopped"] >= 1

    db.refresh(inst)
    assert inst.status == "stopped"
    assert inst.stopped_at is not None
