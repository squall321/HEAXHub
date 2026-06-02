"""Job Pydantic schemas."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.db.models.job import JobStatus


class RunRequest(BaseModel):
    """JSON params for POST /apps/{app_id}/run when not using multipart.

    When multipart is used, this body lives in the `params_json` form field as JSON.
    """

    inputs: dict[str, Any] = {}
    version_id: uuid.UUID | None = None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    app_id: str
    app_version_id: uuid.UUID | None = None
    executor_user_id: uuid.UUID
    status: JobStatus
    execution_target: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_sec: int | None = None
    created_at: datetime


class JobDetailOut(JobOut):
    params_json: dict[str, Any] | None = None
    input_files: list[str] | None = None
    storage_path: str
    result_summary: dict[str, Any] | None = None
    error_message: str | None = None
