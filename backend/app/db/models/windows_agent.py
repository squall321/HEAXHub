"""WindowsAgent ORM model — registered Windows Worker Agent that polls the hub for jobs."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WindowsAgent(Base):
    __tablename__ = "windows_agents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    pool: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hostname: Mapped[str | None] = mapped_column(String(256), nullable=True)
    agent_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # status: unknown | online | busy | offline
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", index=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auth_token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    capabilities: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WindowsAgent {self.name} pool={self.pool} status={self.status}>"
