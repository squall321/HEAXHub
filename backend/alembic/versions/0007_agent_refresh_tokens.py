"""agent_refresh_tokens — sibling table for HWAXAgent refresh tokens.

The existing ``refresh_tokens`` table FKs ``users.id``. HWAXAgent (Tauri 2
tray launcher) issues refresh tokens whose subject is a ``WindowsAgent.id``
instead, so we keep them in a dedicated table to avoid breaking the user
auth flow and to allow independent rotation policies.

Revision ID: 0007_agent_refresh_tokens
Revises: 0006_windows_agents_device_kind
Create Date: 2026-06-07
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_agent_refresh_tokens"
down_revision: Union[str, None] = "0006_windows_agents_device_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_refresh_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("windows_agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("jti", sa.String(64), nullable=False),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_jti", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_agent_refresh_tokens_agent_id",
        "agent_refresh_tokens",
        ["agent_id"],
    )
    op.create_index(
        "ix_agent_refresh_tokens_jti",
        "agent_refresh_tokens",
        ["jti"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_agent_refresh_tokens_jti", table_name="agent_refresh_tokens")
    op.drop_index("ix_agent_refresh_tokens_agent_id", table_name="agent_refresh_tokens")
    op.drop_table("agent_refresh_tokens")
