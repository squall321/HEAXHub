"""Tests for DELETE /api/v1/apps/{app_id}/installers/{installer_id} (admin).

TestClient + savepoint get_db override (DB rolls back). Skips when Postgres is
unreachable.
"""
from __future__ import annotations

import uuid
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


def _user(session: Session, *, role: UserRole, email: str) -> str:
    u = User(
        email=email, display_name="T", organization="t", password_hash="x",
        auth_source=AuthSource.LOCAL, email_verified=True,
        status=UserStatus.ACTIVE, role=role,
    )
    session.add(u)
    session.flush()
    return create_access_token(str(u.id))


def _app_with_pkg(session: Session, app_id: str) -> InstallerPackage:
    owner = User(
        email=f"del-owner-{app_id}@example.com", display_name="O", organization="t",
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
    pkg = InstallerPackage(
        app_id=app_id, version="1.0.0", os="windows-x64",
        installer_url=f"/api/v1/apps/{app_id}/installers/windows-x64/1.0.0",
        sha256="a" * 64, signed=True,
    )
    session.add(pkg)
    session.flush()
    return pkg


def _auth(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def test_delete_installer_removes_row(ctx) -> None:
    session, client = ctx
    admin = _user(session, role=UserRole.ADMIN, email="del-admin-1@example.com")
    pkg = _app_with_pkg(session, "del_app_1")
    resp = client.delete(f"/api/v1/apps/del_app_1/installers/{pkg.id}", headers=_auth(admin))
    assert resp.status_code == 200, resp.text
    assert session.get(InstallerPackage, pkg.id) is None


def test_delete_wrong_app_id_404(ctx) -> None:
    session, client = ctx
    admin = _user(session, role=UserRole.ADMIN, email="del-admin-2@example.com")
    pkg = _app_with_pkg(session, "del_app_2")
    # Same installer id, but addressed under a different app — must 404, not delete.
    resp = client.delete(f"/api/v1/apps/other_app/installers/{pkg.id}", headers=_auth(admin))
    assert resp.status_code == 404, resp.text
    assert session.get(InstallerPackage, pkg.id) is not None  # still there


def test_delete_unknown_404(ctx) -> None:
    session, client = ctx
    admin = _user(session, role=UserRole.ADMIN, email="del-admin-3@example.com")
    _app_with_pkg(session, "del_app_3")
    resp = client.delete(
        f"/api/v1/apps/del_app_3/installers/{uuid.uuid4()}", headers=_auth(admin)
    )
    assert resp.status_code == 404, resp.text


def test_delete_requires_admin_403(ctx) -> None:
    session, client = ctx
    user = _user(session, role=UserRole.USER, email="del-user-1@example.com")
    pkg = _app_with_pkg(session, "del_app_4")
    resp = client.delete(f"/api/v1/apps/del_app_4/installers/{pkg.id}", headers=_auth(user))
    assert resp.status_code == 403, resp.text
