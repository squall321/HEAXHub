"""app_type enum: add 'desktop_agent'.

Only adds the enum value. The 'hwax-agent' App row seeding is split into
its own migration (0009) because ``ALTER TYPE ... ADD VALUE`` cannot be
used in the same transaction as a query that references the new value
(Postgres restriction). Alembic wraps each migration in its own
transaction, so splitting the two yields the cleanest dry-run behaviour.

Revision ID: 0008_app_type_desktop_agent
Revises: 0007_agent_refresh_tokens
Create Date: 2026-06-08
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_app_type_desktop_agent"
down_revision: Union[str, None] = "0007_agent_refresh_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE app_type ADD VALUE IF NOT EXISTS 'desktop_agent'"
    )


def downgrade() -> None:
    # Postgres has no ``ALTER TYPE DROP VALUE``; removing requires a full
    # CREATE TYPE ... RENAME / column rewrite dance. Keeping the unused
    # enum value behind is strictly safer than the alternative.
    pass
