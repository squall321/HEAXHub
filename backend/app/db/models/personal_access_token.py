# 개인 액세스 토큰(PAT) ORM — 헤드리스 클라이언트(MCP·CI)용 장수명 자격증명, 해시만 저장.
"""PersonalAccessToken ORM model.

Long-lived, revocable credentials for headless clients (MCP clients, CI,
scripts) that cannot ride the browser SSO cookie. Only the SHA-256 hash of
the token is persisted — the plaintext is shown exactly once at issuance.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PersonalAccessToken(Base):
    __tablename__ = "personal_access_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # 표시·식별용 앞부분 (예: "heax_pat_Ab3d") — 목록에서 어떤 토큰인지 구분하는 용도.
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    # SHA-256 hex — 평문은 저장하지 않는다.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # None 이면 무기한 (폐기로만 무효화).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_active(self) -> bool:
        now = datetime.now(timezone.utc)
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > now
