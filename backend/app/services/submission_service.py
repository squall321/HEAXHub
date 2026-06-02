"""Submission CRUD + lifecycle transitions."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.db.models.app import App
from app.db.models.submission import Submission, SubmissionStatus
from app.db.models.user import User, UserRole
from app.schemas.submission import SubmissionCreate


# --- validators --------------------------------------------------------------


def _check_git_url(url: str) -> None:
    settings = get_settings()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https", "git", "ssh"}:
        raise ValidationError(f"Unsupported git URL scheme '{parsed.scheme}'")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValidationError("Git URL has no hostname")
    allowed = settings.allowed_git_host_list
    if not any(host == h or host.endswith("." + h) for h in allowed):
        raise ValidationError(
            f"Git host '{host}' is not in the allowed list",
            details={"allowed": allowed},
        )


# --- CRUD --------------------------------------------------------------------


def create_submission(db: Session, *, user: User, payload: SubmissionCreate) -> Submission:
    _check_git_url(str(payload.upstream_repo_url))

    existing_app = db.get(App, payload.proposed_app_id)
    if existing_app is not None:
        raise ConflictError(f"App id '{payload.proposed_app_id}' already exists")

    dup = db.execute(
        select(Submission).where(
            Submission.proposed_app_id == payload.proposed_app_id,
            Submission.status.in_(
                [
                    SubmissionStatus.PENDING,
                    SubmissionStatus.UNDER_REVIEW,
                    SubmissionStatus.APPROVED,
                    SubmissionStatus.PROVISIONING,
                    SubmissionStatus.BUILDING,
                ]
            ),
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise ConflictError("An active submission for this app_id already exists")

    sub = Submission(
        submitter_user_id=user.id,
        proposed_app_id=payload.proposed_app_id,
        name=payload.name,
        description=payload.description,
        upstream_repo_url=str(payload.upstream_repo_url),
        proposed_app_type=payload.proposed_app_type,
        proposed_execution_target=payload.proposed_execution_target,
        proposed_manifest=payload.proposed_manifest,
        status=SubmissionStatus.PENDING,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def list_submissions(
    db: Session,
    *,
    user: User,
    status: SubmissionStatus | None = None,
    mine: bool = False,
) -> list[Submission]:
    stmt = select(Submission).order_by(Submission.created_at.desc())
    if mine or user.role != UserRole.ADMIN:
        stmt = stmt.where(Submission.submitter_user_id == user.id)
    if status is not None:
        stmt = stmt.where(Submission.status == status)
    return list(db.execute(stmt).scalars())


def get_submission(db: Session, *, user: User, submission_id: uuid.UUID) -> Submission:
    sub = db.get(Submission, submission_id)
    if sub is None:
        raise NotFoundError("Submission not found")
    if user.role != UserRole.ADMIN and sub.submitter_user_id != user.id:
        raise ForbiddenError("Not allowed to view this submission")
    return sub


def review_submission(
    db: Session,
    *,
    reviewer: User,
    submission_id: uuid.UUID,
    new_status: SubmissionStatus,
    notes: str | None,
) -> Submission:
    if reviewer.role != UserRole.ADMIN:
        raise ForbiddenError("Only admins can review submissions")

    sub = db.get(Submission, submission_id)
    if sub is None:
        raise NotFoundError("Submission not found")

    valid_transitions = {
        SubmissionStatus.PENDING,
        SubmissionStatus.UNDER_REVIEW,
        SubmissionStatus.MANIFEST_REQUIRED,
        SubmissionStatus.APPROVED,
        SubmissionStatus.REJECTED,
    }
    if new_status not in valid_transitions:
        raise ValidationError(f"Cannot transition to status '{new_status.value}' via review")

    sub.status = new_status
    sub.review_notes = notes
    sub.reviewer_user_id = reviewer.id
    sub.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(sub)
    return sub
