"""GET /api/v1/installers/{id}/download must STREAM the bytes for an internal
(relative) installer_url — the launcher's agent JWT can't follow a 302 to the
user-gated /apps/... route. Absolute installer_url still 302s.

TestClient + savepoint get_db override; the stream case writes a real file under
installer_storage_root and cleans it up. Skips when Postgres is unreachable.
"""
from __future__ import annotations

import shutil
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.security import create_access_token
from app.db.models.app import App, AppStatus, AppType, AppVisibility, ExecutionTarget
from app.db.models.installer_package import InstallerPackage
from app.db.models.user import AuthSource, User, UserRole, UserStatus
from app.db.session import engine, get_db
from app.main import app as fastapi_app
from app.services import agent_registry, installer_packages

SHA = "d" * 64


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


def _launcher_token(session: Session, name: str) -> str:
    agent, _ = agent_registry.register_agent(
        session, name=name, pool="hwax-launcher", device_kind="launcher"
    )
    return create_access_token(str(agent.id), extra={"aud": "hwax-agent"})


def _app(session: Session, app_id: str) -> None:
    owner = User(
        email=f"dls-owner-{app_id}@example.com", display_name="O", organization="t",
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
    session.flush()


def _pkg(session: Session, app_id: str, *, url: str) -> InstallerPackage:
    row = InstallerPackage(
        app_id=app_id, version="1.0.0", os="windows-x64",
        installer_url=url, sha256=SHA, signed=True,
    )
    session.add(row)
    session.flush()
    return row


def _auth(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def test_relative_installer_url_streams_bytes(ctx) -> None:
    session, client = ctx
    tok = _launcher_token(session, "dls-stream")
    app_id, os_name, version = "dls_stream_app", "windows-x64", "1.0.0"
    _app(session, app_id)
    pkg = _pkg(session, app_id, url=f"/api/v1/apps/{app_id}/installers/{os_name}/{version}")

    d = installer_packages.installer_dir(app_id, os_name, version)
    d.mkdir(parents=True, exist_ok=True)
    installer_packages.installer_path(app_id, os_name, version).write_bytes(b"AGENT-SETUP")
    try:
        resp = client.get(
            f"/api/v1/installers/{pkg.id}/download",
            headers=_auth(tok),
            follow_redirects=False,
        )
        assert resp.status_code == 200, resp.text  # streamed, NOT 302
        assert resp.content == b"AGENT-SETUP"
        assert resp.headers["x-sha256"] == SHA
    finally:
        shutil.rmtree(d.parents[1], ignore_errors=True)


def test_relative_missing_file_410(ctx) -> None:
    session, client = ctx
    tok = _launcher_token(session, "dls-410")
    _app(session, "dls_missing")
    pkg = _pkg(session, "dls_missing", url="/api/v1/apps/dls_missing/installers/windows-x64/1.0.0")
    resp = client.get(
        f"/api/v1/installers/{pkg.id}/download", headers=_auth(tok), follow_redirects=False
    )
    assert resp.status_code == 410, resp.text


def test_absolute_installer_url_redirects(ctx) -> None:
    session, client = ctx
    tok = _launcher_token(session, "dls-redir")
    _app(session, "dls_abs")
    abs_url = "https://store.example.test/x.exe"
    pkg = _pkg(session, "dls_abs", url=abs_url)
    resp = client.get(
        f"/api/v1/installers/{pkg.id}/download", headers=_auth(tok), follow_redirects=False
    )
    assert resp.status_code == 302, resp.text
    assert resp.headers["location"] == abs_url
