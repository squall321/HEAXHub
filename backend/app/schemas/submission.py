"""Submission Pydantic schemas."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.db.models.app import AppType, ExecutionTarget
from app.db.models.submission import SubmissionStatus


class SubmissionCreate(BaseModel):
    # Accept both legacy ``proposed_app_type`` and the frontend's ``app_type``.
    # ``populate_by_name=True`` lets both the field name and any alias work.
    model_config = ConfigDict(populate_by_name=True)

    proposed_app_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    # Loosened from HttpUrl to str — non-git sources (archive_url, local_path,
    # system_command) may not be valid HTTP URLs. Scheme/host validation lives
    # in submission_service.create_submission, gated by source_config.type.
    upstream_repo_url: str | None = None
    proposed_app_type: AppType | None = Field(
        default=None,
        validation_alias=AliasChoices("proposed_app_type", "app_type"),
    )
    proposed_execution_target: ExecutionTarget | None = Field(
        default=None,
        validation_alias=AliasChoices("proposed_execution_target", "execution_target"),
    )
    proposed_manifest: dict[str, Any] | None = None
    source_config: dict[str, Any] | None = None


class SubmissionPatch(BaseModel):
    status: SubmissionStatus | None = None
    review_notes: str | None = None


class SubmissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    submitter_user_id: uuid.UUID
    proposed_app_id: str
    name: str
    description: str | None = None
    upstream_repo_url: str
    proposed_app_type: str | None = None
    proposed_execution_target: str | None = None
    source_config: dict[str, Any] | None = None
    status: SubmissionStatus
    review_notes: str | None = None
    reviewer_user_id: uuid.UUID | None = None
    created_at: datetime
    reviewed_at: datetime | None = None
    published_at: datetime | None = None
    test_job_id: str | None = None
