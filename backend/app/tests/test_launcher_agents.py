"""Tests for the launcher_agents router (NEXT_STEPS §2.4).

Drives the real FastAPI app via TestClient, but overrides ``get_db`` with a
savepoint-bound session so every write rolls back. Skips when Postgres is
unreachable.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.security import create_access_token
from app.db.models.user import AuthSource, User, UserRole, UserStatus
from app.db.session import engine, get_db
from app.main import app as fastapi_app
from app.services import agent_registry

ENROLL = "/api/v1/launcher-agents/enroll"
REFRESH = "/api/v1/launcher-agents/refresh"
MANIFEST = "/api/v1/launcher-agents/manifest"


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


def _register_launcher(session: Session, name: str) -> str:
    _agent, token = agent_registry.register_agent(
        session, name=name, pool="hwax-launcher", device_kind="launcher"
    )
    return token


def _make_user(session: Session) -> User:
    user = User(
        email="launcher-router-test@example.com",
        display_name="Router Test",
        organization="t",
        password_hash="x",
        auth_source=AuthSource.LOCAL,
        email_verified=True,
        status=UserStatus.ACTIVE,
        role=UserRole.USER,
    )
    session.add(user)
    session.flush()
    return user


def _enroll(client: TestClient, token: str, **body) -> dict:
    resp = client.post(ENROLL, json={"enrollment_token": token, **body})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── enroll ──────────────────────────────────────────────────────────────────────


def test_enroll_happy_path_and_access_token_works(ctx) -> None:
    session, client = ctx
    token = _register_launcher(session, "router-enroll-1")

    body = _enroll(client, token, hostname="ws-router", agent_version="0.2.0")
    # Response is exactly the contract EnrollmentResult (additionalProperties:false).
    assert set(body) == {"agent_id", "access_token", "refresh_token", "expires_in"}
    assert body["expires_in"] == 3600

    m = client.get(MANIFEST, headers={"Authorization": f"Bearer {body['access_token']}"})
    assert m.status_code == 200, m.text
    assert m.json()["schema_version"] == 1


def test_enroll_unknown_token_401(ctx) -> None:
    _session, client = ctx
    resp = client.post(ENROLL, json={"enrollment_token": "nope-not-real"})
    assert resp.status_code == 401, resp.text


def test_enroll_service_agent_403(ctx) -> None:
    session, client = ctx
    _agent, token = agent_registry.register_agent(
        session, name="router-svc-1", pool="p", device_kind="service"
    )
    resp = client.post(ENROLL, json={"enrollment_token": token})
    assert resp.status_code == 403, resp.text


def test_enroll_rejects_extra_field_422(ctx) -> None:
    _session, client = ctx
    resp = client.post(ENROLL, json={"enrollment_token": "x", "bogus": 1})
    assert resp.status_code == 422, resp.text


def test_enroll_burns_token_second_call_401(ctx) -> None:
    session, client = ctx
    token = _register_launcher(session, "router-enroll-burn")
    _enroll(client, token)
    resp = client.post(ENROLL, json={"enrollment_token": token})
    assert resp.status_code == 401, resp.text


# ── refresh ─────────────────────────────────────────────────────────────────────


def test_refresh_rotates(ctx) -> None:
    session, client = ctx
    token = _register_launcher(session, "router-refresh-1")
    first = _enroll(client, token)

    resp = client.post(REFRESH, json={"refresh_token": first["refresh_token"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"access_token", "refresh_token", "expires_in"}
    # jti rotates ⇒ a brand-new refresh token (access may share an iat-second).
    assert body["refresh_token"] != first["refresh_token"]

    m = client.get(MANIFEST, headers={"Authorization": f"Bearer {body['access_token']}"})
    assert m.status_code == 200, m.text


def test_refresh_reuse_detected_401(ctx) -> None:
    session, client = ctx
    token = _register_launcher(session, "router-refresh-reuse")
    first = _enroll(client, token)
    client.post(REFRESH, json={"refresh_token": first["refresh_token"]})  # rotate once
    # Replaying the now-revoked refresh token is rejected.
    resp = client.post(REFRESH, json={"refresh_token": first["refresh_token"]})
    assert resp.status_code == 401, resp.text


# ── audience isolation (both directions) ─────────────────────────────────────────


def test_manifest_requires_bearer_401(ctx) -> None:
    _session, client = ctx
    assert client.get(MANIFEST).status_code == 401


def test_manifest_rejects_user_token_401(ctx) -> None:
    session, client = ctx
    user = _make_user(session)
    user_token = create_access_token(str(user.id))  # no audience
    resp = client.get(MANIFEST, headers={"Authorization": f"Bearer {user_token}"})
    assert resp.status_code == 401, resp.text


def test_launcher_token_rejected_on_user_route_401(ctx) -> None:
    session, client = ctx
    token = _register_launcher(session, "router-crossguard")
    body = _enroll(client, token)
    # The reverse guard: an aud-scoped launcher token must not pass get_current_user.
    resp = client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert resp.status_code == 401, resp.text


# ── conditional GET (ETag / 304) ──────────────────────────────────────────────


def test_manifest_etag_then_304(ctx) -> None:
    session, client = ctx
    token = _register_launcher(session, "router-etag-1")
    body = _enroll(client, token)
    h = {"Authorization": f"Bearer {body['access_token']}"}

    first = client.get(MANIFEST, headers=h)
    assert first.status_code == 200, first.text
    etag = first.headers.get("etag")
    assert etag, "manifest must set an ETag"

    # Unchanged catalog + matching If-None-Match → 304, no body.
    second = client.get(MANIFEST, headers={**h, "If-None-Match": etag})
    assert second.status_code == 304, second.text
    assert second.content == b""


# installs / audit / heartbeat are now real endpoints — see test_launcher_reports.py.
