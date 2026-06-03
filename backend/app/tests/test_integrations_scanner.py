"""Auto-discovery of integrations/ — scanner unit tests.

These DB-backed tests use a savepoint-rolled-back session (same pattern as
``test_common_infra.py``) so they leave no rows behind. The integrations root
is monkeypatched to a per-test ``tmp_path``, so we don't accidentally upsert
the real first-party demos.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.app import App
from app.db.models.app_version import AppVersion
from app.db.models.user import AuthSource, User, UserRole, UserStatus
from app.db.session import engine
from app.services import integrations_scanner


# ─── DB reachability + fixtures ──────────────────────────────────────────────


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
        pytest.skip("database unreachable; skipping DB-backed scanner test")

    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def admin_user(db: Session) -> Iterator[User]:
    """Inline admin so the scanner's seed-admin lookup always resolves.

    We also pin SEED_ADMIN_EMAIL via monkeypatch so the lookup is deterministic
    regardless of which admins already exist in the dev DB.
    """
    email = f"int-scan-{uuid.uuid4().hex[:8]}@example.com"
    user = User(
        email=email,
        display_name="Integration Scanner Test",
        organization="Test",
        password_hash="x",
        auth_source=AuthSource.LOCAL,
        status=UserStatus.ACTIVE,
        role=UserRole.ADMIN,
        email_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    settings = get_settings()
    # The savepoint rollback discards the user; restore the original email
    # afterward so other tests aren't affected by the monkeypatch.
    original = settings.seed_admin_email
    settings.seed_admin_email = email
    try:
        yield user
    finally:
        settings.seed_admin_email = original


def _write_manifest(
    root: Path,
    *,
    slug: str,
    app_id: str,
    version: str,
    stack: str = "python_cli",
    extra: dict | None = None,
) -> Path:
    integration_dir = root / slug
    portal_dir = integration_dir / ".portal"
    portal_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 2,
        "id": app_id,
        "name": f"Test · {slug}",
        "version": version,
        "owner": "test",
        "status": "stable",
        "app_type": "cli_tool",
        "execution_target": "linux_runner",
        "description": "synthetic integration for scanner tests",
        "tags": ["test", "scanner"],
        "build": {"stack": stack},
        "launch": {"mode": "job_runner", "command": "./.portal/run.sh"},
        "permissions": {"visibility": "team"},
    }
    if extra:
        manifest.update(extra)
    (portal_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest), encoding="utf-8"
    )
    return integration_dir


def _unique_app_id(prefix: str = "scan_test_") -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


# ─── Tests ───────────────────────────────────────────────────────────────────


def test_scan_creates_app(
    tmp_path: Path,
    db: Session,
    admin_user: User,
) -> None:
    """First scan of an integration with a fresh manifest creates App+AppVersion."""
    app_id = _unique_app_id()
    integration_dir = _write_manifest(
        tmp_path, slug="demo-cli", app_id=app_id, version="0.1.0"
    )

    results = integrations_scanner.scan_integrations(db, root=tmp_path)

    # Exactly one result for our one synthetic integration.
    assert len(results) == 1
    r = results[0]
    assert r.slug == "demo-cli"
    assert r.action == "created"
    assert r.app_id == app_id
    assert r.version == "0.1.0"

    app = db.get(App, app_id)
    assert app is not None
    assert app.workspace_path == str(integration_dir)
    assert app.current_version_id is not None
    assert app.owner_user_id == admin_user.id
    assert app.extra and app.extra.get("stack") == "python_cli"

    version = db.get(AppVersion, app.current_version_id)
    assert version is not None
    assert version.version == "0.1.0"
    assert version.manifest_snapshot
    assert version.manifest_snapshot.get("id") == app_id


def test_scan_updates_version(
    tmp_path: Path,
    db: Session,
    admin_user: User,
) -> None:
    """Bumping manifest.version creates a new AppVersion and re-points current_version_id."""
    app_id = _unique_app_id()

    _write_manifest(tmp_path, slug="demo-cli", app_id=app_id, version="0.1.0")
    first = integrations_scanner.scan_integrations(db, root=tmp_path)
    assert first[0].action == "created"
    app = db.get(App, app_id)
    assert app is not None
    first_version_id = app.current_version_id
    assert first_version_id is not None

    # Bump the version on disk.
    _write_manifest(tmp_path, slug="demo-cli", app_id=app_id, version="0.2.0")

    second = integrations_scanner.scan_integrations(db, root=tmp_path)
    assert len(second) == 1
    assert second[0].action == "updated"
    assert second[0].version == "0.2.0"

    db.expire_all()
    app = db.get(App, app_id)
    assert app is not None
    assert app.current_version_id is not None
    assert app.current_version_id != first_version_id

    new_version = db.get(AppVersion, app.current_version_id)
    assert new_version is not None
    assert new_version.version == "0.2.0"


def test_scan_unchanged_is_noop(
    tmp_path: Path,
    db: Session,
    admin_user: User,
) -> None:
    """Re-scanning the same version reports 'unchanged' and creates no new rows."""
    app_id = _unique_app_id()

    _write_manifest(tmp_path, slug="demo-cli", app_id=app_id, version="0.1.0")
    integrations_scanner.scan_integrations(db, root=tmp_path)

    # Count AppVersion rows for this app right after the first scan.
    before = (
        db.query(AppVersion).filter(AppVersion.app_id == app_id).count()
    )

    again = integrations_scanner.scan_integrations(db, root=tmp_path)
    assert len(again) == 1
    assert again[0].action == "unchanged"
    assert again[0].version == "0.1.0"

    after = (
        db.query(AppVersion).filter(AppVersion.app_id == app_id).count()
    )
    assert after == before
