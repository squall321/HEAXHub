"""Tests for GET /api/v1/installers/{app_id}/latest — the Tauri updater feed
(NEXT_STEPS follow-up; contract openapi /installers/{app_id}/latest).

Public endpoint (no bearer). Drives the real app via TestClient with a savepoint
get_db override; writes a real minisign .sig under installer_storage_root and
cleans it up. Skips when Postgres is unreachable.
"""
from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.models.installer_package import InstallerPackage
from app.db.session import engine, get_db
from app.main import app as fastapi_app
from app.services import installer_packages

SHA = "c" * 64
SIG = "untrusted comment: signature from tauri\nRWQ_FAKE_BASE64_MINISIGN_SIG_DATA==\n"


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


def _make_pkg(
    session: Session, *, app_id: str, version: str, uploaded_at: datetime | None = None
) -> InstallerPackage:
    row = InstallerPackage(
        app_id=app_id,
        version=version,
        os="windows-x64",
        installer_url=f"/api/v1/apps/{app_id}/installers/windows-x64/{version}",
        sha256=SHA,
        signed=True,
        # Set explicitly: Postgres now() is transaction-time, so two rows in the
        # same savepoint would otherwise share an uploaded_at and make
        # get_latest's ordering ambiguous.
        uploaded_at=uploaded_at or datetime.now(timezone.utc),
    )
    session.add(row)
    session.flush()
    return row


def _write_sig(app_id: str, version: str) -> None:
    d = installer_packages.installer_dir(app_id, "windows-x64", version)
    d.mkdir(parents=True, exist_ok=True)
    installer_packages.signature_path(app_id, "windows-x64", version).write_text(
        SIG, encoding="utf-8"
    )


def _rm(app_id: str, version: str) -> None:
    d = installer_packages.installer_dir(app_id, "windows-x64", version)
    shutil.rmtree(d.parents[1], ignore_errors=True)  # storage_root/app_id


def _feed(app_id: str) -> str:
    return f"/api/v1/installers/{app_id}/latest"


# ── 204 cases ────────────────────────────────────────────────────────────────────


def test_feed_204_when_no_build(ctx) -> None:
    _session, client = ctx
    resp = client.get(_feed("hwax-agent"))
    assert resp.status_code == 204, resp.text


def test_feed_204_when_no_signature(ctx) -> None:
    session, client = ctx
    _make_pkg(session, app_id="feed-nosig", version="1.0.0")
    # No .sig on disk → nothing signature-verifiable to offer.
    resp = client.get(_feed("feed-nosig"))
    assert resp.status_code == 204, resp.text


# ── 200 happy path ────────────────────────────────────────────────────────────────


def test_feed_returns_tauri_shape(ctx) -> None:
    session, client = ctx
    app_id, version = "feed-ok", "1.2.3"
    row = _make_pkg(session, app_id=app_id, version=version)
    _write_sig(app_id, version)
    try:
        resp = client.get(_feed(app_id))  # public — no bearer
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["version"] == version
        assert set(body["platforms"].keys()) == {"windows-x86_64"}
        plat = body["platforms"]["windows-x86_64"]
        assert set(plat.keys()) == {"signature", "url"}
        assert plat["signature"] == SIG.strip()
        assert plat["url"].endswith(f"/api/v1/installers/{row.id}/download")
        assert "pub_date" in body
    finally:
        _rm(app_id, version)


def test_feed_picks_latest_version(ctx) -> None:
    session, client = ctx
    app_id = "feed-latest"
    now = datetime.now(timezone.utc)
    _make_pkg(session, app_id=app_id, version="1.0.0", uploaded_at=now - timedelta(days=2))
    _make_pkg(session, app_id=app_id, version="2.0.0", uploaded_at=now)  # newer
    _write_sig(app_id, "2.0.0")
    try:
        resp = client.get(_feed(app_id))
        assert resp.status_code == 200, resp.text
        assert resp.json()["version"] == "2.0.0"
    finally:
        _rm(app_id, "2.0.0")
        _rm(app_id, "1.0.0")
