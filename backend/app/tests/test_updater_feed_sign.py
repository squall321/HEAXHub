"""GET /api/v1/installers/{app_id}/latest (Tauri updater feed) must serve the
real minisign signature + an absolute public download URL — and 204 when there's
no .sig to verify against. Public (no auth).

TestClient + savepoint get_db override; writes a real .sig under
installer_storage_root and cleans up. Skips when Postgres is unreachable.
"""
from __future__ import annotations

import shutil
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.models.app import App, AppStatus, AppType, AppVisibility, ExecutionTarget
from app.db.models.installer_package import InstallerPackage
from app.db.models.user import AuthSource, User, UserRole, UserStatus
from app.db.session import engine, get_db
from app.main import app as fastapi_app
from app.services import installer_packages

SIG = "untrusted comment: sig\nRWQ_FAKE_MINISIGN_SIG==\n"


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
        pytest.skip("database unreachable")
    conn = engine.connect()
    txn = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    fastapi_app.dependency_overrides[get_db] = lambda: s
    client = TestClient(fastapi_app)
    try:
        yield s, client
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)
        s.close()
        txn.rollback()
        conn.close()


def _app_with_pkg(session: Session, app_id: str, version: str = "1.2.3") -> None:
    owner = User(
        email=f"feed-owner-{app_id}@example.com", display_name="O", organization="t",
        password_hash="x", auth_source=AuthSource.LOCAL, email_verified=True,
        status=UserStatus.ACTIVE, role=UserRole.ADMIN,
    )
    session.add(owner)
    session.flush()
    session.add(App(
        id=app_id, name=app_id, owner_user_id=owner.id, app_type=AppType.WINDOWS_GUI,
        execution_target=ExecutionTarget.LOCAL_PC, status=AppStatus.STABLE,
        visibility=AppVisibility.TEAM, upstream_repo_url="https://e.test/x.git",
        workspace_path="/tmp/x",
    ))
    session.add(InstallerPackage(
        app_id=app_id, version=version, os="windows-x64",
        installer_url=f"/api/v1/apps/{app_id}/installers/windows-x64/{version}",
        sha256="e" * 64, signed=True,
    ))
    session.flush()


def _feed(app_id: str) -> str:
    return f"/api/v1/installers/{app_id}/latest"


def test_feed_204_without_signature(ctx) -> None:
    session, client = ctx
    _app_with_pkg(session, "feed-nosig")
    # No .sig on disk → nothing the updater could verify → no update.
    assert client.get(_feed("feed-nosig")).status_code == 204


def test_feed_serves_signature_and_absolute_public_url(ctx) -> None:
    session, client = ctx
    app_id, version = "feed-ok", "1.2.3"
    _app_with_pkg(session, app_id, version)
    d = installer_packages.installer_dir(app_id, "windows-x64", version)
    d.mkdir(parents=True, exist_ok=True)
    installer_packages.signature_path(app_id, "windows-x64", version).write_text(
        SIG, encoding="utf-8"
    )
    try:
        resp = client.get(_feed(app_id))  # public — no bearer
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["version"] == version
        plat = body["platforms"]["windows-x86_64"]
        assert plat["signature"] == SIG.strip()
        assert plat["url"].endswith(f"/api/v1/installers/{app_id}/public-download")
        assert plat["url"].startswith("http")  # absolute
    finally:
        shutil.rmtree(d.parents[1], ignore_errors=True)
