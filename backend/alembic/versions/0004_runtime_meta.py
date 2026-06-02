"""jobs.runtime_meta — opaque scheduler handles (e.g. slurm_job_id).

Revision ID: 0004_runtime_meta
Revises: 0003_v2_infrastructure
Create Date: 2026-05-29
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_runtime_meta"
down_revision: Union[str, None] = "0003_v2_infrastructure"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("runtime_meta", postgresql.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jobs", "runtime_meta")
