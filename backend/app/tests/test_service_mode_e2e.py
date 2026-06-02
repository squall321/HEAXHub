"""End-to-end test for ``launch.mode: service`` via the production
:mod:`app.services.service_manager`.

The flow under test:

  1. Seed an App + AppVersion that mirrors the ``streamlit-hello`` fixture
     manifest (``launch.mode: service``, health at ``/healthz``).
  2. ``service_manager.start_service`` spawns the daemon, allocates a port,
     and registers a Caddy route at ``/apps/{app_id}/*``.
  3. The route is reachable through Caddy's public listener and the upstream
     responds with HTML that contains the app id.
  4. ``service_manager.stop_service`` SIGTERMs the daemon, deletes the Caddy
     route, releases the port, and flips the ServiceInstance row to
     ``stopped``.

Skip conditions (kept generous so the test is harmless in minimal CI):
  * Postgres unreachable — port_allocator needs to commit a row.
  * Caddy admin API unreachable — start_service quietly skips route
    registration when proxy_manager fails, but the assertion that a route
    appears would then fail, so we skip cleanly instead.

Marked ``integration`` so default ``pytest`` runs (which use
``-m 'not integration'`` per pyproject.toml) exclude it.
"""
from __future__ import annotations

import shutil
import time
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
from app.db.models.app_version import AppVersion, BuildStatus
from app.db.models.service_instance import ServiceInstance
from app.db.models.user import AuthSource, User, UserRole, UserStatus
from app.db.session import SessionLocal, engine
from app.services import proxy_manager, service_manager, workspace_manager

pytestmark = pytest.mark.integration


REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_DIR = REPO_ROOT / "templates" / "streamlit-hello"


# ─── Reachability ────────────────────────────────────────────────────────────


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


def _wait_for_status(
    db: Session, *, instance_id: uuid.UUID, predicate, timeout: float = 15.0
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        db.expire_all()
        inst = db.get(ServiceInstance, instance_id)
        if inst is not None and predicate(inst):
            return True
        time.sleep(0.2)
    return False


def _wait_for_health_port(port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/healthz"
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=1.0)
            if resp.status_code < 500:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    return False


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def db() -> Iterator[Session]:
    if not _db_reachable():
        pytest.skip("database unreachable; skipping service-mode E2E")
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _require_template_dir() -> None:
    if not (TEMPLATE_DIR / ".portal" / "run.sh").exists():
        pytest.skip(f"streamlit-hello fixture missing at {TEMPLATE_DIR}")


@pytest.fixture()
def admin_user(db: Session) -> Iterator[User]:
    email = f"svc-e2e-{uuid.uuid4().hex[:8]}@example.com"
    user = User(
        email=email,
        display_name="Service E2E",
        organization="Test",
        password_hash="x",
        auth_source=AuthSource.LOCAL,
        status=UserStatus.ACTIVE,
        role=UserRole.ADMIN,
        email_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    try:
        yield user
    finally:
        db.delete(user)
        db.commit()


@pytest.fixture()
def seeded_app(db: Session, admin_user: User) -> Iterator[tuple[App, AppVersion]]:
    """Materialize an App + AppVersion + workspace from the streamlit-hello fixture."""
    # workspace_manager._APP_ID_RE enforces ^[a-z][a-z0-9_]{2,63}$ so build a
    # compliant unique id from the test run.
    app_id = f"svce2e_{uuid.uuid4().hex[:8]}"
    workspace = workspace_manager.create_app_workspace(app_id)

    # Stage the fixture into upstream/ — start_service uses upstream/ as cwd
    # when present, and the default command is ``./.portal/run.sh``.
    upstream_portal = workspace / "upstream" / ".portal"
    upstream_portal.mkdir(parents=True, exist_ok=True)
    for name in ("run.sh", "manifest.yaml"):
        src = TEMPLATE_DIR / ".portal" / name
        if src.exists():
            dst = upstream_portal / name
            shutil.copy2(src, dst)
            if name == "run.sh":
                dst.chmod(0o755)

    manifest = {
        "schema_version": 2,
        "id": app_id,
        "name": "Streamlit Hello (svc e2e)",
        "version": "0.1.0",
        "app_type": "web_app",
        "execution_target": "linux_runner",
        "launch": {
            "mode": "service",
            "command": "./.portal/run.sh",
            "health_check": {
                "path": "/healthz",
                "interval_seconds": 5,
                "timeout_seconds": 3,
            },
            "restart_policy": {"policy": "no", "max_attempts": 0},
        },
        "permissions": {"visibility": "team"},
    }

    app = App(
        id=app_id,
        name=manifest["name"],
        description="service-mode E2E fixture",
        owner_user_id=admin_user.id,
        app_type=AppType.WEB_APP,
        execution_target=ExecutionTarget.LINUX_RUNNER,
        status=AppStatus.STABLE,
        visibility=AppVisibility.TEAM,
        upstream_repo_url=f"file://{TEMPLATE_DIR}",
        tags=["fixture", "service-e2e"],
        workspace_path=str(workspace),
    )
    db.add(app)

    version = AppVersion(
        app_id=app_id,
        version="0.1.0",
        manifest_snapshot=manifest,
        build_status=BuildStatus.SUCCESS,
    )
    db.add(version)
    db.flush()
    app.current_version_id = version.id
    db.commit()
    db.refresh(app)
    db.refresh(version)

    try:
        yield app, version
    finally:
        # Best-effort cleanup: instances -> app (cascades versions).
        try:
            db.query(ServiceInstance).filter(
                ServiceInstance.app_id == app_id
            ).delete(synchronize_session=False)
            db.commit()
        except Exception:
            db.rollback()
        try:
            row = db.get(App, app_id)
            if row is not None:
                row.current_version_id = None
                db.commit()
                db.delete(row)
                db.commit()
        except Exception:
            db.rollback()
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)


# ─── The actual E2E ──────────────────────────────────────────────────────────


@pytest.mark.skipif(not _caddy_reachable(), reason="Caddy admin API unreachable")
def test_service_mode_start_stop_through_caddy(
    db: Session, seeded_app: tuple[App, AppVersion]
) -> None:
    """start_service -> hit through Caddy -> stop_service -> verify cleanup."""
    app, version = seeded_app
    settings = get_settings()
    public_base = f"http://{settings.public_host}:{settings.public_port}"

    # Pre-clean any stale route from an interrupted previous run.
    proxy_manager.unregister_app_route(app.id)

    instance: ServiceInstance | None = None
    try:
        try:
            instance = service_manager.start_service(db, app=app, version=version)
        except RuntimeError as exc:  # pragma: no cover — env-specific
            pytest.skip(f"start_service raised RuntimeError: {exc}")

        assert instance.app_id == app.id
        assert instance.pid and instance.pid > 0
        # Port allocation is best-effort; without it Caddy can't be wired so
        # the test can't meaningfully verify routing.
        if instance.port is None:
            pytest.skip("port_allocator unavailable; cannot verify Caddy routing")

        # Wait until the daemon actually starts listening.
        if not _wait_for_health_port(instance.port, timeout=15.0):
            workdir = Path(instance.workdir) if instance.workdir else None
            log_hint = f" (logs near {workdir})" if workdir else ""
            pytest.fail(
                f"daemon never became healthy on 127.0.0.1:{instance.port}{log_hint}"
            )

        route_id = f"app-{app.id}"
        routes = proxy_manager.list_routes()
        ids = {r.get("@id") for r in routes if isinstance(r, dict)}
        assert route_id in ids, f"Caddy route {route_id} not present (got {ids})"

        # Public access through Caddy.
        with httpx.Client(timeout=5.0, follow_redirects=True) as client:
            resp = client.get(f"{public_base}/apps/{app.id}/")
            assert resp.status_code == 200, (
                f"GET via Caddy failed: status={resp.status_code} body={resp.text[:200]}"
            )
            assert app.id in resp.text, (
                f"response did not echo app_id; body={resp.text[:200]}"
            )

        # ----- stop & verify cleanup -----------------------------------------
        service_manager.stop_service(db, instance_id=instance.id)

        assert _wait_for_status(
            db, instance_id=instance.id, predicate=lambda i: i.status == "stopped"
        ), "ServiceInstance.status did not become 'stopped'"

        # Caddy route gone.
        routes_after = proxy_manager.list_routes()
        ids_after = {r.get("@id") for r in routes_after if isinstance(r, dict)}
        assert route_id not in ids_after, (
            f"route {route_id} not cleaned up; got {ids_after}"
        )

        # Public route returns non-2xx after teardown.
        with httpx.Client(timeout=2.0, follow_redirects=False) as client:
            try:
                resp_after = client.get(f"{public_base}/apps/{app.id}/")
                assert resp_after.status_code >= 400, (
                    f"expected 4xx/5xx after stop, got {resp_after.status_code}"
                )
            except httpx.HTTPError:
                # Caddy may close the connection — also acceptable.
                pass

    finally:
        # Defensive cleanup if the test exited mid-flight.
        if instance is not None:
            try:
                db.expire_all()
                inst = db.get(ServiceInstance, instance.id)
                if inst is not None and inst.status != "stopped":
                    service_manager.stop_service(db, instance_id=instance.id)
            except Exception:
                pass
        # Make sure the route is gone regardless of test outcome.
        try:
            proxy_manager.unregister_app_route(app.id)
        except Exception:
            pass
