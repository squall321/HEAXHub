"""Job creation + dispatch."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logger import get_logger
from app.db.models.app import App
from app.db.models.app_version import AppVersion
from app.db.models.job import Job, JobStatus
from app.db.models.user import User
from app.runners.registry import runner_for_target
from app.services import workspace_manager
from app.workers.job_tasks import run_job

logger = get_logger(__name__)


# --- job_id helpers ----------------------------------------------------------


def _next_job_id(db: Session) -> str:
    """Produce a job_id like `job_YYYYMMDD_NNNN` (NNNN: per-day counter)."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"job_{today}_"
    count = (
        db.execute(select(func.count()).select_from(Job).where(Job.id.like(f"{prefix}%")))
    ).scalar_one()
    return f"{prefix}{count + 1:04d}"


# --- submit ------------------------------------------------------------------


def submit_job(
    db: Session,
    *,
    user: User,
    app: App,
    params: dict[str, Any],
    files: dict[str, tuple[str, bytes]] | None = None,
    version_id: str | None = None,
) -> Job:
    """Create a job row + storage dirs + write params.json + enqueue celery task."""
    if app.current_version_id is None and version_id is None:
        raise ConflictError("App has no published version yet")

    if version_id is not None:
        version = db.get(AppVersion, version_id)
        if version is None or version.app_id != app.id:
            raise NotFoundError("AppVersion not found")
    else:
        version = db.get(AppVersion, app.current_version_id)
        if version is None:
            raise NotFoundError("Published version missing")

    job_id = _next_job_id(db)
    storage = workspace_manager.create_job_storage(job_id)

    # Write input files
    input_paths: list[str] = []
    if files:
        for filename, (orig_name, data) in files.items():
            safe = (orig_name or filename).replace("/", "_")
            dest = workspace_manager.safe_join(storage / "input", safe)
            dest.write_bytes(data)
            input_paths.append(str(dest.relative_to(storage)))

    # Write params.json
    params_body = {
        "app_id": app.id,
        "app_version": version.version,
        "job_id": job_id,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "submitted_by": {
            "user_id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
        },
        "inputs": params or {},
    }
    (storage / "params.json").write_text(
        json.dumps(params_body, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    job = Job(
        id=job_id,
        app_id=app.id,
        app_version_id=version.id,
        executor_user_id=user.id,
        status=JobStatus.QUEUED,
        execution_target=app.execution_target.value,
        params_json=params or {},
        input_files=input_paths,
        storage_path=str(storage),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Enqueue celery task
    try:
        run_job.delay(job.id)
    except Exception:
        logger.exception("Failed to enqueue job task; job remains queued for retry")

    return job


def cancel_job(db: Session, *, user: User, job: Job) -> Job:
    if job.status not in {JobStatus.QUEUED, JobStatus.RUNNING}:
        raise ConflictError(f"Cannot cancel job in status {job.status.value}")
    # Best-effort: ask the runner to terminate
    try:
        runner = runner_for_target(job.execution_target)
        runner.cancel(job.id)
    except Exception:
        logger.exception("Runner cancel failed for job=%s", job.id)

    job.status = JobStatus.CANCELED
    job.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return job


def cleanup_job_storage(job: Job) -> None:
    """Optional helper to wipe a job's storage. Not invoked automatically."""
    path = Path(job.storage_path)
    if path.exists():
        try:
            shutil.rmtree(path)
        except Exception:
            logger.exception("Failed to remove job storage %s", path)


def get_status(db: Session, job_id: str) -> JobStatus:
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError("Job not found")
    return job.status


def ensure_run_inputs(params: dict[str, Any]) -> dict[str, Any]:
    """Validate the user-provided run payload. Right now we only ensure it is a dict."""
    if not isinstance(params, dict):
        raise ValidationError("params must be a JSON object")
    return params
