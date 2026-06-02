"""Orchestrates submission → provisioning → build → publish.

Heavy work (clone, build) is delegated to Celery tasks. This module produces
DB rows and enqueues tasks.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logger import get_logger
from app.db.models.app import App, AppStatus, AppType, AppVisibility, ExecutionTarget
from app.db.models.app_version import AppVersion, BuildStatus
from app.db.models.submission import Submission, SubmissionStatus
from app.db.models.user import User, UserRole
from app.services import workspace_manager

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
