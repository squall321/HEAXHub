"""installer_packages.package_format — real installer format for the manifest.

The launcher manifest's ``package.type`` was inferred from ``installer_url``,
which for disk-stored installers has no file extension, so every package
defaulted to ``"exe"``. Store the real format (captured from the uploaded
filename at upload time) so the manifest reports it honestly. Nullable: legacy
rows stay NULL and the builder falls back to URL inference for them.

Revision ID: 0008_installer_packages_format
Revises: 0007_agent_refresh_tokens
Create Date: 2026-06-09
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_installer_packages_format"
down_revision: Union[str, None] = "0007_agent_refresh_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "installer_packages",
        sa.Column("package_format", sa.String(8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("installer_packages", "package_format")
