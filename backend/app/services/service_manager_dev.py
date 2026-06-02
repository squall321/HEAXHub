"""Test/dev helper for driving the multi-host E2E flow end-to-end.

The production :mod:`app.services.service_manager` orchestrates daemon launch
through the App/AppVersion ORM, manifest snapshots and the workspace tree.
For the multi-host smoke tests we want to exercise the same *integration*
points — port_allocator, proxy_manager, and a real long-running child
process — but without spinning up Postgres rows, builds, or full workspaces.

This module is intentionally narrow:

  * :func:`start_dev_service` allocates a port, spawns ``bash run.sh`` for the
    given template directory with the runtime contract env vars set, registers
    a Caddy route for ``/apps/{app_id}/*``, waits for the health endpoint, and
    returns a :class:`DevServiceHandle`.
  * :func:`stop_dev_service` SIGTERMs the process, unregisters the Caddy route,
    and releases the port back to the allocator.

Only used by tests. Production code paths stay in ``service_manager.py``.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.services import port_allocator, proxy_manager

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DevServiceHandle:
    """Bookkeeping for one running dev fixture instance."""

    app_id: str
    port: int
    proc: subprocess.Popen[bytes]
    root_path: str
    log_path: Path


def _wait_for_health(
    *, port: int, path: str, timeout_seconds: float = 10.0
) -> bool:
    """Poll the upstream's health endpoint directly (bypassing Caddy)."""
    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{port}{path}"
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=1.0)
            if resp.status_code < 500:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    return False


def start_dev_service(
    db: Session,
    *,
    app_id: str,
    template_dir: Path,
    health_path: str = "/healthz",
    log_dir: Path | None = None,
    health_timeout_seconds: float = 10.0,
) -> DevServiceHandle:
    """Start one daemon instance from ``template_dir/.portal/run.sh``.

    The launcher receives ``APP_ID``, ``PORT``, ``BIND_HOST`` and ``ROOT_PATH``
    via the environment, matching the contract documented in the streamlit-hello
    fixture and used by the production service_manager.

    Raises :class:`RuntimeError` if the process exits before reporting healthy
    or if the health endpoint never responds within ``health_timeout_seconds``.
    """
    run_script = template_dir / ".portal" / "run.sh"
    if not run_script.exists():
        raise FileNotFoundError(f"run.sh missing: {run_script}")

    # NOTE: pass app_id=None to port_allocator because port_allocations.app_id
    # carries a FK into apps.id. This dev helper deliberately bypasses the
    # App/AppVersion ORM (see module docstring), so seeding an apps row would
    # contradict the helper's purpose. The Python-side handle still carries
    # app_id for routing/cleanup; release_port only needs the port number.
    port = port_allocator.allocate_port(db, scope="app", app_id=None)
    root_path = f"/apps/{app_id}"

    env = os.environ.copy()
    env["APP_ID"] = app_id
    env["PORT"] = str(port)
    env["BIND_HOST"] = "127.0.0.1"
    env["ROOT_PATH"] = root_path

    log_dir = log_dir or template_dir / ".portal" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{app_id}.log"
    log_fh = log_path.open("ab", buffering=0)

    proc = subprocess.Popen(  # noqa: S603
        ["bash", str(run_script)],
        cwd=str(template_dir),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    # Make sure the process didn't crash immediately (eg. PORT collision).
    time.sleep(0.1)
    if proc.poll() is not None:
        port_allocator.release_port(db, port)
        raise RuntimeError(
            f"dev service exited immediately (rc={proc.returncode}); see {log_path}"
        )

    if not _wait_for_health(
        port=port, path=health_path, timeout_seconds=health_timeout_seconds
    ):
        _terminate(proc)
        port_allocator.release_port(db, port)
        raise RuntimeError(
            f"dev service {app_id} never reached healthy; see {log_path}"
        )

    proxy_result = proxy_manager.register_app_route(
        app_id=app_id, port=port, base_path=root_path
    )
    if not proxy_result.ok:
        # Caddy unreachable — the test fixture should skip rather than fail.
        _terminate(proc)
        port_allocator.release_port(db, port)
        raise RuntimeError(
            f"Caddy register_app_route failed for {app_id}: {proxy_result.reason}"
        )

    logger.info(
        "dev service started app_id=%s port=%d pid=%d root_path=%s",
        app_id,
        port,
        proc.pid,
        root_path,
    )
    return DevServiceHandle(
        app_id=app_id, port=port, proc=proc, root_path=root_path, log_path=log_path
    )


def stop_dev_service(db: Session, handle: DevServiceHandle) -> None:
    """SIGTERM the daemon, drop the Caddy route, release the port. Idempotent."""
    proxy_manager.unregister_app_route(handle.app_id)
    _terminate(handle.proc)
    port_allocator.release_port(db, handle.port)
    logger.info("dev service stopped app_id=%s", handle.app_id)


def _terminate(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
