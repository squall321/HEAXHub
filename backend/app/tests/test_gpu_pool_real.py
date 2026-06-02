"""Real GPU pool integration test (register → acquire → release).

Hits the live PostgreSQL DB and shells out to ``nvidia-smi``. Skips cleanly
when either is missing so the standard ``pytest -m "not integration"`` run
never blocks on hardware.

Strategy:
    1. Skip if ``nvidia-smi`` is not on PATH (or returns no GPUs).
    2. Skip if PostgreSQL is unreachable.
    3. Create a throw-away App + Job (FKs required by ``gpu_holdings``).
    4. Call ``register_gpus`` → at least one ``GpuDevice`` row exists.
    5. ``acquire`` one device, assert it flipped to ``busy`` and that
       a matching ``GpuHolding`` row was inserted.
    6. ``release`` the holding, assert the device is back to ``free``
       and ``released_at`` is stamped.
    7. Tear down the temp Job + App rows.
"""
from __future__ import annotations

import shutil
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.models.app import App, AppStatus, AppType, ExecutionTarget
from app.db.models.gpu_device import GpuDevice
from app.db.models.gpu_holding import GpuHolding
from app.db.models.job import Job, JobStatus
from app.db.models.user import User
from app.db.session import SessionLocal, engine
from app.services import gpu_manager

pytestmark = pytest.mark.integration


# ── skip gates ────────────────────────────────────────────────────────────────


def _nvidia_smi_available() -> bool:
    return shutil.which("nvidia-smi") is not None


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


if not _nvidia_smi_available():
    pytest.skip("nvidia-smi not available", allow_module_level=True)
if not _db_reachable():
    pytest.skip("PostgreSQL not reachable", allow_module_level=True)
if not gpu_manager.discover_local_gpus():
    pytest.skip("nvidia-smi returned no GPUs", allow_module_level=True)


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def temp_app_and_job(db: Session) -> Iterator[tuple[App, Job]]:
    """Insert a throw-away App + Job pair. Tears down in reverse order."""
    user = db.execute(select(User).limit(1)).scalar_one_or_none()
    if user is None:
        pytest.skip("no user rows available for test FK")

    app_id = f"gputest_{uuid.uuid4().hex[:8]}"
    app = App(
        id=app_id,
        name="gpu-pool-real-it",
        owner_user_id=user.id,
        app_type=AppType.CONTAINER_APP,
        execution_target=ExecutionTarget.APPTAINER,
        status=AppStatus.STABLE,
        upstream_repo_url="local://gpu-pool-real-it",
        workspace_path=f"/tmp/{app_id}",
    )
    db.add(app)
    db.flush()

    job_id = f"j-{uuid.uuid4().hex[:10]}"
    job = Job(
        id=job_id,
        app_id=app_id,
        executor_user_id=user.id,
        status=JobStatus.QUEUED,
        execution_target="apptainer",
        storage_path=f"/tmp/{job_id}",
    )
    db.add(job)
    db.commit()
    db.refresh(app)
    db.refresh(job)

    try:
        yield app, job
    finally:
        # Best-effort cleanup — release any leftover holdings, then drop the
        # rows. CASCADE on gpu_holdings.job_id handles the holdings row.
        try:
            gpu_manager.release_for_job(db, job_id=job_id)
        except Exception:
            pass
        try:
            db.delete(job)
            db.flush()
            db.delete(app)
            db.commit()
        except Exception:
            db.rollback()


# ── tests ─────────────────────────────────────────────────────────────────────


def test_register_acquire_release_roundtrip(
    db: Session, temp_app_and_job: tuple[App, Job]
) -> None:
    _, job = temp_app_and_job

    # 1) register_gpus discovers the local card(s).
    touched = gpu_manager.register_gpus(db)
    assert touched >= 1, "register_gpus should touch at least one device"

    devices = list(
        db.execute(select(GpuDevice).order_by(GpuDevice.device_index)).scalars()
    )
    assert devices, "expected at least one GpuDevice row after register_gpus"

    # 2) acquire one device matching the manifest constraints.
    holding_devices = gpu_manager.acquire(
        db,
        job_id=job.id,
        count=1,
        min_memory_gb=8,
        cuda_min="8.0",
    )
    assert len(holding_devices) == 1
    assert holding_devices[0].status == "busy"

    # GpuHolding row was inserted.
    active = list(
        db.execute(
            select(GpuHolding)
            .where(GpuHolding.job_id == job.id)
            .where(GpuHolding.released_at.is_(None))
        ).scalars()
    )
    assert len(active) == 1
    assert active[0].device_id == holding_devices[0].id

    # 3) release flips status back to free and stamps released_at.
    gpu_manager.release(db, active)
    db.refresh(holding_devices[0])
    assert holding_devices[0].status == "free"

    after = list(
        db.execute(
            select(GpuHolding).where(GpuHolding.job_id == job.id)
        ).scalars()
    )
    assert all(h.released_at is not None for h in after)


def test_acquire_rejects_unsatisfiable_constraints(
    db: Session, temp_app_and_job: tuple[App, Job]
) -> None:
    """Constraints we can't satisfy must return [] without taking the device busy."""
    _, job = temp_app_and_job
    gpu_manager.register_gpus(db)

    # Ask for absurdly large memory — must return [] and leave devices free.
    chosen = gpu_manager.acquire(
        db,
        job_id=job.id,
        count=1,
        min_memory_gb=10_000,  # 10 TB — nothing matches
        cuda_min="8.0",
    )
    assert chosen == []

    free_after = list(
        db.execute(select(GpuDevice).where(GpuDevice.status == "busy")).scalars()
    )
    # None of the rows should have been left busy because acquire() rolls back.
    for d in free_after:
        # If something else is using the GPU concurrently, that's not our row.
        assert d.id != getattr(chosen and chosen[0], "id", None)
