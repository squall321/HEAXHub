"""Tests for GET /api/v1/installers/{id}/download (NEXT_STEPS §2.5).

Drives the real app via TestClient with a savepoint-bound get_db override (DB
rolls back). The disk-stream case writes a real file under installer_storage_root
and cleans it up. Skips when Postgres is unreachable.
"""
from __future__ import annotations

import shutil
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.security import create_access_token
from app.db.models.app import (
    App,
    AppStatus,
    AppType,
    AppVisibility,
    ExecutionTarget,
)
from app.db.models.installer_package import InstallerPackage
from app.db.models.user import AuthSource, User, UserRole, UserStatus
from app.db.session import engine, get_db
from app.main import app as fastapi_app
from app.services import agent_registry, installer_packages

SHA = "b" * 64


def _make_app(session: Session, app_id: str, *, status: AppStatus = AppStatus.STABLE) -> App:
    """A windows_gui app + owner so the download gate (publishable status) passes."""
    owner = User(
        email=f"dl-owner-{app_id}@example.com",
        display_name="O",
        organization="t",
        password_hash="x",
        auth_source=AuthSource.LOCAL,
        email_verified=True,
        status=UserStatus.ACTIVE,
        role=UserRole.ADMIN,
    )
    session.add(owner)
    session.flush()
    app = App(
        id=app_id,
        name=app_id,
        owner_user_id=owner.id,
        app_type=AppType.WINDOWS_GUI,
        execution_target=ExecutionTarget.LOCAL_PC,
        status=status,
        visibility=AppVisibility.TEAM,
        upstream_repo_url="https://example.com/x.git",
        workspace_path="/tmp/x",
    )
    session.add(app)
    session.flush()
    return app


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


@pytest.fixture()
def ctx() -> Iterator[tuple[Session, TestClient]]:
    if not _db_reachable():
        pytest.skip("database unreachable; skipping DB-backed test")
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    fastapi_app.dependency_overrides[get_db] = lambda: session
    client = TestClient(fastapi_app)
    try:
        yield session, client
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)
        session.close()
        transaction.rollback()
        connection.close()


def _launcher_token(session: Session, name: str) -> str:
    agent, _ = agent_registry.register_agent(
        session, name=name, pool="hwax-launcher", device_kind="launcher"
    )
    return create_access_token(str(agent.id), extra={"aud": "hwax-agent"})


def _make_pkg(
    session: Session, *, app_id: str, version: str, os: str, url: str
) -> InstallerPackage:
    row = InstallerPackage(
        app_id=app_id, version=version, os=os, installer_url=url, sha256=SHA, signed=True
    )
    session.add(row)
    session.flush()
    return row


def _url(installer_id) -> str:
    return f"/api/v1/installers/{installer_id}/download"


# ── disk stream (current deployment) ─────────────────────────────────────────────


def test_download_streams_disk_file(ctx) -> None:
    session, client = ctx
    token = _launcher_token(session, "dl-stream-1")
    app_id, os_name, version = "dl_stream_app", "windows-x64", "1.0.0"
    _make_app(session, app_id)
    pkg = _make_pkg(
        session,
        app_id=app_id,
        version=version,
        os=os_name,
        url=f"/api/v1/apps/{app_id}/installers/{os_name}/{version}",  # relative
    )

    d = installer_packages.installer_dir(app_id, os_name, version)
    d.mkdir(parents=True, exist_ok=True)
    installer_packages.installer_path(app_id, os_name, version).write_bytes(b"INSTALLER")
    try:
        resp = client.get(_url(pkg.id), headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        assert resp.content == b"INSTALLER"
        assert resp.headers["x-installer-sha256"] == SHA
        assert resp.headers["content-type"] == "application/octet-stream"
    finally:
        shutil.rmtree(d.parents[1], ignore_errors=True)  # remove storage_root/app_id


def test_download_410_when_file_missing(ctx) -> None:
    session, client = ctx
    token = _launcher_token(session, "dl-missing-1")
    app_id, os_name, version = "dl_missing_app", "windows-x64", "2.0.0"
    _make_app(session, app_id)
    pkg = _make_pkg(
        session,
        app_id=app_id,
        version=version,
        os=os_name,
        url=f"/api/v1/apps/{app_id}/installers/{os_name}/{version}",
    )
    resp = client.get(_url(pkg.id), headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 410, resp.text


# ── absolute-URL redirect (future object storage) ────────────────────────────────


def test_download_redirects_for_absolute_url(ctx) -> None:
    session, client = ctx
    token = _launcher_token(session, "dl-redirect-1")
    abs_url = "https://store.example.test/installers/x.exe"
    _make_app(session, "dl_redir_app")
    pkg = _make_pkg(
        session, app_id="dl_redir_app", version="1.0.0", os="windows-x64", url=abs_url
    )
    resp = client.get(
        _url(pkg.id),
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    assert resp.headers["location"] == abs_url


# ── not found / auth ─────────────────────────────────────────────────────────────


def test_download_unknown_id_404(ctx) -> None:
    session, client = ctx
    token = _launcher_token(session, "dl-404-1")
    resp = client.get(_url(uuid.uuid4()), headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404, resp.text


def test_download_draft_app_gated_404(ctx) -> None:
    # Least privilege: a valid launcher JWT must NOT pull an installer whose app
    # is DRAFT (unreleased) — 404, not revealing the id.
    session, client = ctx
    token = _launcher_token(session, "dl-draft-1")
    _make_app(session, "dl_draft_app", status=AppStatus.DRAFT)
    pkg = _make_pkg(
        session, app_id="dl_draft_app", version="1.0.0", os="windows-x64", url="x"
    )
    resp = client.get(_url(pkg.id), headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404, resp.text


def test_download_requires_bearer_401(ctx) -> None:
    session, client = ctx
    _make_app(session, "dl_auth_app")
    pkg = _make_pkg(
        session, app_id="dl_auth_app", version="1.0.0", os="windows-x64", url="x"
    )
    assert client.get(_url(pkg.id)).status_code == 401


def test_download_rejects_user_token_401(ctx) -> None:
    session, client = ctx
    user = User(
        email="dl-user@example.com",
        display_name="DL User",
        organization="t",
        password_hash="x",
        auth_source=AuthSource.LOCAL,
        email_verified=True,
        status=UserStatus.ACTIVE,
        role=UserRole.USER,
    )
    session.add(user)
    session.flush()
    user_token = create_access_token(str(user.id))  # no audience
    _make_app(session, "dl_user_app")
    pkg = _make_pkg(
        session, app_id="dl_user_app", version="1.0.0", os="windows-x64", url="x"
    )
    resp = client.get(_url(pkg.id), headers={"Authorization": f"Bearer {user_token}"})
    assert resp.status_code == 401, resp.text
