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

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import get_settings
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
def test_two_daemons_hosted_simultaneously(db: Session) -> None:
    """Spin up demo-a + demo-b, hit both through Caddy, then tear down."""
    base = _public_base()
    app_ids = ["demo-a", "demo-b"]
    handles: list[service_manager_dev.DevServiceHandle] = []

    # Pre-clean any stale routes from a previous interrupted run.
    for app_id in app_ids:
        proxy_manager.unregister_app_route(app_id)

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

    # After cleanup the routes must be gone.
    routes = proxy_manager.list_routes()
    route_ids = {r.get("@id") for r in routes if isinstance(r, dict)}
    for app_id in app_ids:
        assert f"app-{app_id}" not in route_ids, (
            f"route app-{app_id} not cleaned up; routes={route_ids}"
        )
