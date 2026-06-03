"""submissions.source_config — JSONB descriptor for non-git source types.

Carries the {type, url|path|command, ...} object that the frontend submits.
Without this column, Pydantic silently dropped ``source_config`` and the
submission persisted with no record of the source descriptor.

Revision ID: 0005_submission_source_config
Revises: 0004_runtime_meta
Create Date: 2026-06-02
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_submission_source_config"
down_revision: Union[str, None] = "0004_runtime_meta"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: this column may already exist if a prior partial run
    # added it before the alembic version was stamped.
    op.execute(
        "ALTER TABLE submissions ADD COLUMN IF NOT EXISTS source_config JSONB"
    )
    # Index on source_config->>'type' for filtering by source kind.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_submissions_source_config_type "
        "ON submissions ((source_config->>'type'))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_submissions_source_config_type")
    op.execute("ALTER TABLE submissions DROP COLUMN IF EXISTS source_config")
