"""Tests for POST /api/v1/submissions/{id}/publish.

Covers:
  - 401/403 for anonymous and non-admin users
  - 409 when submission status is not BUILT
  - 200 happy path: flips Submission to PUBLISHED + App to STABLE + writes published_at
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.db.models.app import App, AppStatus, AppType, AppVisibility, ExecutionTarget
from app.db.models.app_version import AppVersion, BuildStatus
from app.db.models.submission import Submission, SubmissionStatus
from app.db.models.user import AuthSource, User, UserRole, UserStatus
from app.db.session import SessionLocal, engine
from app.main import app as fastapi_app


def _db_reachable() -> bool:
    try:
        with engine.connect():
            return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(fastapi_app) as c:
        yield c


@pytest.fixture()
def admin_and_user() -> Iterator[tuple[str, str, uuid.UUID, uuid.UUID]]:
    """Seed (admin_token, regular_token, admin_id, regular_id)."""
    if not _db_reachable():
        pytest.skip("database unreachable")

    db: Session = SessionLocal()
    admin = User(
        email=f"pub-admin-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Pub Admin",
        organization="Test",
        password_hash=hash_password("StrongPa55word!"),
        auth_source=AuthSource.LOCAL,
        status=UserStatus.ACTIVE,
        role=UserRole.ADMIN,
        email_verified=True,
    )
    user = User(
        email=f"pub-user-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Pub User",
        organization="Test",
        password_hash=hash_password("StrongPa55word!"),
        auth_source=AuthSource.LOCAL,
        status=UserStatus.ACTIVE,
        role=UserRole.USER,
        email_verified=True,
    )
    db.add_all([admin, user])
    db.commit()
    db.refresh(admin)
    db.refresh(user)
    admin_token = create_access_token(str(admin.id))
    user_token = create_access_token(str(user.id))
    admin_id = admin.id
    user_id = user.id
    db.close()

    try:
        yield admin_token, user_token, admin_id, user_id
    finally:
        from sqlalchemy import text as _text
        db = SessionLocal()
        try:
            # publish writes to audit_log → break FK before user delete
            for uid in (admin_id, user_id):
                db.execute(
                    _text("DELETE FROM audit_log WHERE actor_user_id = :uid"),
                    {"uid": str(uid)},
                )
                row = db.get(User, uid)
                if row is not None:
                    db.delete(row)
            db.commit()
        finally:
            db.close()


def _seed_submission_with_app(
    db: Session, *, submitter_id: uuid.UUID, status: SubmissionStatus
) -> tuple[Submission, App, AppVersion]:
    slug = f"pub_test_{uuid.uuid4().hex[:8]}"
    app_row = App(
        id=slug,
        name="Pub Test App",
        owner_user_id=submitter_id,
        status=AppStatus.DRAFT,
        app_type=AppType.CLI_TOOL,
        execution_target=ExecutionTarget.LINUX_RUNNER,
        upstream_repo_url="https://example.com/dummy.git",
        workspace_path=f"./app_workspaces/{slug}",
        visibility=AppVisibility.TEAM,
    )
    db.add(app_row)
    db.flush()
    version = AppVersion(
        id=uuid.uuid4(),
        app_id=slug,
        version="0.1.0",
        build_status=BuildStatus.SUCCESS,
        manifest_snapshot={},
    )
    db.add(version)
    db.flush()
    app_row.current_version_id = version.id
    sub = Submission(
        id=uuid.uuid4(),
        submitter_user_id=submitter_id,
        proposed_app_id=slug,
        name="Pub Test",
        upstream_repo_url="https://example.com/dummy.git",
        status=status,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    db.refresh(app_row)
    return sub, app_row, version


def _cleanup(db: Session, sub: Submission | None, app_row: App | None) -> None:
    if sub is not None:
        row = db.get(Submission, sub.id)
        if row is not None:
            db.delete(row)
    if app_row is not None:
        # break FK to current_version_id, then delete versions, then app.
        a = db.get(App, app_row.id)
        if a is not None:
            a.current_version_id = None
            db.flush()
            for v in db.query(AppVersion).filter(AppVersion.app_id == a.id).all():
                db.delete(v)
            db.delete(a)
    db.commit()


def test_publish_requires_auth(client: TestClient) -> None:
    resp = client.post(f"/api/v1/submissions/{uuid.uuid4()}/publish")
    assert resp.status_code in {401, 403}, resp.text


def test_publish_requires_admin(
    client: TestClient, admin_and_user: tuple[str, str, uuid.UUID, uuid.UUID]
) -> None:
    _, user_token, _, _ = admin_and_user
    resp = client.post(
        f"/api/v1/submissions/{uuid.uuid4()}/publish",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert resp.status_code == 403, resp.text


def test_publish_requires_built_status(
    client: TestClient, admin_and_user: tuple[str, str, uuid.UUID, uuid.UUID]
) -> None:
    admin_token, _, admin_id, _ = admin_and_user
    db = SessionLocal()
    sub, app_row, _ = _seed_submission_with_app(
        db, submitter_id=admin_id, status=SubmissionStatus.PENDING
    )
    sid = sub.id
    db.close()
    try:
        resp = client.post(
            f"/api/v1/submissions/{sid}/publish",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 409, resp.text
    finally:
        db = SessionLocal()
        sub2 = db.get(Submission, sid)
        if sub2 is not None:
            _cleanup(db, sub2, db.get(App, app_row.id))
        db.close()


def test_publish_happy_path_flips_states(
    client: TestClient, admin_and_user: tuple[str, str, uuid.UUID, uuid.UUID]
) -> None:
    admin_token, _, admin_id, _ = admin_and_user
    db = SessionLocal()
    sub, app_row, _ = _seed_submission_with_app(
        db, submitter_id=admin_id, status=SubmissionStatus.BUILT
    )
    sid = sub.id
    app_id = app_row.id
    db.close()
    try:
        resp = client.post(
            f"/api/v1/submissions/{sid}/publish",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "published"
        assert body["published_at"] is not None

        db = SessionLocal()
        sub2 = db.get(Submission, sid)
        app2 = db.get(App, app_id)
        assert sub2.status == SubmissionStatus.PUBLISHED
        assert sub2.published_at is not None
        assert sub2.reviewer_user_id == admin_id
        assert app2.status == AppStatus.STABLE
        db.close()
    finally:
        db = SessionLocal()
        sub2 = db.get(Submission, sid)
        app2 = db.get(App, app_id)
        if sub2 is not None and app2 is not None:
            _cleanup(db, sub2, app2)
        db.close()
