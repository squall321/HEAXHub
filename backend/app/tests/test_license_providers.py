"""Tests for license providers and the license_manager provider pre-check.

Three groups:
  1. MockLicenseProvider — totals from settings, in-use from license_holdings,
     basic acquire/release roundtrip via license_manager.
  2. FlexLMProvider — when the lmstat binary is missing, check_available()
     returns the UNKNOWN_AVAILABLE sentinel and health() reports "degraded".
  3. license_manager.acquire() refuses when the provider reports 0 available.

DB-backed tests use a savepoint-rolled-back session and skip when the DB is
unreachable, matching test_common_infra.py's style.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.app import App, AppStatus
from app.db.models.job import Job, JobStatus
from app.db.models.license_holding import LicenseHolding
from app.db.models.license_pool import LicensePool
from app.db.models.user import User, UserRole, UserStatus
from app.db.session import engine
from app.services import license_manager
from app.services.license_providers import (
    UNKNOWN_AVAILABLE,
    FlexLMProvider,
    MockLicenseProvider,
    _parse_mock_features,
)


# ─── DB session fixture ─────────────────────────────────────────────────────


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


# ─── _parse_mock_features ────────────────────────────────────────────────────


def test_parse_mock_features_basic() -> None:
    assert _parse_mock_features("lsdyna:8,ansys:4") == {"lsdyna": 8, "ansys": 4}


def test_parse_mock_features_tolerates_garbage() -> None:
    # blanks, missing colon, non-int count — all silently dropped
    parsed = _parse_mock_features("lsdyna:8, ,bad,ansys:notanumber,nastran:2")
    assert parsed == {"lsdyna": 8, "nastran": 2}


def test_parse_mock_features_empty() -> None:
    assert _parse_mock_features("") == {}


# ─── FlexLMProvider (no binary on test host) ─────────────────────────────────


def test_flexlm_provider_missing_binary_returns_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(
        settings, "flexlm_lmstat_bin", "/definitely/not/here/lmstat"
    )
    provider = FlexLMProvider(settings=settings)
    assert provider.check_available("lsdyna") == UNKNOWN_AVAILABLE


def test_flexlm_provider_health_reports_degraded_when_binary_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(
        settings, "flexlm_lmstat_bin", "/definitely/not/here/lmstat"
    )
    provider = FlexLMProvider(settings=settings)
    health = provider.health()
    assert health["provider"] == "flexlm"
    assert health["status"] == "degraded"
    assert health["lmstat_bin_present"] is False


# ─── MockLicenseProvider — DB-backed ─────────────────────────────────────────


def _make_pool_and_job(
    db: Session, pool_name: str, total: int, feature: str | None = None
) -> tuple[str, str]:
    """Create a LicensePool and a parent Job row. Returns (pool_id, job_id)."""
    # User
    user = User(
        email=f"lic_{uuid.uuid4().hex[:8]}@example.com",
        display_name="License Tester",
        organization="qa",
        password_hash="x",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
    )
    db.add(user)
    db.flush()

    # App
    app_id = f"licapp_{uuid.uuid4().hex[:8]}"
    app = App(
        id=app_id,
        name="lic app",
        owner_user_id=user.id,
        status=AppStatus.STABLE,
        app_type="cli_tool",
        execution_target="linux_runner",
        upstream_repo_url="https://example.com/dummy.git",
        workspace_path=f"./app_workspaces/{app_id}",
    )
    db.add(app)
    db.flush()

    # Job
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

    # Pool
    pool = LicensePool(
        name=pool_name,
        total_tokens=total,
        feature=feature,
    )
    db.add(pool)
    db.commit()
    return str(pool.id), job.id


def test_mock_provider_acquire_release_roundtrip(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MockLicenseProvider should see availability shrink after acquire and
    rebound after release. Exercises the license_manager wrapper too."""
    feature = f"lsdyna_{uuid.uuid4().hex[:6]}"
    pool_name = f"pool_{uuid.uuid4().hex[:6]}"
    settings = get_settings()
    monkeypatch.setattr(
        settings, "mock_license_features", f"{feature}:4"
    )
    monkeypatch.setattr(settings, "license_provider", "mock")

    _pool_id, job_id = _make_pool_and_job(db, pool_name, total=4, feature=feature)

    provider = MockLicenseProvider(db=db, settings=settings)
    assert provider.check_available(feature) == 4

    holding = license_manager.acquire(
        db, pool_name=pool_name, tokens=3, job_id=job_id, wait_seconds=0
    )
    assert holding is not None
    # Re-instantiate to ensure no stale cache; re-read DB state.
    provider2 = MockLicenseProvider(db=db, settings=settings)
    assert provider2.check_available(feature) == 1

    license_manager.release(db, holding)
    provider3 = MockLicenseProvider(db=db, settings=settings)
    assert provider3.check_available(feature) == 4


def test_mock_provider_unknown_feature_is_unknown(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "mock_license_features", "lsdyna:8")
    provider = MockLicenseProvider(db=db, settings=get_settings())
    assert provider.check_available("not_configured") == UNKNOWN_AVAILABLE


def test_mock_provider_health(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "mock_license_features", "lsdyna:8,ansys:4")
    provider = MockLicenseProvider(db=db, settings=get_settings())
    h = provider.health()
    assert h["provider"] == "mock"
    assert h["status"] == "ok"
    assert h["features"] == {"lsdyna": 8, "ansys": 4}


# ─── license_manager refuses when provider exhausted ─────────────────────────


def test_acquire_refused_when_provider_reports_zero(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the mock provider says 0 tokens free for the feature, acquire()
    must raise LicenseUnavailableError without inserting a holding row."""
    feature = f"empty_{uuid.uuid4().hex[:6]}"
    pool_name = f"pool_{uuid.uuid4().hex[:6]}"
    settings = get_settings()
    monkeypatch.setattr(settings, "mock_license_features", f"{feature}:0")
    monkeypatch.setattr(settings, "license_provider", "mock")

    pool_id, job_id = _make_pool_and_job(db, pool_name, total=4, feature=feature)

    with pytest.raises(license_manager.LicenseUnavailableError) as excinfo:
        license_manager.acquire(
            db, pool_name=pool_name, tokens=1, job_id=job_id, wait_seconds=0
        )
    assert excinfo.value.details["available"] == 0
    assert excinfo.value.details["feature"] == feature

    # No holding should have been written.
    from sqlalchemy import select as _select
    rows = list(
        db.execute(
            _select(LicenseHolding).where(LicenseHolding.pool_id == uuid.UUID(pool_id))
        ).scalars()
    )
    assert rows == []
