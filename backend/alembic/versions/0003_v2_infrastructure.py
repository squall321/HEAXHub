"""v2 infrastructure: ports, secrets, licenses, GPUs, services, agents, installers, change_requests, source_config.

Revision ID: 0003_v2_infrastructure
Revises: 0002_favorites_and_tokens
Create Date: 2026-05-28
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_v2_infrastructure"
down_revision: Union[str, None] = "0002_favorites_and_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── apps/submissions: source_config + relax upstream_repo_url ──────────
    op.add_column(
        "apps",
        sa.Column("source_config", postgresql.JSONB, nullable=True),
    )
    op.add_column(
        "submissions",
        sa.Column("source_config", postgresql.JSONB, nullable=True),
    )
    op.alter_column("submissions", "upstream_repo_url", existing_type=sa.String(1024), nullable=True)

    # ── port_allocations ──────────────────────────────────────────────────
    op.create_table(
        "port_allocations",
        sa.Column("port", sa.Integer, primary_key=True),
        sa.Column("app_id", sa.String(64), sa.ForeignKey("apps.id", ondelete="SET NULL"), nullable=True),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("scope", sa.String(16), nullable=False, server_default="app"),
        sa.Column("allocated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_port_allocations_released", "port_allocations", ["released_at"])
    op.create_index("ix_port_allocations_app_id", "port_allocations", ["app_id"])

    # ── secret_values ─────────────────────────────────────────────────────
    op.create_table(
        "secret_values",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("scope", sa.String(128), nullable=False, server_default="global"),  # global | app:{id} | user:{uuid}
        sa.Column("value_encrypted", sa.LargeBinary, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("key", "scope", name="uq_secret_values_key_scope"),
    )
    op.create_index("ix_secret_values_scope", "secret_values", ["scope"])

    # ── license_pools / license_holdings ──────────────────────────────────
    op.create_table(
        "license_pools",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("total_tokens", sa.Integer, nullable=False),
        sa.Column("feature", sa.String(128), nullable=True),
        sa.Column("server", sa.String(256), nullable=True),
        sa.Column("check_command", sa.Text, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "license_holdings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("pool_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("license_pools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tokens", sa.Integer, nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_license_holdings_active", "license_holdings", ["pool_id", "released_at"])

    # ── gpu_devices / gpu_holdings ────────────────────────────────────────
    op.create_table(
        "gpu_devices",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("host", sa.String(128), nullable=False),
        sa.Column("device_index", sa.Integer, nullable=False),  # /dev/nvidiaN
        sa.Column("uuid", sa.String(64), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("cuda_capability", sa.String(8), nullable=True),
        sa.Column("memory_mb", sa.Integer, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="free"),
        sa.UniqueConstraint("host", "device_index", name="uq_gpu_devices_host_idx"),
    )
    op.create_table(
        "gpu_holdings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("device_id", sa.Integer, sa.ForeignKey("gpu_devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_gpu_holdings_active", "gpu_holdings", ["device_id", "released_at"])

    # ── service_instances (장기 데몬) ──────────────────────────────────────
    op.create_table(
        "service_instances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("app_id", sa.String(64), sa.ForeignKey("apps.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("app_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("pid", sa.Integer, nullable=True),
        sa.Column("port", sa.Integer, nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="starting"),  # starting/healthy/unhealthy/stopped
        sa.Column("workdir", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_health", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restart_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_service_instances_status", "service_instances", ["status"])
    op.create_index("ix_service_instances_app_id", "service_instances", ["app_id"])

    # ── windows_agents ────────────────────────────────────────────────────
    op.create_table(
        "windows_agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("pool", sa.String(64), nullable=False),
        sa.Column("hostname", sa.String(256), nullable=True),
        sa.Column("agent_version", sa.String(32), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("auth_token_hash", sa.String(128), nullable=False),
        sa.Column("capabilities", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("disabled", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_windows_agents_pool", "windows_agents", ["pool"])
    op.create_index("ix_windows_agents_status", "windows_agents", ["status"])

    # ── installer_packages ────────────────────────────────────────────────
    op.create_table(
        "installer_packages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("app_id", sa.String(64), sa.ForeignKey("apps.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("os", sa.String(32), nullable=False),  # windows-x64, macos-arm64, linux-x64
        sa.Column("installer_url", sa.Text, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=True),
        sa.Column("signed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("app_id", "version", "os", name="uq_installer_packages_av_os"),
    )

    # ── change_requests ───────────────────────────────────────────────────
    op.create_table(
        "change_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("submissions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("app_id", sa.String(64), sa.ForeignKey("apps.id", ondelete="SET NULL"), nullable=True),
        sa.Column("repo_url", sa.Text, nullable=False),
        sa.Column("commit_sha", sa.String(40), nullable=True),
        sa.Column("static_facts", postgresql.JSONB, nullable=False),
        sa.Column("llm_response", postgresql.JSONB, nullable=False),
        sa.Column("operator_overrides", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("final_manifest", postgresql.JSONB, nullable=False),
        sa.Column("markdown_body", sa.Text, nullable=False),
        sa.Column("pr_payload", postgresql.JSONB, nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("pr_url", sa.Text, nullable=True),
        sa.Column("issue_url", sa.Text, nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_change_requests_status", "change_requests", ["status"])
    op.create_index("ix_change_requests_submission", "change_requests", ["submission_id"])
    op.create_index("ix_change_requests_app_id", "change_requests", ["app_id"])


def downgrade() -> None:
    op.drop_index("ix_change_requests_app_id", table_name="change_requests")
    op.drop_index("ix_change_requests_submission", table_name="change_requests")
    op.drop_index("ix_change_requests_status", table_name="change_requests")
    op.drop_table("change_requests")

    op.drop_table("installer_packages")

    op.drop_index("ix_windows_agents_status", table_name="windows_agents")
    op.drop_index("ix_windows_agents_pool", table_name="windows_agents")
    op.drop_table("windows_agents")

    op.drop_index("ix_service_instances_app_id", table_name="service_instances")
    op.drop_index("ix_service_instances_status", table_name="service_instances")
    op.drop_table("service_instances")

    op.drop_index("ix_gpu_holdings_active", table_name="gpu_holdings")
    op.drop_table("gpu_holdings")
    op.drop_table("gpu_devices")

    op.drop_index("ix_license_holdings_active", table_name="license_holdings")
    op.drop_table("license_holdings")
    op.drop_table("license_pools")

    op.drop_index("ix_secret_values_scope", table_name="secret_values")
    op.drop_table("secret_values")

    op.drop_index("ix_port_allocations_app_id", table_name="port_allocations")
    op.drop_index("ix_port_allocations_released", table_name="port_allocations")
    op.drop_table("port_allocations")

    op.alter_column("submissions", "upstream_repo_url", existing_type=sa.String(1024), nullable=False)
    op.drop_column("submissions", "source_config")
    op.drop_column("apps", "source_config")
