"""ChangeRequest ORM model — AI-generated portal registration proposals.

Tracks the full lifecycle of a Stage 3 change-request artifact: static-analysis
facts, raw LLM response, operator overrides, the rendered Markdown body, and
any PR/issue URL produced when the request is published to a GitHub repo.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChangeRequest(Base):
    __tablename__ = "change_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    submission_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("submissions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    app_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("apps.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    repo_url: Mapped[str] = mapped_column(Text, nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)

    static_facts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    llm_response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    operator_overrides: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    final_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    markdown_body: Mapped[str] = mapped_column(Text, nullable=False)
    pr_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft", index=True)
    pr_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    issue_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
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
        return f"<ChangeRequest {self.id} {self.status} {self.repo_url}>"
