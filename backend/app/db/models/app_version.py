"""AppVersion ORM model."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BuildStatus(str, enum.Enum):
    PENDING = "pending"
    BUILDING = "building"
    SUCCESS = "success"
    FAILED = "failed"


class AppVersion(Base):
    __tablename__ = "app_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    app_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("apps.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    git_commit_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    git_tag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    manifest_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    build_status: Mapped[BuildStatus] = mapped_column(
        Enum(BuildStatus, name="build_status", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=BuildStatus.PENDING,
    )
    build_log_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    sif_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    venv_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AppVersion {self.app_id}@{self.version} {self.build_status.value}>"
