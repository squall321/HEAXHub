"""Orchestrates submission → provisioning → build → publish.

Heavy work (clone, build) is delegated to Celery tasks. This module produces
DB rows and enqueues tasks.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logger import get_logger
from app.db.models.app import App, AppStatus, AppType, AppVisibility, ExecutionTarget
from app.db.models.app_version import AppVersion, BuildStatus
from app.db.models.submission import Submission, SubmissionStatus
from app.db.models.user import User, UserRole
from app.services import audit_service, workspace_manager

logger = get_logger(__name__)


def _coerce_enum(value: str | None, enum_cls: type, default: object) -> object:
    if value is None:
        return default
    try:
        return enum_cls(value)
    except ValueError as exc:
        raise ValidationError(f"Invalid {enum_cls.__name__} value '{value}'") from exc


def approve_and_provision(db: Session, *, reviewer: User, submission_id: uuid.UUID) -> Submission:
    """Mark submission approved and enqueue provisioning."""
    if reviewer.role != UserRole.ADMIN:
        raise ValidationError("Only admins can approve submissions")

    sub = db.get(Submission, submission_id)
    if sub is None:
        raise NotFoundError("Submission not found")
    if sub.status not in {
        SubmissionStatus.PENDING,
        SubmissionStatus.UNDER_REVIEW,
        SubmissionStatus.MANIFEST_REQUIRED,
    }:
        raise ConflictError(f"Cannot approve submission in status {sub.status.value}")

    if db.get(App, sub.proposed_app_id) is not None:
        raise ConflictError(f"App {sub.proposed_app_id} already exists")

    sub.status = SubmissionStatus.APPROVED
    sub.reviewer_user_id = reviewer.id
    sub.reviewed_at = datetime.now(timezone.utc)
    db.commit()

    # Defer the actual clone+build to celery. Lazy import: app.workers.sync_tasks
    # imports this module at top-level for orchestration, so hoisting would cycle.
    from app.workers.sync_tasks import clone_upstream  # noqa: PLC0415

    clone_upstream.delay(str(sub.id))
    return sub


def provision_workspace(db: Session, *, submission_id: uuid.UUID) -> tuple[App, AppVersion]:
    """Synchronous provisioning step run inside a Celery worker.

    1. Create the workspace directories.
    2. Insert App + AppVersion rows.
    3. Returns objects for the caller to enqueue the build.
    """
    sub = db.get(Submission, submission_id)
    if sub is None:
        raise NotFoundError("Submission not found")

    workspace = workspace_manager.create_app_workspace(sub.proposed_app_id)

    # Resolve manifest-implied values (best-effort).
    manifest = sub.proposed_manifest or {}
    app_type = _coerce_enum(
        manifest.get("app_type") or sub.proposed_app_type,
        AppType,
        AppType.CLI_TOOL,
    )
    execution_target = _coerce_enum(
        manifest.get("execution_target") or sub.proposed_execution_target,
        ExecutionTarget,
        ExecutionTarget.LINUX_RUNNER,
    )

    app = App(
        id=sub.proposed_app_id,
        name=sub.name,
        description=sub.description,
        owner_user_id=sub.submitter_user_id,
        app_type=app_type,  # type: ignore[arg-type]
        execution_target=execution_target,  # type: ignore[arg-type]
        status=AppStatus.DRAFT,
        visibility=AppVisibility.TEAM,
        upstream_repo_url=sub.upstream_repo_url,
        tags=manifest.get("tags") or [],
        workspace_path=str(workspace),
    )
    db.add(app)

    version = AppVersion(
        app_id=app.id,
        version=manifest.get("version", "0.1.0"),
        manifest_snapshot=manifest or None,
        build_status=BuildStatus.PENDING,
    )
    db.add(version)
    db.flush()

    sub.status = SubmissionStatus.PROVISIONING
    db.commit()
    db.refresh(app)
    db.refresh(version)
    return app, version


def _latest_successful_version_id(db: Session, *, app_id: str) -> uuid.UUID | None:
    """Most recent AppVersion for ``app_id`` whose build succeeded, or None.

    Used by publish to resolve a version when ``App.current_version_id`` was
    never set (the build pipeline records build_status but doesn't promote
    current_version_id).
    """
    return db.execute(
        select(AppVersion.id)
        .where(AppVersion.app_id == app_id)
        .where(AppVersion.build_status == BuildStatus.SUCCESS)
        .order_by(AppVersion.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def publish_app(
    db: Session, *, app_id: str, version_id: uuid.UUID, actor: User | None = None
) -> App:
    app = db.get(App, app_id)
    if app is None:
        raise NotFoundError("App not found")
    version = db.get(AppVersion, version_id)
    if version is None:
        raise NotFoundError("AppVersion not found")
    if version.app_id != app_id:
        raise ValidationError("Version does not belong to this app")
    if version.build_status != BuildStatus.SUCCESS:
        raise ConflictError("Cannot publish a version that has not built successfully")

    app.current_version_id = version.id
    if app.status == AppStatus.DRAFT:
        app.status = AppStatus.STABLE
    version.released_at = datetime.now(timezone.utc)
    if actor:
        version.released_by = actor.id
    db.commit()
    db.refresh(app)
    return app


def retry_submission(
    db: Session, *, reviewer: User, submission_id: uuid.UUID
) -> Submission:
    """Re-run clone+build for a FAILED submission without manual SQL.

    Failure modes that strand a submission:
      - clone failed early → no App row, just status=FAILED.
      - build failed after provisioning → an App/AppVersion row exists, which
        makes ``approve_and_provision`` reject the same app_id forever ("App
        already exists") and blocks any fresh re-submission of that id.

    This clears the orphaned App/AppVersion rows (if any) and re-enqueues
    ``clone_upstream`` from the APPROVED state, so the operator never has to
    DELETE rows by hand.
    """
    if reviewer.role != UserRole.ADMIN:
        raise ValidationError("Only admins can retry submissions")

    sub = db.get(Submission, submission_id)
    if sub is None:
        raise NotFoundError("Submission not found")
    if sub.status not in {SubmissionStatus.FAILED, SubmissionStatus.MANIFEST_REQUIRED}:
        raise ConflictError(
            f"Cannot retry submission in status '{sub.status.value}' "
            "(only failed / manifest_required can be retried)."
        )

    # Clear orphaned rows so the clone+provision path starts clean.
    app = db.get(App, sub.proposed_app_id)
    if app is not None:
        app.current_version_id = None
        db.flush()
        db.execute(
            AppVersion.__table__.delete().where(
                AppVersion.app_id == sub.proposed_app_id
            )
        )
        db.delete(app)

    sub.status = SubmissionStatus.APPROVED
    sub.reviewer_user_id = reviewer.id
    sub.reviewed_at = datetime.now(timezone.utc)
    sub.review_notes = (sub.review_notes or "") + "\n[retry] re-enqueued by operator"
    db.commit()

    audit_service.safe_log(
        db,
        actor_user_id=reviewer.id,
        action="submission.retried",
        target_type="submission",
        target_id=str(sub.id),
        meta={"app_id": sub.proposed_app_id},
    )

    from app.workers.sync_tasks import clone_upstream  # noqa: PLC0415

    clone_upstream.delay(str(sub.id))
    return sub


def publish_submission(
    db: Session, *, reviewer: User, submission_id: uuid.UUID
) -> Submission:
    """Flip a BUILT submission to PUBLISHED and promote its App to STABLE.

    The reviewer triggers this from the admin UI once the build pipeline has
    finished and the test-run looks good. publish_app handles App / AppVersion
    side; this function additionally:
      - sets Submission.status = PUBLISHED
      - sets Submission.published_at = now
      - writes audit log
    """
    sub = db.get(Submission, submission_id)
    if sub is None:
        raise NotFoundError("Submission not found")
    if sub.status != SubmissionStatus.BUILT:
        raise ConflictError(
            f"Submission status is '{sub.status.value}', expected 'built' to publish."
        )
    app = db.get(App, sub.proposed_app_id)
    if app is None:
        raise NotFoundError(f"App {sub.proposed_app_id} not found")

    # current_version_id is set by publish_app, but the build pipeline doesn't
    # set it — so a freshly-built submission would deadlock here. Resolve the
    # latest successfully-built AppVersion automatically.
    version_id = app.current_version_id
    if version_id is None:
        version_id = _latest_successful_version_id(db, app_id=app.id)
    if version_id is None:
        raise ConflictError(
            "App has no successfully-built AppVersion to publish "
            "(build may still be running or have failed)."
        )

    publish_app(db, app_id=app.id, version_id=version_id, actor=reviewer)

    sub.status = SubmissionStatus.PUBLISHED
    sub.published_at = datetime.now(timezone.utc)
    sub.reviewer_user_id = reviewer.id
    sub.reviewed_at = datetime.now(timezone.utc)

    audit_service.safe_log(
        db,
        actor_user_id=reviewer.id,
        action="submission.published",
        target_type="submission",
        target_id=str(sub.id),
        meta={"app_id": sub.proposed_app_id},
    )

    db.commit()
    db.refresh(sub)
    return sub
