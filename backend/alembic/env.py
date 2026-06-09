"""Alembic environment configuration for HEAXHub."""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make `app` importable
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# Load .env if present (so DATABASE_URL is available when running alembic locally)
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv(BACKEND_ROOT.parent / ".env")
except Exception:
    pass

from app.db.base import Base  # noqa: E402

# Import all models so that Base.metadata is fully populated
from app.db import models  # noqa: F401, E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

db_url = os.getenv("DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # ``transaction_per_migration=True`` is required so that 0008
        # (``ALTER TYPE app_type ADD VALUE 'desktop_agent'``) commits
        # before 0009 references the new enum value — Postgres rejects
        # newly added enum values inside the same transaction
        # (``UnsafeNewEnumValueUsage``).
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
