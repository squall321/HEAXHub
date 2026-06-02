"""Initial schema: users, apps, app_versions, submissions, jobs, permissions, audit_log.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-26

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Enum names (Postgres types)
AUTH_SOURCE = sa.Enum("local", "sso", name="auth_source")
USER_STATUS = sa.Enum("pending_verification", "active", "disabled", name="user_status")
USER_ROLE = sa.Enum("admin", "owner", "user", "viewer", name="user_role")
APP_TYPE = sa.Enum(
    "cli_tool",
    "web_app",
    "windows_gui",
    "remote_app",
    "external_link",
    "slurm_job",
    "container_app",
    name="app_type",
)
EXECUTION_TARGET = sa.Enum(
    "linux_runner",
    "slurm",
    "apptainer",
    "windows_worker",
    "external_url",
    "local_pc",
    name="execution_target",
)
APP_STATUS = sa.Enum(
    "draft", "beta", "stable", "deprecated", "archived", name="app_status"
)
APP_VISIBILITY = sa.Enum(
    "private", "team", "department", "company", name="app_visibility"
)
BUILD_STATUS = sa.Enum(
    "pending", "building", "success", "failed", name="build_status"
)
SUBMISSION_STATUS = sa.Enum(
    "pending",
    "under_review",
    "manifest_required",
    "approved",
    "rejected",
    "provisioning",
    "building",
    "built",
    "published",
    "failed",
    name="submission_status",
)
JOB_STATUS = sa.Enum(
    "queued", "running", "success", "failed", "canceled", name="job_status"
)
PRINCIPAL_TYPE = sa.Enum("user", "group", "role", name="principal_type")
PERMISSION_LEVEL = sa.Enum("view", "execute", "manage", name="permission_level")


def upgrade() -> None:
    # users -------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("organization", sa.String(200), nullable=False, server_default=""),
        sa.Column("password_hash", sa.String(512), nullable=True),
        sa.Column("auth_source", AUTH_SOURCE, nullable=False, server_default="local"),
        sa.Column("sso_subject", sa.String(255), nullable=True, unique=True),
        sa.Column("email_verified", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("status", USER_STATUS, nullable=False, server_default="pending_verification"),
        sa.Column("role", USER_ROLE, nullable=False, server_default="user"),
        sa.Column("ldap_groups", postgresql.JSON, nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # apps --------------------------------------------------------------------
    op.create_table(
        "apps",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("app_type", APP_TYPE, nullable=False),
        sa.Column("execution_target", EXECUTION_TARGET, nullable=False),
        sa.Column("status", APP_STATUS, nullable=False, server_default="draft"),
        sa.Column("visibility", APP_VISIBILITY, nullable=False, server_default="team"),
        sa.Column("upstream_repo_url", sa.String(1024), nullable=False),
        sa.Column("overlay_repo_url", sa.String(1024), nullable=True),
        sa.Column("tags", postgresql.JSON, nullable=True),
        sa.Column("workspace_path", sa.String(1024), nullable=False),
        sa.Column("extra", postgresql.JSON, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_apps_status_visibility", "apps", ["status", "visibility"])
    op.create_index("ix_apps_owner", "apps", ["owner_user_id"])

    # app_versions ------------------------------------------------------------
    op.create_table(
        "app_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "app_id",
            sa.String(64),
            sa.ForeignKey("apps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("git_commit_hash", sa.String(64), nullable=True),
        sa.Column("git_tag", sa.String(128), nullable=True),
        sa.Column("manifest_snapshot", postgresql.JSON, nullable=True),
        sa.Column("build_status", BUILD_STATUS, nullable=False, server_default="pending"),
        sa.Column("build_log_path", sa.String(1024), nullable=True),
        sa.Column("sif_path", sa.String(1024), nullable=True),
        sa.Column("venv_path", sa.String(1024), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "released_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_app_versions_app", "app_versions", ["app_id"])
    op.create_foreign_key(
        "fk_apps_current_version",
        "apps",
        "app_versions",
        ["current_version_id"],
        ["id"],
        use_alter=True,
    )

    # submissions -------------------------------------------------------------
    op.create_table(
        "submissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "submitter_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("proposed_app_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("upstream_repo_url", sa.String(1024), nullable=False),
        sa.Column("proposed_app_type", sa.String(50), nullable=True),
        sa.Column("proposed_execution_target", sa.String(50), nullable=True),
        sa.Column("proposed_manifest", postgresql.JSON, nullable=True),
        sa.Column("status", SUBMISSION_STATUS, nullable=False, server_default="pending"),
        sa.Column("review_notes", sa.Text, nullable=True),
        sa.Column(
            "reviewer_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_submissions_submitter", "submissions", ["submitter_user_id"])
    op.create_index("ix_submissions_status_created", "submissions", ["status", "created_at"])
    op.create_index("ix_submissions_app_id", "submissions", ["proposed_app_id"])

    # jobs --------------------------------------------------------------------
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("app_id", sa.String(64), sa.ForeignKey("apps.id"), nullable=False),
        sa.Column(
            "app_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_versions.id"),
            nullable=True,
        ),
        sa.Column(
            "executor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("status", JOB_STATUS, nullable=False, server_default="queued"),
        sa.Column("execution_target", sa.String(50), nullable=False),
        sa.Column("params_json", postgresql.JSON, nullable=True),
        sa.Column("input_files", postgresql.JSON, nullable=True),
        sa.Column("storage_path", sa.String(1024), nullable=False),
        sa.Column("result_summary", postgresql.JSON, nullable=True),
        sa.Column("error_message", sa.String(2048), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_sec", sa.Integer, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_jobs_app_id", "jobs", ["app_id"])
    op.create_index("ix_jobs_executor", "jobs", ["executor_user_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_created", "jobs", ["created_at"])

    # permissions -------------------------------------------------------------
    op.create_table(
        "permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "app_id",
            sa.String(64),
            sa.ForeignKey("apps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("principal_type", PRINCIPAL_TYPE, nullable=False),
        sa.Column("principal_id", sa.String(255), nullable=False),
        sa.Column("permission", PERMISSION_LEVEL, nullable=False),
        sa.UniqueConstraint(
            "app_id", "principal_type", "principal_id", "permission", name="uq_permission_grant"
        ),
    )
    op.create_index("ix_permissions_app", "permissions", ["app_id"])

    # audit_log ---------------------------------------------------------------
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(128), nullable=False),
        sa.Column("meta", postgresql.JSON, nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_audit_actor_created", "audit_log", ["actor_user_id", "created_at"])
    op.create_index("ix_audit_action", "audit_log", ["action"])
    op.create_index("ix_audit_target", "audit_log", ["target_type", "target_id"])
    op.create_index("ix_audit_created", "audit_log", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("permissions")
    op.drop_table("jobs")
    op.drop_table("submissions")
    op.drop_constraint("fk_apps_current_version", "apps", type_="foreignkey")
    op.drop_table("app_versions")
    op.drop_table("apps")
    op.drop_table("users")

    for enum in (
        PERMISSION_LEVEL,
        PRINCIPAL_TYPE,
        JOB_STATUS,
        SUBMISSION_STATUS,
        BUILD_STATUS,
        APP_VISIBILITY,
        APP_STATUS,
        EXECUTION_TARGET,
        APP_TYPE,
        USER_ROLE,
        USER_STATUS,
        AUTH_SOURCE,
    ):
        enum.drop(op.get_bind(), checkfirst=True)
