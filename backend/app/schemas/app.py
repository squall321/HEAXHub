"""App / Manifest Pydantic schemas."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.app import AppStatus, AppType, AppVisibility, ExecutionTarget
from app.db.models.app_version import BuildStatus


class AppListQuery(BaseModel):
    """Catalog filter parameters."""

    q: str | None = None
    app_type: AppType | None = None
    status: AppStatus | None = None
    visibility: AppVisibility | None = None
    tag: str | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=200)


class AppOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    owner_user_id: uuid.UUID
    app_type: AppType
    execution_target: ExecutionTarget
    status: AppStatus
    visibility: AppVisibility
    tags: list[str] | None = None
    current_version_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class AppVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    app_id: str
    version: str
    git_commit_hash: str | None = None
    git_tag: str | None = None
    build_status: BuildStatus
    released_at: datetime | None = None
    created_at: datetime


class AppDetailOut(AppOut):
    upstream_repo_url: str
    overlay_repo_url: str | None = None
    workspace_path: str
    versions: list[AppVersionOut] = []
    manifest: dict[str, Any] | None = None


class ManifestLaunch(BaseModel):
    mode: Literal["job_runner", "url", "remote_agent", "local_protocol"]
    command: str | None = None
    url: str | None = None
    runtime: str | None = None
    open_in: Literal["new_tab", "iframe"] | None = None


class ManifestModel(BaseModel):
    """Minimal Python view of the manifest schema. Authoritative validation
    is JSON-Schema based (see manifest_validator service).
    """

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    id: str
    name: str
    version: str
    owner: str
    status: AppStatus
    app_type: AppType
    execution_target: ExecutionTarget
    description: str | None = None
    tags: list[str] | None = None
    launch: ManifestLaunch
    inputs: list[dict[str, Any]] | None = None
    outputs: list[dict[str, Any]] | None = None
    permissions: dict[str, Any] | None = None
    resources: dict[str, Any] | None = None
    build: dict[str, Any] | None = None
    requirements: dict[str, Any] | None = None
