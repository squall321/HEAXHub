"""Full-lifecycle integration test.

Walks a single tool from submission through approval, clone (from a local
template repo), Python venv build, publish, job submission and execution. The
test runs Celery tasks inline via eager mode and redirects WORKSPACE_ROOT and
JOB_STORAGE_ROOT into a per-test tmpdir so it leaves no host-level artifacts.

Skipped automatically if PostgreSQL is unreachable — mirrors the pattern used
by ``test_common_infra.py``.
"""
from __future__ import annotations

import json
import shutil
import sys
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.app_version import AppVersion, BuildStatus
from app.db.models.job import Job, JobStatus
from app.db.models.submission import Submission, SubmissionStatus
from app.db.models.user import AuthSource, User, UserRole, UserStatus
from app.db.session import SessionLocal, engine
from app.services import app_lifecycle, workspace_manager

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Reachability helpers
# ---------------------------------------------------------------------------


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


def _template_repo() -> Path:
    here = Path(__file__).resolve()
    # backend/app/tests/integration/test_full_lifecycle.py -> repo root
    repo_root = here.parents[4]
    return repo_root / "templates" / "python-cli"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect workspace_root and job_storage_root into a tmpdir for the test."""
    settings = get_settings()
    ws = tmp_path / "workspaces"
    js = tmp_path / "job_storage"
    ws.mkdir(parents=True, exist_ok=True)
    js.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "workspace_root", ws)
    monkeypatch.setattr(settings, "job_storage_root", js)
    # Ensure python_build_path points at the same interpreter the test is running on.
    monkeypatch.setattr(settings, "python_build_path", sys.executable)
    return tmp_path


@pytest.fixture()
def eager_celery() -> Iterator[None]:
    """Run Celery tasks synchronously in-process (no broker required)."""
    from app.workers.celery_app import celery_app

    previous_eager = celery_app.conf.task_always_eager
    previous_propagate = celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    try:
        yield
    finally:
        celery_app.conf.task_always_eager = previous_eager
        celery_app.conf.task_eager_propagates = previous_propagate


@pytest.fixture()
def db_session() -> Iterator[Session]:
    """Plain DB session — this integration test commits real rows."""
    if not _db_reachable():
        pytest.skip("database unreachable; skipping integration test")
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def admin_user(db_session: Session) -> Iterator[User]:
    """Create a throwaway admin user and clean up afterwards."""
    email = f"integration-admin-{uuid.uuid4().hex[:8]}@example.com"
    user = User(
        email=email,
        display_name="Integration Admin",
        organization="Test",
        password_hash="x",
        auth_source=AuthSource.LOCAL,
        status=UserStatus.ACTIVE,
        role=UserRole.ADMIN,
        email_verified=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    try:
        yield user
    finally:
        db_session.delete(user)
        db_session.commit()


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def _cleanup(db: Session, app_id: str, submission_id: uuid.UUID) -> None:
    """Best-effort cleanup: jobs -> submission -> app (cascades to versions)."""
    from app.db.models.app import App as AppModel

    # Drop jobs and clear the submission's test_job_id FK first.
    db.query(Job).filter(Job.app_id == app_id).delete(synchronize_session=False)
    sub = db.get(Submission, submission_id)
    if sub is not None:
        sub.test_job_id = None
    db.commit()

    # Break the circular App<->AppVersion FK so the cascade can fire.
    app = db.get(AppModel, app_id)
    if app is not None:
        app.current_version_id = None
        db.commit()
        # ondelete=CASCADE on AppVersion.app_id sweeps versions automatically.
        db.delete(app)
        db.commit()

    sub = db.get(Submission, submission_id)
    if sub is not None:
        db.delete(sub)
        db.commit()


def test_full_lifecycle_local_template(
    db_session: Session,
    admin_user: User,
    tmp_workspace_root: Path,
    eager_celery: None,
) -> None:
    """Submission -> approve (eager clone+build) -> publish -> run job."""
    template = _template_repo()
    if not template.exists():
        pytest.skip(f"template repo missing at {template}")

    app_id = f"itest_{uuid.uuid4().hex[:10]}"
    sub = Submission(
        submitter_user_id=admin_user.id,
        proposed_app_id=app_id,
        name="Integration Test Tool",
        description="full-lifecycle smoke",
        # The url is required by the model but unused because we set source_config.
        upstream_repo_url="https://github.com/heaxhub/integration-test",
        proposed_app_type="cli_tool",
        proposed_execution_target="linux_runner",
        proposed_manifest=None,
        status=SubmissionStatus.PENDING,
    )
    db_session.add(sub)
    db_session.commit()
    db_session.refresh(sub)
    submission_id = sub.id

    try:
        # --- approve + provision + clone + build (all eager) -----------------
        # clone_upstream re-fetches the Submission from its own SessionLocal so
        # in-memory attribute injection wouldn't survive. Patch fetch_source to
        # always pull from our local template repo instead of hitting the network.
        from app.workers import sync_tasks

        def _fetch_local(_cfg: dict, dest: Path) -> dict:
            from app.services.source_fetcher import fetch_source as real_fetch

            return real_fetch(
                {"type": "local_path", "path": str(template), "sync": "copy"}, dest
            )

        original_fetch = sync_tasks.fetch_source
        sync_tasks.fetch_source = _fetch_local  # type: ignore[assignment]
        try:
            app_lifecycle.approve_and_provision(
                db_session, reviewer=admin_user, submission_id=submission_id
            )
        finally:
            sync_tasks.fetch_source = original_fetch  # type: ignore[assignment]

        # After approve_and_provision, eager celery has run the entire chain:
        # clone_upstream -> build_python_venv
        db_session.expire_all()
        sub = db_session.get(Submission, submission_id)
        assert sub is not None
        assert sub.status in {SubmissionStatus.BUILT, SubmissionStatus.BUILDING}, (
            f"expected built/building, got {sub.status}"
        )

        # Find the version, assert build succeeded.
        version = (
            db_session.query(AppVersion)
            .filter(AppVersion.app_id == app_id)
            .order_by(AppVersion.created_at.desc())
            .first()
        )
        assert version is not None, "no AppVersion created"
        assert version.build_status == BuildStatus.SUCCESS, (
            f"build failed: status={version.build_status}, "
            f"log={version.build_log_path}"
        )

        # --- publish ----------------------------------------------------------
        published = app_lifecycle.publish_app(
            db_session, app_id=app_id, version_id=version.id, actor=admin_user
        )
        assert published.current_version_id == version.id

        # LocalRunner looks for overlay/.portal/run.sh first. clone_upstream
        # only copies manifest.yaml from upstream into overlay, so we mirror
        # what an operator would do: stage the run.sh into the overlay too.
        workspace = get_settings().workspace_root / app_id
        overlay_runsh = workspace / "overlay" / ".portal" / "run.sh"
        upstream_runsh = workspace / "upstream" / ".portal" / "run.sh"
        if upstream_runsh.exists() and not overlay_runsh.exists():
            overlay_runsh.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(upstream_runsh, overlay_runsh)
            overlay_runsh.chmod(0o755)

        # --- create + run a job ----------------------------------------------
        job_id = f"job_{datetime.now(timezone.utc).strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"
        storage = workspace_manager.create_job_storage(job_id)
        # Provide a minimal input file + params.json so the tool produces a result.
        (storage / "input" / "hello.txt").write_text("hi", encoding="utf-8")
        (storage / "params.json").write_text(json.dumps({"sample_count": 1}), encoding="utf-8")

        job = Job(
            id=job_id,
            app_id=app_id,
            app_version_id=version.id,
            executor_user_id=admin_user.id,
            status=JobStatus.QUEUED,
            execution_target="linux_runner",
            params_json={"sample_count": 1},
            input_files=["hello.txt"],
            storage_path=str(storage),
        )
        db_session.add(job)
        db_session.commit()

        # Run it eagerly. LocalRunner publishes to redis — wrap the publish to
        # tolerate redis being down in the test environment.
        from app.runners import local_runner as lr

        original_publish = lr._publish_line
        lr._publish_line = lambda *a, **kw: None  # type: ignore[assignment]
        try:
            from app.workers.job_tasks import run_job

            result = run_job.apply(args=[job_id]).get()
        finally:
            lr._publish_line = original_publish  # type: ignore[assignment]

        assert result.get("ok"), f"run_job failed: {result}"

        # --- assert result.json present + valid -------------------------------
        result_path = storage / "output" / "result.json"
        assert result_path.exists(), "result.json missing from job output"
        result_data = json.loads(result_path.read_text(encoding="utf-8"))
        assert result_data.get("status") in {"success", "warning"}

        db_session.expire_all()
        job_row = db_session.get(Job, job_id)
        assert job_row is not None
        assert job_row.status == JobStatus.SUCCESS

    finally:
        # Best-effort cleanup; we don't want stray rows in the dev DB.
        try:
            _cleanup(db_session, app_id, submission_id)
        except Exception:  # noqa: BLE001 - cleanup must not mask test failure
            db_session.rollback()
        # tmp_workspace_root tears down via tmp_path; explicit rmtree for clarity.
        ws = get_settings().workspace_root / app_id
        if ws.exists():
            shutil.rmtree(ws, ignore_errors=True)
