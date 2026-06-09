"""Seed the 'hwax-agent' App row (depends on the enum value from 0008).

Inserts a single ``apps`` row that represents the HWAXAgent launcher itself.
The existing installer infrastructure (``installer_packages`` table +
``/api/v1/apps/{app_id}/installers`` upload route +
``/api/v1/installers/{app_id}/latest`` updater feed) then works for the
launcher with zero new endpoints — the portal-side download page just
reads the latest installer for ``app_id='hwax-agent'``.

This is its own migration (not merged with 0008) because Postgres won't let
us reference a newly added enum value in the same transaction as the
``ALTER TYPE ... ADD VALUE`` statement.

Revision ID: 0009_seed_hwax_agent_app
Revises: 0008_app_type_desktop_agent
Create Date: 2026-06-08
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_seed_hwax_agent_app"
down_revision: Union[str, None] = "0008_app_type_desktop_agent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_APP_ID = "hwax-agent"


def upgrade() -> None:
    bind = op.get_bind()

    # Pick an owner — first admin if available, else first user.
    # If the DB has no users at all yet (fresh install), skip; the
    # ``app.services.bootstrap`` startup hook (or the seed_admin command)
    # is responsible for inserting the launcher row in that case.
    owner_id = bind.execute(
        sa.text(
            "SELECT id FROM users WHERE role = 'admin' "
            "ORDER BY created_at ASC LIMIT 1"
        )
    ).scalar()
    if owner_id is None:
        owner_id = bind.execute(
            sa.text("SELECT id FROM users ORDER BY created_at ASC LIMIT 1")
        ).scalar()
    if owner_id is None:
        return

    # Skip if already present (idempotent re-runs).
    existing = bind.execute(
        sa.text("SELECT id FROM apps WHERE id = :id").bindparams(id=_APP_ID)
    ).scalar()
    if existing is not None:
        return

    bind.execute(
        sa.text(
            """
            INSERT INTO apps (
                id, name, description, owner_user_id,
                app_type, execution_target, status, visibility,
                upstream_repo_url, workspace_path, tags, extra,
                created_at, updated_at
            ) VALUES (
                :id, :name, :description, :owner_id,
                'desktop_agent', 'local_pc', 'stable', 'company',
                :upstream_repo_url, :workspace_path, :tags,
                :extra,
                now(), now()
            )
            """
        ),
        {
            "id": _APP_ID,
            "name": "HWAX Agent (Windows tray launcher)",
            "description": (
                "Tauri 2 desktop launcher that pairs with this hub and "
                "installs/manages Windows GUI apps from the catalog. "
                "Distributed from the portal /heax-hub/download page."
            ),
            "owner_id": owner_id,
            "upstream_repo_url": "https://github.com/squall321/HWAXLauncher",
            "workspace_path": "/dev/null",  # unused — desktop_agent has no SIF
            "tags": "[]",
            "extra": '{"hwax_agent": true}',
        },
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM apps WHERE id = :id").bindparams(id=_APP_ID)
    )
