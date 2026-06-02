"""Permission ORM model — per-app ACL."""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PrincipalType(str, enum.Enum):
    USER = "user"
    GROUP = "group"
    ROLE = "role"


class PermissionLevel(str, enum.Enum):
    VIEW = "view"
    EXECUTE = "execute"
    MANAGE = "manage"


class Permission(Base):
    __tablename__ = "permissions"
    __table_args__ = (
        UniqueConstraint(
            "app_id", "principal_type", "principal_id", "permission", name="uq_permission_grant"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    app_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("apps.id", ondelete="CASCADE"), nullable=False, index=True
    )
    principal_type: Mapped[PrincipalType] = mapped_column(
        Enum(PrincipalType, name="principal_type", values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    principal_id: Mapped[str] = mapped_column(String(255), nullable=False)
    permission: Mapped[PermissionLevel] = mapped_column(
        Enum(PermissionLevel, name="permission_level", values_callable=lambda x: [e.value for e in x]), nullable=False
    )
