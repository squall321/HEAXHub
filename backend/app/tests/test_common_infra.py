"""Smoke tests for v2 common infrastructure (SA1).

Covers:
  - port_allocator allocate/release/reuse roundtrip
  - secret_manager set/get/delete roundtrip with a temporary Fernet key
  - interpreter_pool major.minor fallback

DB-backed tests use the configured DATABASE_URL and roll back via a savepoint,
so they leave no rows behind. If the DB is unreachable they are skipped — the
file is still importable, matching the lightweight style of test_smoke.py.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import engine
from app.services import interpreter_pool, port_allocator, secret_manager


# ─── DB session fixture (savepoint-rolled-back) ──────────────────────────────


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


@pytest.fixture()
def db() -> Iterator[Session]:
    if not _db_reachable():
        pytest.skip("database unreachable; skipping DB-backed test")

    connection = engine.connect()
    transaction = connection.begin()
    # join_transaction_mode="create_savepoint" lets the session.commit() inside
    # services commit a SAVEPOINT instead of the outer transaction, so the outer
    # rollback at fixture teardown still discards everything.
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# ─── Fernet key fixture ──────────────────────────────────────────────────────


@pytest.fixture()
def fernet_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Inject a fresh Fernet key into settings for the duration of the test."""
    key = Fernet.generate_key().decode()
    settings = get_settings()
    monkeypatch.setattr(settings, "secret_encryption_key", key)
    return key


# ─── port_allocator ──────────────────────────────────────────────────────────


def test_port_allocator_allocate_release_reuse(db: Session) -> None:
    # Clear any pre-existing rows so the reuse assertion is deterministic.
    # The outer savepoint rollback discards this delete after the test.
    db.execute(text("DELETE FROM port_allocations"))
    db.commit()

    p1 = port_allocator.allocate_port(db, scope="app")
    p2 = port_allocator.allocate_port(db, scope="app")
    assert p1 != p2
    low = get_settings().app_port_range_low
    high = get_settings().app_port_range_high
    assert low <= p1 <= high
    assert low <= p2 <= high

    # Release p1, the next allocation should reuse it (oldest released wins).
    port_allocator.release_port(db, p1)
    p3 = port_allocator.allocate_port(db, scope="app")
    assert p3 == p1


# ─── secret_manager ──────────────────────────────────────────────────────────


def test_secret_manager_roundtrip(db: Session, fernet_key: str) -> None:
    key = "PYTEST_SECRET_INFRA_X"
    secret_manager.set_secret(db, key, "hello-world", scope="global", description="t")

    assert secret_manager.get_secret(db, key, scope="global") == "hello-world"

    # list_secrets returns metadata only, never the plaintext value.
    listing = secret_manager.list_secrets(db, scope_prefix="global")
    found = next((s for s in listing if s["key"] == key), None)
    assert found is not None
    assert "value" not in found
    assert "value_encrypted" not in found

    # Rotate the value.
    secret_manager.set_secret(db, key, "rotated", scope="global")
    assert secret_manager.get_secret(db, key, scope="global") == "rotated"

    # Delete.
    assert secret_manager.delete_secret(db, key, scope="global") is True
    assert secret_manager.get_secret(db, key, scope="global") is None


# ─── interpreter_pool ────────────────────────────────────────────────────────


def test_interpreter_pool_major_minor_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "interpreters.yaml"
    cfg.write_text(
        "python:\n"
        '  "3.10": /opt/py310\n'
        '  "3.11": /opt/py311\n'
        '  "3.12": /opt/py312\n'
        "node:\n"
        '  "20": /opt/node20\n',
        encoding="utf-8",
    )

    settings = get_settings()
    monkeypatch.setattr(settings, "interpreters_config", cfg)
    interpreter_pool.reload_config()

    # 1) exact match
    assert interpreter_pool.python_for("3.11") == "/opt/py311"

    # 2) major.minor fallback ("3.11.4" -> "3.11")
    assert interpreter_pool.python_for("3.11.4") == "/opt/py311"

    # 3) same-major fallback ("3.9" -> newest in same major = "3.12")
    assert interpreter_pool.python_for("3.9") == "/opt/py312"

    # available list is sorted ascending
    assert interpreter_pool.available_pythons() == ["3.10", "3.11", "3.12"]

    # Unknown major raises with the available list.
    with pytest.raises(RuntimeError) as excinfo:
        interpreter_pool.python_for("2.7")
    assert "2.7" in str(excinfo.value)
    assert "3.10" in str(excinfo.value)

    # Node resolves too.
    assert interpreter_pool.node_for("20") == "/opt/node20"
