"""HTTP-level smoke tests.

Fire startup via FastAPI's TestClient and hit every GET endpoint that doesn't
need a body. Catches:
  - bad response_model declarations (FastAPI raises at request time)
  - handlers that crash on empty DB
  - missing dependencies in routing wiring

DB-backed tests are skipped when PostgreSQL is unreachable — the file stays
importable and unit-only assertions still run.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.db.models.user import AuthSource, User, UserRole, UserStatus
from app.db.session import SessionLocal, engine
from app.main import app

# --- Endpoints exercised without auth (expect 401 or 200) -------------------

# Routes that should respond 200 even with no auth.
PUBLIC_GET_ROUTES = [
    "/",
    "/health",
]

# Routes that require auth — we hit them with no token and assert 401.
PROTECTED_GET_ROUTES_NO_AUTH = [
    "/api/v1/auth/me",
    "/api/v1/users/me",
    "/api/v1/apps",
    "/api/v1/apps/recommended",
    "/api/v1/apps/favorites",
    "/api/v1/jobs",
    "/api/v1/submissions",
    "/api/v1/change-requests",
    "/api/v1/admin/users",
    "/api/v1/admin/updates",
    "/api/v1/admin/stats",
    "/api/v1/admin/audit",
    "/api/v1/admin/system/health",
    "/api/v1/admin/integrations",
    "/api/v1/admin/licenses",
    "/api/v1/admin/gpus",
    "/api/v1/admin/services",
]

# Routes that need an authenticated user (we'll seed an admin and pass a token).
ADMIN_GET_ROUTES_WITH_AUTH = [
    "/api/v1/auth/me",
    "/api/v1/users/me",
    "/api/v1/apps",
    "/api/v1/apps/recommended",
    "/api/v1/apps/favorites",
    "/api/v1/jobs",
    "/api/v1/submissions",
    "/api/v1/change-requests",
    "/api/v1/admin/users",
    "/api/v1/admin/updates",
    "/api/v1/admin/stats",
    "/api/v1/admin/audit",
    "/api/v1/admin/system/health",
    "/api/v1/admin/integrations",
    "/api/v1/admin/licenses",
    "/api/v1/admin/gpus",
    "/api/v1/admin/services",
]


# --- Fixtures ---------------------------------------------------------------


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    """TestClient that triggers FastAPI startup/shutdown lifespan."""
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin_token() -> Iterator[tuple[str, uuid.UUID]]:
    """Seed a throwaway admin, return (jwt, user_id). Cleaned up on teardown."""
    if not _db_reachable():
        pytest.skip("database unreachable; skipping authenticated smoke test")

    session: Session = SessionLocal()
    email = f"smoke-admin-{uuid.uuid4().hex[:8]}@example.com"
    user = User(
        email=email,
        display_name="Smoke Admin",
        organization="Test",
        password_hash=hash_password("StrongPa55word!"),
        auth_source=AuthSource.LOCAL,
        status=UserStatus.ACTIVE,
        role=UserRole.ADMIN,
        email_verified=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    token = create_access_token(str(user.id))
    user_id = user.id
    session.close()

    try:
        yield token, user_id
    finally:
        session = SessionLocal()
        try:
            row = session.get(User, user_id)
            if row is not None:
                session.delete(row)
                session.commit()
        finally:
            session.close()


# --- Tests ------------------------------------------------------------------


@pytest.mark.parametrize("path", PUBLIC_GET_ROUTES)
def test_public_get_returns_200(client: TestClient, path: str) -> None:
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} => {resp.status_code}: {resp.text[:200]}"


@pytest.mark.parametrize("path", PROTECTED_GET_ROUTES_NO_AUTH)
def test_protected_get_without_auth_returns_401(client: TestClient, path: str) -> None:
    resp = client.get(path)
    assert resp.status_code in {401, 403}, (
        f"{path} expected 401/403, got {resp.status_code}: {resp.text[:200]}"
    )


@pytest.mark.parametrize("path", ADMIN_GET_ROUTES_WITH_AUTH)
def test_admin_get_with_auth_returns_200(
    client: TestClient, admin_token: tuple[str, uuid.UUID], path: str
) -> None:
    token, _ = admin_token
    resp = client.get(path, headers={"Authorization": f"Bearer {token}"})
    # 200 is the expected happy path. 5xx surfaces handler bugs we want to catch.
    assert resp.status_code == 200, (
        f"{path} expected 200, got {resp.status_code}: {resp.text[:300]}"
    )
