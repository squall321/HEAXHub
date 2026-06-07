"""windows_agents.device_kind — distinguish launcher (HWAXAgent) from service agents.

The HWAXAgent Windows launcher is registered as a WindowsAgent with
device_kind='launcher'; the existing polling Windows Worker agents are 'service'
(or NULL for rows created before this column). Existing rows are left NULL — no
backfill here (NEXT_STEPS §2.1; backfill to 'service' is a later, separate step).

Revision ID: 0006_windows_agents_device_kind
Revises: 0005_submission_source_config
Create Date: 2026-06-08
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0006_windows_agents_device_kind"
down_revision: Union[str, None] = "0005_submission_source_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent (matches the 0005 style): the column may already exist from a
    # partial run before the alembic version was stamped. Cardinality <= 3 so no
    # index is warranted.
    op.execute(
        "ALTER TABLE windows_agents ADD COLUMN IF NOT EXISTS device_kind VARCHAR(16)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE windows_agents DROP COLUMN IF EXISTS device_kind")
