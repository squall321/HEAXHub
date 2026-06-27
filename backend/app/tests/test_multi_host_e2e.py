"""End-to-end smoke test for the multi-host web_app hosting flow.

This test verifies that two independent fixture daemons can be brought up
simultaneously, get distinct ports from :mod:`port_allocator`, get their
``/apps/{app_id}/*`` routes installed in the live Caddy instance, and are
reachable + distinguishable via the public Caddy port.

Skip rules:
  * Skip when the database is unreachable — needs port_allocator persistence.
  * Skip when the Caddy admin API at ``CADDY_ADMIN_URL`` is not responding —
    Caddy is provisioned by ``deploy/apptainer/start.sh`` but may be absent in
    minimal CI environments.

The test never reuses production App rows; it drives the dev helper
:mod:`app.services.service_manager_dev` directly.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.app import (
    App,
    AppStatus,
    AppType,
    AppVisibility,
    ExecutionTarget,
)
from app.db.models.user import AuthSource, User, UserRole, UserStatus
from app.db.session import engine
from app.services import proxy_manager, service_manager_dev


REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_DIR = REPO_ROOT / "templates" / "streamlit-hello"


# ─── Skip guards ─────────────────────────────────────────────────────────────


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


def _caddy_reachable() -> bool:
    admin = get_settings().caddy_admin_url.rstrip("/")
    try:
        resp = httpx.get(f"{admin}/config/", timeout=1.0)
        return resp.status_code < 500
    except httpx.HTTPError:
        return False


def _authz_reachable() -> bool:
    """A live, current backend with the ``forward_auth`` gate endpoint must be
    serving ``/api/v1/authz`` — every ``/apps/*`` route now subrequests it, so
    without it Caddy blocks all public hits. A no-slug probe returns 200 on a
    current build; a stale build (missing the route) returns 404 → skip."""
    settings = get_settings()
    url = f"{settings.app_base_url.rstrip('/')}/api/v1/authz"
    try:
        return httpx.get(url, timeout=1.0).status_code == 200
    except httpx.HTTPError:
        return False


def _public_base() -> str:
    settings = get_settings()
    return f"http://{settings.public_host}:{settings.public_port}"


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def db() -> Iterator[Session]:
    if not _db_reachable():
        pytest.skip("database unreachable; skipping multi-host E2E")
    # NOTE: unlike test_common_infra.py we cannot use a savepoint-rolled-back
    # session here, because port_allocator commits port rows that the *child*
    # processes need to outlive the test. We therefore use a plain session and
    # rely on stop_dev_service to release each port we allocated.
    session = Session(bind=engine)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _require_template_dir() -> None:
    if not (TEMPLATE_DIR / ".portal" / "run.sh").exists():
        pytest.skip(f"streamlit-hello fixture missing at {TEMPLATE_DIR}")


# ─── The actual E2E ──────────────────────────────────────────────────────────


@pytest.mark.skipif(not _caddy_reachable(), reason="Caddy admin API unreachable")
@pytest.mark.skipif(
    not _authz_reachable(),
    reason="forward_auth gate endpoint /api/v1/authz unreachable; "
    "every /apps/* route is gated through it",
)
def test_two_daemons_hosted_simultaneously(db: Session) -> None:
    """Spin up demo-a + demo-b, hit both through Caddy, then tear down."""
    base = _public_base()
    app_ids = ["demo-a", "demo-b"]
    handles: list[service_manager_dev.DevServiceHandle] = []

    # Pre-clean any stale routes from a previous interrupted run.
    for app_id in app_ids:
        proxy_manager.unregister_app_route(app_id)

    # SEC-03: 이제 모든 /apps/* 가 forward_auth 게이트를 거친다. 라우팅 자체를
    # 검증하려면 대상 앱이 공개(COMPANY+STABLE)여야 쿠키 없이 200 으로 통과한다.
    # 테스트용 owner + App 행을 시드하고 finally 에서 정리한다.
    owner = User(
        email=f"e2e-{uuid.uuid4().hex[:8]}@example.com",
        display_name="E2E",
        organization="Test",
        password_hash="x",
        auth_source=AuthSource.LOCAL,
        status=UserStatus.ACTIVE,
        role=UserRole.USER,
        email_verified=True,
    )
    db.add(owner)
    db.commit()
    db.refresh(owner)
    seeded_apps: list[App] = []
    for app_id in app_ids:
        seeded_apps.append(
            App(
                id=app_id,
                name=app_id,
                owner_user_id=owner.id,
                app_type=AppType.WEB_APP,
                execution_target=ExecutionTarget.LINUX_RUNNER,
                status=AppStatus.STABLE,
                visibility=AppVisibility.COMPANY,
                upstream_repo_url="https://example.com/repo.git",
                workspace_path=f"/tmp/{app_id}",
            )
        )
    db.add_all(seeded_apps)
    db.commit()

    try:
        for app_id in app_ids:
            try:
                handle = service_manager_dev.start_dev_service(
                    db,
                    app_id=app_id,
                    template_dir=TEMPLATE_DIR,
                    health_path="/healthz",
                    health_timeout_seconds=15.0,
                )
            except RuntimeError as exc:
                # The allocated port collided with a system process (e.g.
                # node_exporter on 9100 in this lab environment). That is an
                # environmental constraint, not a defect under test.
                log_path = TEMPLATE_DIR / ".portal" / "logs" / f"{app_id}.log"
                if log_path.exists() and "Address already in use" in log_path.read_text():
                    pytest.skip(
                        f"allocated port for {app_id} collides with a system "
                        f"process; skipping multi-host E2E"
                    )
                raise
            handles.append(handle)

        # Sanity: distinct ports.
        ports = {h.port for h in handles}
        assert len(ports) == len(handles), f"port collision: {[h.port for h in handles]}"

        # Public access — each route returns its own body.
        with httpx.Client(timeout=5.0, follow_redirects=True) as client:
            for handle in handles:
                resp = client.get(f"{base}/apps/{handle.app_id}/")
                assert resp.status_code == 200, (
                    f"{handle.app_id}: status={resp.status_code} body={resp.text[:200]}"
                )
                assert handle.app_id in resp.text, (
                    f"{handle.app_id}: body did not contain app_id\n{resp.text[:200]}"
                )

            # Caddy lists both routes.
            routes = proxy_manager.list_routes()
            route_ids = {r.get("@id") for r in routes if isinstance(r, dict)}
            for app_id in app_ids:
                assert f"app-{app_id}" in route_ids, (
                    f"route app-{app_id} missing; got {route_ids}"
                )

    finally:
        for handle in handles:
            try:
                service_manager_dev.stop_dev_service(db, handle)
            except Exception:  # pragma: no cover — best-effort cleanup
                pass
        # 시드한 App/owner 행 정리.
        for obj in [*seeded_apps, owner]:
            try:
                db.delete(db.merge(obj))
            except Exception:  # pragma: no cover — best-effort cleanup
                pass
        db.commit()

    # After cleanup the routes must be gone.
    routes = proxy_manager.list_routes()
    route_ids = {r.get("@id") for r in routes if isinstance(r, dict)}
    for app_id in app_ids:
        assert f"app-{app_id}" not in route_ids, (
            f"route app-{app_id} not cleaned up; routes={route_ids}"
        )
