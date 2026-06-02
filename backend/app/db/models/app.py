"""App ORM model — represents a registered automation app."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AppType(str, enum.Enum):
    CLI_TOOL = "cli_tool"
    WEB_APP = "web_app"
    WINDOWS_GUI = "windows_gui"
    REMOTE_APP = "remote_app"
    EXTERNAL_LINK = "external_link"
    SLURM_JOB = "slurm_job"
    CONTAINER_APP = "container_app"


class ExecutionTarget(str, enum.Enum):
    LINUX_RUNNER = "linux_runner"
    SLURM = "slurm"
    APPTAINER = "apptainer"
    WINDOWS_WORKER = "windows_worker"
    EXTERNAL_URL = "external_url"
    LOCAL_PC = "local_pc"


class AppStatus(str, enum.Enum):
    DRAFT = "draft"
    BETA = "beta"
    STABLE = "stable"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class AppVisibility(str, enum.Enum):
    PRIVATE = "private"
    TEAM = "team"
    DEPARTMENT = "department"
    COMPANY = "company"


class App(Base):
    __tablename__ = "apps"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_versions.id", use_alter=True), nullable=True
    )
    app_type: Mapped[AppType] = mapped_column(Enum(AppType, name="app_type", values_callable=lambda x: [e.value for e in x]), nullable=False)
    execution_target: Mapped[ExecutionTarget] = mapped_column(
        Enum(ExecutionTarget, name="execution_target", values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    status: Mapped[AppStatus] = mapped_column(
        Enum(AppStatus, name="app_status", values_callable=lambda x: [e.value for e in x]), nullable=False, default=AppStatus.DRAFT
    )
    visibility: Mapped[AppVisibility] = mapped_column(
        Enum(AppVisibility, name="app_visibility", values_callable=lambda x: [e.value for e in x]), nullable=False, default=AppVisibility.TEAM
    )
    upstream_repo_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    overlay_repo_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, default=list)
    workspace_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<App {self.id} status={self.status.value}>"
