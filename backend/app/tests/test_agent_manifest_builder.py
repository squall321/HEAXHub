"""Tests for agent_manifest_builder (HWAXAgent program catalog, NEXT_STEPS §2.3).

DB tests use the same savepoint-roll-back trick as test_agent_registry; if the
database is unreachable they skip.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.models.app import (
    App,
    AppStatus,
    AppType,
    AppVisibility,
    ExecutionTarget,
)
from app.db.models.installer_package import InstallerPackage
from app.db.models.user import AuthSource, User, UserRole, UserStatus
from app.db.session import engine
from app.services import agent_manifest_builder

BASE_URL = "https://hub.example.test"
SHA = "a" * 64


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
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# ── fixtures helpers ─────────────────────────────────────────────────────────


def _make_owner(db: Session) -> User:
    user = User(
        email="manifest-test@example.com",
        display_name="Manifest Test",
        organization="t",
        password_hash="x",
        auth_source=AuthSource.LOCAL,
        email_verified=True,
        status=UserStatus.ACTIVE,
        role=UserRole.ADMIN,
    )
    db.add(user)
    db.flush()
    return user


def _make_app(
    db: Session,
    owner: User,
    app_id: str,
    *,
    app_type: AppType = AppType.WINDOWS_GUI,
    status: AppStatus = AppStatus.STABLE,
    name: str | None = None,
    extra: dict | None = None,
    description: str | None = None,
) -> App:
    app = App(
        id=app_id,
        name=name or app_id.replace("_", " ").title(),
        description=description,
        owner_user_id=owner.id,
        app_type=app_type,
        execution_target=ExecutionTarget.LOCAL_PC,
        status=status,
        visibility=AppVisibility.TEAM,
        upstream_repo_url="https://example.com/x.git",
        workspace_path="/tmp/x",
        extra=extra,
    )
    db.add(app)
    db.flush()
    return app


def _make_pkg(
    db: Session,
    app_id: str,
    *,
    version: str,
    os: str = "windows-x64",
    url: str = "https://store.example.test/x.exe",
    uploaded_at: datetime | None = None,
    size_bytes: int | None = 1234,
) -> InstallerPackage:
    pkg = InstallerPackage(
        app_id=app_id,
        version=version,
        os=os,
        installer_url=url,
        sha256=SHA,
        size_bytes=size_bytes,
        uploaded_at=uploaded_at or datetime.now(timezone.utc),
    )
    db.add(pkg)
    db.flush()
    return pkg


def _ids(manifest: dict) -> set[str]:
    return {p["id"] for p in manifest["programs"]}


# ── intersection / filtering ─────────────────────────────────────────────────


def test_only_apps_with_windows_installer_appear(db: Session) -> None:
    owner = _make_owner(db)
    _make_app(db, owner, "has_installer")
    _make_pkg(db, "has_installer", version="1.0.0")
    _make_app(db, owner, "no_installer")  # windows_gui but no package

    manifest = agent_manifest_builder.build_manifest(db, base_url=BASE_URL)

    assert "has_installer" in _ids(manifest)
    assert "no_installer" not in _ids(manifest)


def test_hidden_statuses_excluded(db: Session) -> None:
    owner = _make_owner(db)
    for app_id, status in [
        ("st_stable", AppStatus.STABLE),
        ("st_beta", AppStatus.BETA),
        ("st_draft", AppStatus.DRAFT),
        ("st_archived", AppStatus.ARCHIVED),
    ]:
        _make_app(db, owner, app_id, status=status)
        _make_pkg(db, app_id, version="1.0.0")

    ids = _ids(agent_manifest_builder.build_manifest(db, base_url=BASE_URL))
    assert {"st_stable", "st_beta"} <= ids
    assert "st_draft" not in ids
    assert "st_archived" not in ids


def test_non_windows_gui_excluded(db: Session) -> None:
    owner = _make_owner(db)
    _make_app(db, owner, "a_web", app_type=AppType.WEB_APP)
    # Even with a windows-x64 installer attached, a non-windows_gui app is out.
    _make_pkg(db, "a_web", version="1.0.0")

    assert "a_web" not in _ids(
        agent_manifest_builder.build_manifest(db, base_url=BASE_URL)
    )


def test_non_windows_os_installer_excluded(db: Session) -> None:
    owner = _make_owner(db)
    _make_app(db, owner, "mac_only")
    _make_pkg(db, "mac_only", version="1.0.0", os="macos-arm64")

    assert "mac_only" not in _ids(
        agent_manifest_builder.build_manifest(db, base_url=BASE_URL)
    )


# ── latest-version selection ─────────────────────────────────────────────────


def test_latest_installer_wins(db: Session) -> None:
    owner = _make_owner(db)
    _make_app(db, owner, "versioned")
    now = datetime.now(timezone.utc)
    _make_pkg(db, "versioned", version="1.0.0", uploaded_at=now - timedelta(days=2))
    _make_pkg(db, "versioned", version="2.0.0", uploaded_at=now)

    manifest = agent_manifest_builder.build_manifest(db, base_url=BASE_URL)
    program = next(p for p in manifest["programs"] if p["id"] == "versioned")
    assert program["version"] == "2.0.0"


# ── url / package shape ──────────────────────────────────────────────────────


def test_package_url_is_download_endpoint_and_type_inferred(db: Session) -> None:
    owner = _make_owner(db)
    _make_app(db, owner, "pkgshape")
    pkg = _make_pkg(
        db, "pkgshape", version="1.0.0", url="https://store.example.test/Setup.msi"
    )

    manifest = agent_manifest_builder.build_manifest(db, base_url=BASE_URL + "/")
    program = next(p for p in manifest["programs"] if p["id"] == "pkgshape")
    # Points at the HEAXHub download endpoint (not the raw store URL), and the
    # trailing slash on base_url is normalised away.
    assert program["package"]["url"] == f"{BASE_URL}/api/v1/installers/{pkg.id}/download"
    assert program["package"]["type"] == "msi"  # inferred from .msi suffix
    assert program["package"]["sha256"] == SHA
    assert program["package"]["size_bytes"] == 1234


# ── extra.windows_install enrichment + defaults ──────────────────────────────


def test_windows_install_enrichment(db: Session) -> None:
    owner = _make_owner(db)
    extra = {
        "windows_install": {
            "entry": {"executable": "bin/Hwax.exe", "args_template": ["{workspace}"]},
            "requirements": {"requires_admin": True, "min_windows": "11.0.22000"},
            "ui": {"show_in_tray": True, "color_accent": "#aabbcc"},
            "category": "simulation",
            "junk_key": "must-not-leak",  # additionalProperties:false in contract
        }
    }
    _make_app(db, owner, "enriched", extra=extra)
    _make_pkg(db, "enriched", version="1.0.0")

    program = next(
        p
        for p in agent_manifest_builder.build_manifest(db, base_url=BASE_URL)["programs"]
        if p["id"] == "enriched"
    )
    assert program["entry"]["executable"] == "bin/Hwax.exe"
    assert program["entry"]["args_template"] == ["{workspace}"]
    assert program["requirements"]["requires_admin"] is True
    assert program["ui"]["show_in_tray"] is True
    assert program["category"] == "simulation"
    # Junk under windows_install never reaches the manifest sub-objects.
    assert "junk_key" not in program
    assert "junk_key" not in program["entry"]


def test_entry_default_when_no_metadata(db: Session) -> None:
    owner = _make_owner(db)
    _make_app(db, owner, "bare")
    _make_pkg(db, "bare", version="1.0.0")

    program = next(
        p
        for p in agent_manifest_builder.build_manifest(db, base_url=BASE_URL)["programs"]
        if p["id"] == "bare"
    )
    # No windows_install → conventional fallback so the program is still valid.
    assert program["entry"]["executable"] == "bare.exe"
    assert "requirements" not in program
    assert "lifecycle" not in program
    assert "ui" not in program


# ── top-level shape ──────────────────────────────────────────────────────────


def test_manifest_top_level_shape(db: Session) -> None:
    owner = _make_owner(db)
    _make_app(db, owner, "shape_a")
    _make_pkg(db, "shape_a", version="1.0.0")

    manifest = agent_manifest_builder.build_manifest(db, base_url=BASE_URL)
    assert manifest["schema_version"] == 1
    assert isinstance(manifest["generated_at"], str)
    assert isinstance(manifest["programs"], list)


# ── contract schema validation (skips if jsonschema not installed) ───────────


def test_manifest_validates_against_contract(db: Session) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = (
        Path(__file__).resolve().parents[3]
        / "contracts"
        / "hwax-agent"
        / "manifest.schema.json"
    )
    if not schema_path.exists():
        pytest.skip(f"contract schema not found at {schema_path}")
    import json

    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    owner = _make_owner(db)
    _make_app(
        db,
        owner,
        "validated",
        description="A validated program",
        extra={
            "windows_install": {
                "entry": {"executable": "bin/V.exe"},
                "ui": {"color_accent": "#123456"},
            }
        },
    )
    _make_pkg(db, "validated", version="3.1.4")

    manifest = agent_manifest_builder.build_manifest(db, base_url=BASE_URL)
    # Raises ValidationError on any contract violation.
    jsonschema.validate(instance=manifest, schema=schema)
