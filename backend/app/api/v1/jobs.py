"""Job endpoints."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import select

import json

from app.core.errors import ForbiddenError, NotFoundError
from app.db.models.app import App
from app.db.models.job import Job
from app.db.models.user import User, UserRole
from app.deps import CurrentUser, DbSession
from app.schemas.common import Paginated
from app.schemas.job import JobDetailOut, JobOut
from app.services import job_orchestrator, permission_service
from app.services.workspace_manager import list_files, safe_join

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _get_job_for_user(db, job_id: str, user: User) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError("Job not found")
    if user.role != UserRole.ADMIN and job.executor_user_id != user.id:
        raise ForbiddenError("Not allowed to view this job")
    return job


@router.get("", response_model=Paginated[JobOut])
def list_jobs(
    db: DbSession,
    user: CurrentUser,
    app_id: str | None = None,
    status_: str | None = None,
    mine: bool = False,
    page: int = 1,
    page_size: int = 50,
) -> Paginated[JobOut]:
    stmt = select(Job).order_by(Job.created_at.desc())
    if mine or user.role != UserRole.ADMIN:
        stmt = stmt.where(Job.executor_user_id == user.id)
    if app_id:
        stmt = stmt.where(Job.app_id == app_id)
    if status_:
        stmt = stmt.where(Job.status == status_)

    rows = list(db.execute(stmt).scalars())
    total = len(rows)
    page = max(1, page)
    page_size = max(1, min(page_size, 500))
    offset = (page - 1) * page_size
    items = [JobOut.model_validate(j) for j in rows[offset : offset + page_size]]
    return Paginated(items=items, total=total, page=page, page_size=page_size)


@router.get("/{job_id}", response_model=JobDetailOut)
def get_job(job_id: str, db: DbSession, user: CurrentUser) -> JobDetailOut:
    job = _get_job_for_user(db, job_id, user)
    return JobDetailOut.model_validate(job)


@router.get("/{job_id}/logs", response_class=PlainTextResponse)
def get_logs(job_id: str, db: DbSession, user: CurrentUser) -> PlainTextResponse:
    job = _get_job_for_user(db, job_id, user)
    log_path = Path(job.storage_path) / "logs" / "stdout.log"
    if not log_path.exists():
        return PlainTextResponse("", media_type="text/plain")
    return PlainTextResponse(log_path.read_text(encoding="utf-8", errors="replace"))


@router.get("/{job_id}/files")
def list_job_files(job_id: str, db: DbSession, user: CurrentUser) -> dict[str, object]:
    job = _get_job_for_user(db, job_id, user)
    output = Path(job.storage_path) / "output"
    return {"files": list_files(output)}


@router.get("/{job_id}/files/{path:path}")
def download_job_file(
    job_id: str, path: str, db: DbSession, user: CurrentUser
) -> FileResponse:
    job = _get_job_for_user(db, job_id, user)
    output = Path(job.storage_path) / "output"
    full = safe_join(output, path)
    if not full.exists() or not full.is_file():
        raise NotFoundError("File not found")
    return FileResponse(str(full), filename=full.name)


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel(job_id: str, db: DbSession, user: CurrentUser) -> JobOut:
    job = _get_job_for_user(db, job_id, user)
    job = job_orchestrator.cancel_job(db, user=user, job=job)
    return JobOut.model_validate(job)


@router.post("/{job_id}/rerun", response_model=JobOut, status_code=status.HTTP_201_CREATED)
def rerun(job_id: str, db: DbSession, user: CurrentUser) -> JobOut:
    """Re-run a previous job with identical params (no input files re-uploaded)."""
    original = _get_job_for_user(db, job_id, user)
    app = db.get(App, original.app_id)
    if app is None:
        raise NotFoundError("App no longer exists")
    permission_service.assert_execute(db, app, user)

    params: dict = {}
    params_path = Path(original.storage_path) / "params.json"
    if params_path.exists():
        try:
            full = json.loads(params_path.read_text(encoding="utf-8"))
            params = full.get("inputs", {}) if isinstance(full, dict) else {}
        except json.JSONDecodeError:
            params = {}

    # Copy input files from the original job into the new job
    files: dict[str, tuple[str, bytes]] = {}
    input_dir = Path(original.storage_path) / "input"
    if input_dir.exists():
        for f in input_dir.iterdir():
            if f.is_file():
                files[f.name] = (f.name, f.read_bytes())

    job = job_orchestrator.submit_job(
        db,
        user=user,
        app=app,
        params=job_orchestrator.ensure_run_inputs(params),
        files=files,
        version_id=original.app_version_id,
    )
    return JobOut.model_validate(job)
