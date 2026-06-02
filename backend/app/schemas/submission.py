"""Submission Pydantic schemas."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.db.models.submission import SubmissionStatus


class SubmissionCreate(BaseModel):
    proposed_app_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    upstream_repo_url: HttpUrl
    proposed_app_type: str | None = None
    proposed_execution_target: str | None = None
    proposed_manifest: dict[str, Any] | None = None


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
    status: SubmissionStatus
    review_notes: str | None = None
    reviewer_user_id: uuid.UUID | None = None
    created_at: datetime
    reviewed_at: datetime | None = None
    published_at: datetime | None = None
    test_job_id: str | None = None
