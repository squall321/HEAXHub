"""PortAllocation ORM model — tracks reverse-proxy port assignments for apps and jobs."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PortAllocation(Base):
    __tablename__ = "port_allocations"

    port: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("apps.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    job_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="app")
    allocated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        state = "released" if self.released_at else "active"
        return f"<PortAllocation {self.port} {self.scope} {state}>"
