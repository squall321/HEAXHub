"""Pre-check guard test for license_manager.acquire().

The contract under test: ``acquire()`` must consult the provider's
``check_available(feature)`` *before* taking the ``SELECT ... FOR UPDATE`` row
lock, so a saturated upstream license server is rejected without ever
touching the pool row. When the provider returns 0, the call raises
:class:`LicenseUnavailableError` and no ``license_holdings`` row is inserted.

Uses an in-process ``MockProvider`` (a tiny stand-in, distinct from the
DB-backed :class:`MockLicenseProvider`) so the test is hermetic and does not
depend on settings parsing.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.models.app import App, AppStatus
from app.db.models.job import Job, JobStatus
from app.db.models.license_holding import LicenseHolding
from app.db.models.license_pool import LicensePool
from app.db.models.user import User, UserRole, UserStatus
from app.db.session import engine
from app.services import license_manager, license_providers


# ─── DB session fixture (savepoint, rolled back) ─────────────────────────────


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


# ─── Hermetic mock provider ──────────────────────────────────────────────────


class MockProvider(license_providers.BaseLicenseProvider):
    """Always reports the configured number of tokens free."""

    name = "test_mock"

    def __init__(self, available: int) -> None:
        self._available = available

    def check_available(self, feature: str) -> int:  # noqa: D401
        return self._available

    def health(self) -> dict:
        return {"provider": self.name, "status": "ok"}


def _make_pool_and_job(
    db: Session, pool_name: str, total: int, feature: str
) -> tuple[str, str]:
    user = User(
        email=f"precheck_{uuid.uuid4().hex[:8]}@example.com",
        display_name="Precheck Tester",
        organization="qa",
        password_hash="x",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
    )
    db.add(user)
    db.flush()

    app_id = f"licapp_{uuid.uuid4().hex[:8]}"
    app = App(
        id=app_id,
        name="precheck app",
        owner_user_id=user.id,
        status=AppStatus.STABLE,
        app_type="cli_tool",
        execution_target="linux_runner",
        upstream_repo_url="https://example.com/dummy.git",
        workspace_path=f"./app_workspaces/{app_id}",
    )
    db.add(app)
    db.flush()

    job_id = f"licjob_{uuid.uuid4().hex[:8]}"
    job = Job(
        id=job_id,
        app_id=app.id,
        executor_user_id=user.id,
        status=JobStatus.QUEUED,
        execution_target="linux_runner",
        storage_path=f"./job_storage/{job_id}",
    )
    db.add(job)
    db.flush()

    pool = LicensePool(name=pool_name, total_tokens=total, feature=feature)
    db.add(pool)
    db.commit()
    return str(pool.id), job.id


# ─── Tests ───────────────────────────────────────────────────────────────────


def test_acquire_calls_precheck_and_raises_when_provider_zero(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider returns 0 → acquire() raises before any holding is inserted."""
    feature = f"feat_{uuid.uuid4().hex[:6]}"
    pool_name = f"pool_{uuid.uuid4().hex[:6]}"
    pool_id, job_id = _make_pool_and_job(db, pool_name, total=4, feature=feature)

    monkeypatch.setattr(
        license_manager, "get_provider", lambda _db: MockProvider(available=0)
    )

    with pytest.raises(license_manager.LicenseUnavailableError) as excinfo:
        license_manager.acquire(
            db, pool_name=pool_name, tokens=1, job_id=job_id, wait_seconds=0
        )

    assert excinfo.value.details["available"] == 0
    assert excinfo.value.details["feature"] == feature
    assert excinfo.value.details["requested"] == 1

    rows = list(
        db.execute(
            select(LicenseHolding).where(LicenseHolding.pool_id == uuid.UUID(pool_id))
        ).scalars()
    )
    assert rows == []


def test_acquire_proceeds_when_provider_reports_enough(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sanity counter-test: provider says >= tokens → acquire() succeeds."""
    feature = f"feat_{uuid.uuid4().hex[:6]}"
    pool_name = f"pool_{uuid.uuid4().hex[:6]}"
    _pool_id, job_id = _make_pool_and_job(db, pool_name, total=4, feature=feature)

    monkeypatch.setattr(
        license_manager, "get_provider", lambda _db: MockProvider(available=4)
    )

    holding = license_manager.acquire(
        db, pool_name=pool_name, tokens=2, job_id=job_id, wait_seconds=0
    )
    assert holding is not None
    assert holding.tokens == 2
