"""LicensePool ORM model — FlexLM/RLM feature token pool definition."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LicensePool(Base):
    __tablename__ = "license_pools"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    feature: Mapped[str | None] = mapped_column(String(128), nullable=True)
    server: Mapped[str | None] = mapped_column(String(256), nullable=True)
    check_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<LicensePool {self.name} total={self.total_tokens}>"
