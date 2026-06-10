"""install_reports — per-attempt launcher install outcomes (NEXT_STEPS §3.1).

Persists each report POSTed to /api/v1/launcher-agents/installs so operators can
see per-agent install health. Distinct from audit_log (which records event
classes); status here is the terminal outcome of one install attempt.

Revision ID: 0009_install_reports
Revises: 0008_installer_packages_format
Create Date: 2026-06-09
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_install_reports"
down_revision: Union[str, None] = "0008_installer_packages_format"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "install_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("windows_agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("app_id", sa.String(64), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sha256_verified", sa.Boolean(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("log_excerpt", sa.Text(), nullable=True),
        sa.Column("previous_version", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_install_reports_agent_id", "install_reports", ["agent_id"])
    op.create_index("ix_install_reports_app_id", "install_reports", ["app_id"])
    op.create_index("ix_install_reports_created_at", "install_reports", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_install_reports_created_at", table_name="install_reports")
    op.drop_index("ix_install_reports_app_id", table_name="install_reports")
    op.drop_index("ix_install_reports_agent_id", table_name="install_reports")
    op.drop_table("install_reports")
