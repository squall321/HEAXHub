# PAT 발급·검증·폐기 테스트 — API 의존성과 /authz forward_auth 양 경로 모두 검증.
"""Personal Access Token tests.

DB-backed (real PostgreSQL, skipped when unreachable — same posture as
test_api_smoke). Covers: issuance (plaintext once), listing (no plaintext),
API auth via Bearer PAT, /authz forward_auth via Bearer PAT, revocation,
expiry, and non-PAT fallback to the JWT path.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.security import PAT_PREFIX, create_access_token, hash_password
from app.db.base import Base
from app.db.models.app import App, AppStatus, AppType, AppVisibility, ExecutionTarget
from app.db.models.audit_log import AuditLog
from app.db.models.personal_access_token import PersonalAccessToken
from app.db.models.user import AuthSource, User, UserRole, UserStatus
from app.db.session import SessionLocal, engine
from app.main import app


def _delete_user_with_audit(session: Session, user_id: uuid.UUID) -> None:
    """시드 사용자 정리 — PAT 발급이 남긴 audit_log가 users FK를 잡고 있으므로 먼저 지운다."""
    session.query(AuditLog).filter(AuditLog.actor_user_id == user_id).delete()
    row = session.get(User, user_id)
    if row is not None:
        session.delete(row)
    session.commit()


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="database unreachable")

# 새 테이블은 멱등 create_all 로 보장 (기존 테이블은 건드리지 않는다).
if _db_reachable():
    Base.metadata.create_all(engine, tables=[PersonalAccessToken.__table__])


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def seeded_user() -> Iterator[tuple[str, uuid.UUID]]:
    """일반 사용자 시드 → (access JWT, user_id). teardown에서 PAT까지 CASCADE 삭제."""
    session: Session = SessionLocal()
    email = f"pat-user-{uuid.uuid4().hex[:8]}@example.com"
    user = User(
        email=email,
        display_name="PAT Tester",
        organization="Test",
        password_hash=hash_password("StrongPa55word!"),
        auth_source=AuthSource.LOCAL,
        status=UserStatus.ACTIVE,
        role=UserRole.USER,
        email_verified=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    jwt_token = create_access_token(str(user.id))
    user_id = user.id
    session.close()
    try:
        yield jwt_token, user_id
    finally:
        session = SessionLocal()
        try:
            _delete_user_with_audit(session, user_id)
        finally:
            session.close()


@pytest.fixture()
def team_app() -> Iterator[str]:
    """authz 게이트 검증용 비공개(team) 앱 시드.

    TEAM 가시성은 소유자 조직 == 사용자 조직으로 판정되므로, seeded_user와 같은
    조직("Test")의 소유자를 함께 시딩한다 (동료의 앱에 PAT로 접근하는 실사용 형태).
    """
    session: Session = SessionLocal()
    owner = User(
        email=f"pat-owner-{uuid.uuid4().hex[:8]}@example.com",
        display_name="PAT App Owner",
        organization="Test",
        password_hash=hash_password("StrongPa55word!"),
        auth_source=AuthSource.LOCAL,
        status=UserStatus.ACTIVE,
        role=UserRole.USER,
        email_verified=True,
    )
    session.add(owner)
    session.commit()
    session.refresh(owner)
    owner_id = owner.id

    slug = f"pat_test_app_{uuid.uuid4().hex[:8]}"
    row = App(
        id=slug,
        name="PAT Test App",
        upstream_repo_url=f"file:///tmp/{slug}",
        workspace_path=f"/tmp/{slug}",
        app_type=AppType.WEB_APP,
        execution_target=ExecutionTarget.LINUX_RUNNER,
        visibility=AppVisibility.TEAM,
        status=AppStatus.BETA,
        owner_user_id=owner_id,
    )
    session.add(row)
    session.commit()
    session.close()
    try:
        yield slug
    finally:
        session = SessionLocal()
        try:
            row = session.get(App, slug)
            if row is not None:
                session.delete(row)
                session.commit()
            _delete_user_with_audit(session, owner_id)
        finally:
            session.close()


def _issue(client: TestClient, jwt_token: str, **body) -> dict:
    r = client.post(
        "/api/v1/auth/tokens",
        json={"name": "test-mcp", **body},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_issue_returns_plaintext_once_and_list_hides_it(client, seeded_user):
    jwt_token, _ = seeded_user
    created = _issue(client, jwt_token)
    assert created["token"].startswith(PAT_PREFIX)
    assert created["token_prefix"] == created["token"][:12]
    assert created["expires_at"] is None

    listed = client.get(
        "/api/v1/auth/tokens", headers={"Authorization": f"Bearer {jwt_token}"}
    ).json()
    assert len(listed) == 1
    assert "token" not in listed[0]
    assert listed[0]["token_prefix"] == created["token_prefix"]


def test_pat_authenticates_api(client, seeded_user):
    jwt_token, user_id = seeded_user
    pat = _issue(client, jwt_token)["token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {pat}"})
    assert me.status_code == 200
    assert me.json()["id"] == str(user_id)


def test_pat_passes_authz_forward_auth(client, seeded_user, team_app):
    jwt_token, _ = seeded_user
    pat = _issue(client, jwt_token)["token"]
    uri = f"/apps/{team_app}/mcp"

    anon = client.get("/api/v1/authz", headers={"X-Forwarded-Uri": uri})
    assert anon.status_code == 401

    ok = client.get(
        "/api/v1/authz",
        headers={"X-Forwarded-Uri": uri, "Authorization": f"Bearer {pat}"},
    )
    assert ok.status_code == 200


def test_revoked_pat_rejected_everywhere(client, seeded_user, team_app):
    jwt_token, _ = seeded_user
    created = _issue(client, jwt_token)
    pat = created["token"]

    r = client.delete(
        f"/api/v1/auth/tokens/{created['id']}",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert r.status_code == 204

    assert client.get("/api/v1/auth/me",
                      headers={"Authorization": f"Bearer {pat}"}).status_code == 401
    assert client.get(
        "/api/v1/authz",
        headers={"X-Forwarded-Uri": f"/apps/{team_app}/mcp",
                 "Authorization": f"Bearer {pat}"},
    ).status_code == 401


def test_expired_pat_rejected(client, seeded_user):
    jwt_token, _ = seeded_user
    created = _issue(client, jwt_token, expires_days=1)
    # 만료를 과거로 강제 (발급 API는 미래만 허용하므로 DB에서 직접).
    session = SessionLocal()
    row = session.get(PersonalAccessToken, uuid.UUID(created["id"]))
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    session.commit()
    session.close()

    r = client.get("/api/v1/auth/me",
                   headers={"Authorization": f"Bearer {created['token']}"})
    assert r.status_code == 401


def test_revoke_requires_owner(client, seeded_user):
    jwt_token, _ = seeded_user
    created = _issue(client, jwt_token)

    # 다른 사용자로는 폐기 불가 (404 — 존재 노출 최소화)
    session = SessionLocal()
    other = User(
        email=f"pat-other-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Other", organization="Test",
        password_hash=hash_password("StrongPa55word!"),
        auth_source=AuthSource.LOCAL, status=UserStatus.ACTIVE,
        role=UserRole.USER, email_verified=True,
    )
    session.add(other)
    session.commit()
    other_jwt = create_access_token(str(other.id))
    other_id = other.id
    session.close()
    try:
        r = client.delete(
            f"/api/v1/auth/tokens/{created['id']}",
            headers={"Authorization": f"Bearer {other_jwt}"},
        )
        assert r.status_code == 404
    finally:
        session = SessionLocal()
        row = session.get(User, other_id)
        if row is not None:
            session.delete(row)
            session.commit()
        session.close()


def test_non_pat_bearer_still_uses_jwt_path(client, seeded_user):
    jwt_token, user_id = seeded_user
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {jwt_token}"})
    assert me.status_code == 200 and me.json()["id"] == str(user_id)
    assert client.get("/api/v1/auth/me",
                      headers={"Authorization": "Bearer heax_pat_not_a_real_token"}).status_code == 401
