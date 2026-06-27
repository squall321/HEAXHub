# forward_auth(/api/v1/authz) 게이트의 인가 판정을 직접 검증하는 테스트.
"""Adversarial unit tests for the SEC-03 ``/api/v1/authz`` forward-auth gate.

These hit the endpoint through the FastAPI TestClient (no Caddy required) and
exercise the security-critical branches directly:

  * public (COMPANY+STABLE) app → 200 without any cookie (demo stays open).
  * private app, no cookie → 401.
  * private app, expired/forged token → 401.
  * private app, valid token but no view permission → 403.
  * slug forgery via client-supplied X-Forwarded-Uri is the *only* slug source
    Caddy feeds us — confirm the endpoint authorizes exactly that slug.
  * no /apps/{slug} in the URI → 200 pass-through (non-app traffic).
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.v1.authz import _SESSION_COOKIE_NAME
from app.core.security import create_access_token
from app.db.models.app import (
    App,
    AppStatus,
    AppType,
    AppVisibility,
    ExecutionTarget,
)
from app.db.models.user import AuthSource, User, UserRole, UserStatus
from app.db.session import SessionLocal, engine
from app.main import app as fastapi_app


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
        pytest.skip("database unreachable; skipping authz gate tests")
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(fastapi_app)


def _mk_user(db: Session, *, role: UserRole = UserRole.USER) -> User:
    user = User(
        email=f"authz-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Authz Test",
        organization="Test",
        password_hash="x",
        auth_source=AuthSource.LOCAL,
        status=UserStatus.ACTIVE,
        role=role,
        email_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _mk_app(
    db: Session,
    owner: User,
    *,
    visibility: AppVisibility,
    status_: AppStatus,
) -> App:
    app_id = f"authz_{uuid.uuid4().hex[:8]}"
    app = App(
        id=app_id,
        name="Authz App",
        owner_user_id=owner.id,
        app_type=AppType.WEB_APP,
        execution_target=ExecutionTarget.LINUX_RUNNER,
        status=status_,
        visibility=visibility,
        upstream_repo_url="https://example.com/repo.git",
        workspace_path=f"/tmp/{app_id}",
    )
    db.add(app)
    db.commit()
    return app


def _cleanup(db: Session, *objs) -> None:
    for obj in objs:
        db.delete(db.merge(obj))
    db.commit()


def _fwd(slug: str) -> dict[str, str]:
    return {"X-Forwarded-Uri": f"/apps/{slug}/some/path?q=1"}


def test_public_app_passes_without_cookie(db: Session, client: TestClient) -> None:
    owner = _mk_user(db)
    app = _mk_app(db, owner, visibility=AppVisibility.COMPANY, status_=AppStatus.STABLE)
    try:
        r = client.get("/api/v1/authz", headers=_fwd(app.id))
        assert r.status_code == 200
    finally:
        _cleanup(db, app, owner)


def test_private_app_blocked_without_cookie(db: Session, client: TestClient) -> None:
    owner = _mk_user(db)
    app = _mk_app(db, owner, visibility=AppVisibility.PRIVATE, status_=AppStatus.STABLE)
    try:
        r = client.get("/api/v1/authz", headers=_fwd(app.id))
        assert r.status_code == 401
    finally:
        _cleanup(db, app, owner)


def test_company_app_not_stable_blocked_without_cookie(
    db: Session, client: TestClient
) -> None:
    """COMPANY visibility but BETA status must NOT be public — only STABLE is."""
    owner = _mk_user(db)
    app = _mk_app(db, owner, visibility=AppVisibility.COMPANY, status_=AppStatus.BETA)
    try:
        r = client.get("/api/v1/authz", headers=_fwd(app.id))
        assert r.status_code == 401
    finally:
        _cleanup(db, app, owner)


def test_private_app_forged_token_blocked(db: Session, client: TestClient) -> None:
    owner = _mk_user(db)
    app = _mk_app(db, owner, visibility=AppVisibility.PRIVATE, status_=AppStatus.STABLE)
    try:
        headers = _fwd(app.id)
        headers["Cookie"] = f"{_SESSION_COOKIE_NAME}=not.a.jwt"
        r = client.get("/api/v1/authz", headers=headers)
        assert r.status_code == 401
    finally:
        _cleanup(db, app, owner)


def test_private_app_other_user_forbidden(db: Session, client: TestClient) -> None:
    owner = _mk_user(db)
    stranger = _mk_user(db)
    app = _mk_app(db, owner, visibility=AppVisibility.PRIVATE, status_=AppStatus.STABLE)
    try:
        token = create_access_token(str(stranger.id))
        headers = _fwd(app.id)
        headers["Cookie"] = f"{_SESSION_COOKIE_NAME}={token}"
        r = client.get("/api/v1/authz", headers=headers)
        assert r.status_code == 403
    finally:
        _cleanup(db, app, owner, stranger)


def test_private_app_owner_allowed(db: Session, client: TestClient) -> None:
    owner = _mk_user(db)
    app = _mk_app(db, owner, visibility=AppVisibility.PRIVATE, status_=AppStatus.STABLE)
    try:
        token = create_access_token(str(owner.id))
        headers = _fwd(app.id)
        headers["Cookie"] = f"{_SESSION_COOKIE_NAME}={token}"
        r = client.get("/api/v1/authz", headers=headers)
        assert r.status_code == 200
    finally:
        _cleanup(db, app, owner)


def test_no_slug_passes_through(db: Session, client: TestClient) -> None:
    r = client.get("/api/v1/authz", headers={"X-Forwarded-Uri": "/health"})
    assert r.status_code == 200


def test_unknown_slug_is_401(db: Session, client: TestClient) -> None:
    r = client.get("/api/v1/authz", headers=_fwd("does_not_exist_xyz"))
    assert r.status_code == 401


def test_bearer_token_also_accepted(db: Session, client: TestClient) -> None:
    """Authz also reads Authorization: Bearer — confirm a valid bearer works."""
    owner = _mk_user(db)
    app = _mk_app(db, owner, visibility=AppVisibility.PRIVATE, status_=AppStatus.STABLE)
    try:
        token = create_access_token(str(owner.id))
        headers = _fwd(app.id)
        headers["Authorization"] = f"Bearer {token}"
        r = client.get("/api/v1/authz", headers=headers)
        assert r.status_code == 200
    finally:
        _cleanup(db, app, owner)
