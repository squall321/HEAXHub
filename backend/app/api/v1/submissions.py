"""Submission endpoints."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.models.app import App
from app.db.models.submission import SubmissionStatus
from app.deps import AdminUser, CurrentUser, DbSession
from app.schemas.common import Paginated
from app.schemas.submission import SubmissionCreate, SubmissionOut, SubmissionPatch
from app.services import app_lifecycle, job_orchestrator, submission_service

router = APIRouter(prefix="/submissions", tags=["submissions"])


@router.post("", response_model=SubmissionOut, status_code=status.HTTP_201_CREATED)
def create_submission(
    payload: SubmissionCreate, db: DbSession, user: CurrentUser
) -> SubmissionOut:
    sub = submission_service.create_submission(db, user=user, payload=payload)
    return SubmissionOut.model_validate(sub)


@router.get("", response_model=Paginated[SubmissionOut])
def list_submissions(
    db: DbSession,
    user: CurrentUser,
    status_: SubmissionStatus | None = None,
    mine: bool = False,
    page: int = 1,
    page_size: int = 50,
) -> Paginated[SubmissionOut]:
    rows = submission_service.list_submissions(db, user=user, status=status_, mine=mine)
    total = len(rows)
    page = max(1, page)
    page_size = max(1, min(page_size, 500))
    offset = (page - 1) * page_size
    items = [SubmissionOut.model_validate(r) for r in rows[offset : offset + page_size]]
    return Paginated(items=items, total=total, page=page, page_size=page_size)


@router.get("/{submission_id}", response_model=SubmissionOut)
def get_submission(
    submission_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> SubmissionOut:
    sub = submission_service.get_submission(db, user=user, submission_id=submission_id)
    return SubmissionOut.model_validate(sub)


@router.patch("/{submission_id}", response_model=SubmissionOut)
def review_submission(
    submission_id: uuid.UUID,
    payload: SubmissionPatch,
    db: DbSession,
    reviewer: AdminUser,
) -> SubmissionOut:
    if payload.status is None:
        raise ValidationError("status is required")

    if payload.status == SubmissionStatus.APPROVED:
        sub = app_lifecycle.approve_and_provision(
            db, reviewer=reviewer, submission_id=submission_id
        )
        if payload.review_notes:
            sub.review_notes = payload.review_notes
            db.commit()
            db.refresh(sub)
    else:
        sub = submission_service.review_submission(
            db,
            reviewer=reviewer,
            submission_id=submission_id,
            new_status=payload.status,
            notes=payload.review_notes,
        )
    return SubmissionOut.model_validate(sub)


@router.post("/{submission_id}/test-run", response_model=SubmissionOut)
def test_run_submission(
    submission_id: uuid.UUID,
    db: DbSession,
    reviewer: AdminUser,
) -> SubmissionOut:
    """Run the built app with empty params/files as a sanity check. Stores the
    test job id on the submission for traceability."""
    sub = submission_service.get_submission(db, user=reviewer, submission_id=submission_id)

    if sub.status not in {SubmissionStatus.BUILT, SubmissionStatus.PUBLISHED}:
        raise ConflictError(
            "test-run requires the submission to be built (status=built or published). "
            "Approve first and wait for the build pipeline to finish."
        )

    app = db.get(App, sub.proposed_app_id)
    if app is None:
        raise NotFoundError("Built app not found")
    if app.current_version_id is None:
        raise ConflictError("App has no published version yet")

    job = job_orchestrator.submit_job(
        db,
        user=reviewer,
        app=app,
        params=job_orchestrator.ensure_run_inputs({}),
        files=None,
    )
    sub.test_job_id = job.id
    db.commit()
    db.refresh(sub)
    return SubmissionOut.model_validate(sub)
