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
from app.db.models.app_version import AppVersion, BuildStatus
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


# ─── Honest build-status recording (source-backed path, builders mocked) ─────


def _patch_source_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fetch_action: str = "updated",
    fetch_commit: str | None = "a" * 40,
    sif_action: str = "built",
    sif_error: str | None = None,
):
    """Stub fetcher/sif_builder/launcher so no real git/apptainer runs."""
    from app.services import (
        integration_fetcher,
        integration_launcher,
        integration_sif_builder,
    )

    class _FR:
        action = fetch_action
        commit = fetch_commit
        error = "boom" if fetch_action == "failed" else None

    class _SR:
        action = sif_action
        sif = Path("/tmp/fake.sif") if sif_action in {"built", "skipped"} else None
        hash = "h"
        error = sif_error
        log_path = Path("/tmp/sif_build.log")
        commit = fetch_commit

    class _LR:
        action = "already_running"
        port = 9999
        base_path = "/apps/x"
        pid = 1
        error = None

    monkeypatch.setattr(
        integration_fetcher, "fetch_for_integration", lambda slug, src: _FR()
    )
    monkeypatch.setattr(
        integration_sif_builder, "build_sif", lambda slug, m, fr: _SR()
    )
    monkeypatch.setattr(
        integration_launcher, "launch", lambda *a, **k: _LR()
    )


def test_build_success_records_metadata(
    tmp_path: Path,
    db: Session,
    admin_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source-backed build success records SUCCESS + sif_path + commit."""
    _patch_source_pipeline(monkeypatch, fetch_action="updated", sif_action="built")
    app_id = _unique_app_id()
    _write_manifest(
        tmp_path, slug="demo-svc", app_id=app_id, version="1.0.0",
        stack="flask",
        extra={
            "launch": {"mode": "service", "command": "gunicorn app:app"},
            "source": {"type": "git", "url": "file:///tmp/repo.git", "ref": "main"},
        },
    )

    results = integrations_scanner.scan_integrations(db, root=tmp_path)
    assert results[0].action == "created"

    app = db.get(App, app_id)
    version = db.get(AppVersion, app.current_version_id)
    assert version.build_status == BuildStatus.SUCCESS
    assert version.sif_path == "/tmp/fake.sif"
    assert version.git_commit_hash == "a" * 40
    assert version.build_log_path == "/tmp/sif_build.log"


def test_build_failure_records_failed_and_audits(
    tmp_path: Path,
    db: Session,
    admin_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SIF build failure → build_status FAILED + integration.build.failed audit."""
    _patch_source_pipeline(
        monkeypatch, fetch_action="updated", sif_action="failed",
        sif_error="apptainer build exit=255",
    )
    app_id = _unique_app_id()
    _write_manifest(
        tmp_path, slug="demo-fail", app_id=app_id, version="1.0.0",
        stack="flask",
        extra={
            "launch": {"mode": "service", "command": "gunicorn app:app"},
            "source": {"type": "git", "url": "file:///tmp/repo.git", "ref": "main"},
        },
    )

    results = integrations_scanner.scan_integrations(db, root=tmp_path)
    assert results[0].action == "created"

    app = db.get(App, app_id)
    version = db.get(AppVersion, app.current_version_id)
    assert version.build_status == BuildStatus.FAILED
    assert version.build_log_path == "/tmp/sif_build.log"

    from app.db.models.audit_log import AuditLog
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.action == "integration.build.failed")
        .filter(AuditLog.target_id == str(version.id))
        .first()
    )
    assert audit is not None
    assert audit.meta and audit.meta.get("app_id") == app_id


def test_unchanged_commit_gated_skips_rebuild(
    tmp_path: Path,
    db: Session,
    admin_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the commit didn't move and a SIF exists, rescan stays 'unchanged'
    and never calls build_sif (commit-gated trigger)."""
    # First scan: build the version (commit 'a'*40, SIF built).
    _patch_source_pipeline(monkeypatch, fetch_action="updated", sif_action="built")
    app_id = _unique_app_id()
    _write_manifest(
        tmp_path, slug="demo-gate", app_id=app_id, version="1.0.0",
        stack="flask",
        extra={
            "launch": {"mode": "service", "command": "gunicorn app:app"},
            "source": {"type": "git", "url": "file:///tmp/repo.git", "ref": "main"},
        },
    )
    integrations_scanner.scan_integrations(db, root=tmp_path)

    # Make _existing_sif_path see a real file so the gate short-circuits.
    sif_file = tmp_path / "demo-gate.sif"
    sif_file.write_bytes(b"x")
    monkeypatch.setattr(
        integrations_scanner, "_existing_sif_path", lambda slug: sif_file
    )

    # Second scan: commit unchanged (fetch 'skipped'); build_sif must NOT run.
    from app.services import integration_sif_builder

    def _boom(*a, **k):  # pragma: no cover - asserts non-invocation
        raise AssertionError("build_sif must not run when commit is unchanged")

    from app.services import integration_fetcher, integration_launcher

    class _FR:
        action = "skipped"
        commit = "a" * 40
        error = None

    class _LR:
        action = "already_running"
        port = 9999
        base_path = "/apps/x"
        pid = 1
        error = None

    monkeypatch.setattr(integration_fetcher, "fetch_for_integration", lambda s, src: _FR())
    monkeypatch.setattr(integration_sif_builder, "build_sif", _boom)
    monkeypatch.setattr(integration_launcher, "launch", lambda *a, **k: _LR())

    again = integrations_scanner.scan_integrations(db, root=tmp_path)
    assert again[0].action == "unchanged"
