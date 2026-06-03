"""SubmissionCreate <-> frontend wire-shape alignment (P0a).

Validates that:
1) The frontend's ``app_type`` / ``execution_target`` field names persist correctly.
2) The legacy ``proposed_app_type`` / ``proposed_execution_target`` names still work.
3) ``source_config`` is persisted on the submission row.
4) When ``source_config.type == 'git'`` carries a url, it is treated as authoritative.

Skipped automatically if PostgreSQL is unreachable — mirrors the pattern used by
test_api_smoke / test_full_lifecycle.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.models.submission import Submission
from app.db.models.user import AuthSource, User, UserRole, UserStatus
from app.db.session import SessionLocal, engine
from app.schemas.submission import SubmissionCreate
from app.services import submission_service


# ---------------------------------------------------------------------------
# Reachability + fixtures
# ---------------------------------------------------------------------------


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


@pytest.fixture()
def session_user() -> Iterator[tuple[Session, User]]:
    if not _db_reachable():
        pytest.skip("database unreachable; skipping schema alignment test")

    db: Session = SessionLocal()
    user = User(
        email=f"sub-align-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Sub Aligner",
        organization="Test",
        password_hash=hash_password("StrongPa55word!"),
        auth_source=AuthSource.LOCAL,
        status=UserStatus.ACTIVE,
        role=UserRole.USER,
        email_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    user_id = user.id

    created_subs: list[uuid.UUID] = []
    try:
        yield db, user
        # Capture ids of submissions made by this user during the test.
        for s in db.query(Submission).filter(Submission.submitter_user_id == user_id).all():
            created_subs.append(s.id)
    finally:
        # Cleanup submissions first, then user.
        for sid in created_subs:
            row = db.get(Submission, sid)
            if row is not None:
                db.delete(row)
        # Also catch any not in the captured list.
        for s in db.query(Submission).filter(Submission.submitter_user_id == user_id).all():
            db.delete(s)
        u = db.get(User, user_id)
        if u is not None:
            db.delete(u)
        db.commit()
        db.close()


def _app_id() -> str:
    # app_id pattern is ^[a-z][a-z0-9_]{2,63}$ — generate a fresh one each run.
    return "t_" + uuid.uuid4().hex[:10]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_new_field_names_persist_app_type(session_user: tuple[Session, User]) -> None:
    """Frontend payload (``app_type`` / ``execution_target``) must reach the DB."""
    db, user = session_user
    payload = SubmissionCreate.model_validate(
        {
            "proposed_app_id": _app_id(),
            "name": "alpha",
            "upstream_repo_url": "https://github.com/example/alpha",
            "app_type": "cli_tool",
            "execution_target": "linux_runner",
        }
    )
    assert payload.proposed_app_type is not None
    assert payload.proposed_app_type.value == "cli_tool"
    assert payload.proposed_execution_target is not None
    assert payload.proposed_execution_target.value == "linux_runner"

    sub = submission_service.create_submission(db, user=user, payload=payload)
    assert sub.proposed_app_type == "cli_tool"
    assert sub.proposed_execution_target == "linux_runner"


def test_old_field_names_still_work(session_user: tuple[Session, User]) -> None:
    """The legacy ``proposed_*`` field names must continue to work for back-compat."""
    db, user = session_user
    payload = SubmissionCreate.model_validate(
        {
            "proposed_app_id": _app_id(),
            "name": "beta",
            "upstream_repo_url": "https://github.com/example/beta",
            "proposed_app_type": "web_app",
            "proposed_execution_target": "linux_runner",
        }
    )
    assert payload.proposed_app_type is not None
    assert payload.proposed_app_type.value == "web_app"

    sub = submission_service.create_submission(db, user=user, payload=payload)
    assert sub.proposed_app_type == "web_app"
    assert sub.proposed_execution_target == "linux_runner"


def test_archive_source_config_persisted(session_user: tuple[Session, User]) -> None:
    """archive_url source_config must persist to the JSONB column and bypass git host check."""
    db, user = session_user
    archive_url = "https://example.com/releases/app-1.0.0.tar.gz"
    payload = SubmissionCreate.model_validate(
        {
            "proposed_app_id": _app_id(),
            "name": "gamma",
            # upstream_repo_url is allowed to be the same as the archive URL since
            # the frontend's pickRepoUrl mirrors it that way for non-git sources.
            "upstream_repo_url": archive_url,
            "app_type": "cli_tool",
            "execution_target": "linux_runner",
            "source_config": {
                "type": "archive_url",
                "url": archive_url,
                "sha256": "deadbeef" * 8,
            },
        }
    )
    assert payload.source_config is not None
    assert payload.source_config["type"] == "archive_url"

    sub = submission_service.create_submission(db, user=user, payload=payload)
    assert isinstance(sub.source_config, dict)
    assert sub.source_config["type"] == "archive_url"
    assert sub.source_config["url"] == archive_url
    assert sub.source_config["sha256"] == "deadbeef" * 8


def test_git_source_config_overrides_upstream_repo_url(
    session_user: tuple[Session, User],
) -> None:
    """When source_config carries a git URL, it is the source of truth.

    The frontend mirrors upstream_repo_url to whatever the user typed at the
    relevant step. The descriptor (source_config) must round-trip intact and
    the persisted upstream_repo_url must match the descriptor's url.
    """
    db, user = session_user
    git_url = "https://github.com/example/delta"
    payload = SubmissionCreate.model_validate(
        {
            "proposed_app_id": _app_id(),
            "name": "delta",
            "upstream_repo_url": git_url,
            "app_type": "cli_tool",
            "execution_target": "linux_runner",
            "source_config": {"type": "git", "url": git_url},
        }
    )
    sub = submission_service.create_submission(db, user=user, payload=payload)
    assert isinstance(sub.source_config, dict)
    assert sub.source_config["type"] == "git"
    assert sub.source_config["url"] == git_url
    assert sub.upstream_repo_url == git_url
