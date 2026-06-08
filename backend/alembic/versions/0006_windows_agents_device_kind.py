"""windows_agents.device_kind — distinguish launcher (HWAXAgent) vs service worker.

The pre-existing ``windows_agents`` rows were polling Windows Workers. The new
HWAXAgent (Tauri 2 tray launcher) reuses the same table but needs to be
distinguished so the same heartbeat endpoint can dispatch correctly. Values:
``'launcher'`` | ``'service'`` | ``NULL`` (pre-existing rows; will be backfilled
to ``'service'`` in a follow-up data migration).

Revision ID: 0006_windows_agents_device_kind
Revises: 0005_submission_source_config
Create Date: 2026-06-07
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_windows_agents_device_kind"
down_revision: Union[str, None] = "0005_submission_source_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "windows_agents",
        sa.Column("device_kind", sa.String(16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("windows_agents", "device_kind")
