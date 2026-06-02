"""Long-running service mode lifecycle (``launch.mode: service``).

Each ``ServiceInstance`` row tracks one daemon process (e.g. Streamlit /
Jupyter / dashboard). The manager handles start/stop/restart and is the entry
point used by the Celery health-check loop.

SA1 components (port_allocator, secret_manager, proxy_manager) are imported
lazily so this module still loads when they are not yet present in the tree.
"""
from __future__ import annotations

import os
import signal
import subprocess
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.logger import get_logger
from app.db.models.app import App, ExecutionTarget
from app.db.models.app_version import AppVersion
from app.db.models.service_instance import ServiceInstance
from app.services import (
    port_allocator,
    proxy_manager,
    secret_manager,
    workspace_manager,
)
from app.services.instance_logging import tee_process

logger = get_logger(__name__)

# Track Popen handles per ServiceInstance.id so stop/restart can signal them.
_RUNNING: dict[str, subprocess.Popen[bytes]] = {}


# ---------------------------------------------------------------------------
# Lazy SA1 imports — degrade gracefully if those services are not yet wired.
# ---------------------------------------------------------------------------


def _try_allocate_port(db: Session, *, app_id: str) -> int | None:
    try:
        return port_allocator.allocate_port(db, app_id=app_id, scope="app")
    except Exception:
        logger.exception("port allocation failed")
        return None


def _try_release_port(db: Session, *, port: int) -> None:
    try:
        port_allocator.release_port(db, port=port)
    except Exception:
        logger.debug("port_allocator.release_port skipped for port=%s", port)


def _inject_secrets(
    db: Session,
    *,
    app_id: str,
    env: dict[str, str],
    env_required: list[str] | None = None,
) -> dict[str, str]:
    """Merge env with secret_manager values for env_required keys.

    env_required comes from manifest.env_required. If empty/None, no secret
    lookup is done.
    """
    if not env_required:
        return env
    try:
        resolved = secret_manager.inject_for_app(db, app_id, env_required)
        # secret_manager returns {key: value}; merge into existing env
        return {**env, **resolved}
    except Exception:
        logger.debug("secret_manager unavailable — env_required not injected")
        return env


def _register_proxy_route(*, app_id: str, port: int) -> None:
    try:
        proxy_manager.register_app_route(app_id=app_id, port=port)
    except Exception:
        logger.debug("proxy_manager unavailable — skipping Caddy route for app=%s", app_id)


def _unregister_proxy_route(*, app_id: str) -> None:
    try:
        proxy_manager.unregister_app_route(app_id=app_id)
    except Exception:
        logger.debug("proxy_manager.unregister_app_route skipped for app=%s", app_id)


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------


def _manifest_for_version(version: AppVersion | None) -> dict[str, Any]:
    if version is None or not version.manifest_snapshot:
        return {}
    return dict(version.manifest_snapshot)


def _launch_config(manifest: dict[str, Any]) -> dict[str, Any]:
    launch = manifest.get("launch") or {}
    if not isinstance(launch, dict):
        return {}
    return launch


def _health_config(manifest: dict[str, Any]) -> dict[str, Any]:
    hc = _launch_config(manifest).get("health_check") or {}
    if not isinstance(hc, dict):
        return {}
    return hc


def _restart_policy(manifest: dict[str, Any]) -> dict[str, Any]:
    pol = _launch_config(manifest).get("restart_policy") or {}
    if not isinstance(pol, dict):
        return {}
    return pol


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def start_service(
    db: Session, *, app: App, version: AppVersion | None = None
) -> ServiceInstance:
    """Spawn a long-running daemon for ``app`` and persist the
    :class:`ServiceInstance` row. Caddy is wired (best-effort) and a tee thread
    streams stdout to Redis + a log file.

    The Celery beat loop polls ``check_health`` afterwards.
    """
    version = version or (
        db.get(AppVersion, app.current_version_id) if app.current_version_id else None
    )
    manifest = _manifest_for_version(version)
    launch = _launch_config(manifest)
    if launch.get("mode") != "service":
        raise ConflictError(
            f"App {app.id} manifest launch.mode is '{launch.get('mode')}', expected 'service'"
        )

    workspace = workspace_manager.app_workspace_path(app.id)
    if not workspace.exists():
        raise NotFoundError(f"Workspace missing for app {app.id}")

    # Port allocation — fall back to OS-chosen if allocator is missing.
    port = _try_allocate_port(db, app_id=app.id)

    # Build base env
    env = os.environ.copy()
    env["APP_ID"] = app.id
    env["WORKSPACE"] = str(workspace)
    if port is not None:
        env["PORT"] = str(port)
    env_required = list(manifest.get("env_required") or [])
    env = _inject_secrets(db, app_id=app.id, env=env, env_required=env_required)

    # Locate command. Default to overlay/.portal/run.sh
    cmd_str: str = launch.get("command") or "./.portal/run.sh"
    cwd = workspace / "upstream"
    if not cwd.exists():
        cwd = workspace

    venv_bin = workspace / "venv" / "bin"
    if venv_bin.exists():
        env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"
        env["VIRTUAL_ENV"] = str(workspace / "venv")

    if app.execution_target == ExecutionTarget.APPTAINER and version and version.sif_path:
        # Run inside the SIF
        binds = [f"{workspace}:/workdir"]
        bind_args: list[str] = []
        for b in binds:
            bind_args += ["--bind", b]
        cmd = [
            get_settings().apptainer_bin,
            "exec",
            *bind_args,
            version.sif_path,
            "bash",
            "-c",
            f"cd /workdir && {cmd_str}",
        ]
    else:
        cmd = ["bash", "-lc", cmd_str]

    instance_id = _uuid.uuid4()
    log_file = workspace / "logs" / f"service-{instance_id}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(  # noqa: S603
        cmd,
        env=env,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
        start_new_session=True,
    )

    instance = ServiceInstance(
        id=instance_id,
        app_id=app.id,
        version_id=version.id if version else None,
        pid=proc.pid,
        port=port,
        status="starting",
        workdir=str(cwd),
        started_at=datetime.now(timezone.utc),
        restart_count=0,
    )
    db.add(instance)
    db.commit()
    db.refresh(instance)

    _RUNNING[str(instance.id)] = proc
    tee_process(proc, instance_id=str(instance.id), log_file=log_file)

    if port is not None:
        _register_proxy_route(app_id=app.id, port=port)

    logger.info(
        "service started instance=%s app=%s pid=%s port=%s",
        instance.id,
        app.id,
        proc.pid,
        port,
    )
    return instance


def stop_service(db: Session, *, instance_id: _uuid.UUID | str) -> None:
    """SIGTERM the daemon and mark the row stopped. Idempotent."""
    inst = db.get(ServiceInstance, instance_id)
    if inst is None:
        raise NotFoundError("ServiceInstance not found")
    _terminate(str(inst.id), pid=inst.pid)
    if inst.port is not None:
        _try_release_port(db, port=inst.port)
    _unregister_proxy_route(app_id=inst.app_id)
    inst.status = "stopped"
    inst.stopped_at = datetime.now(timezone.utc)
    db.add(inst)
    db.commit()
    logger.info("service stopped instance=%s app=%s", inst.id, inst.app_id)


def restart_service(
    db: Session, *, instance_id: _uuid.UUID | str
) -> ServiceInstance:
    """Stop the current instance (if any) and start a new one for the same app."""
    inst = db.get(ServiceInstance, instance_id)
    if inst is None:
        raise NotFoundError("ServiceInstance not found")
    app = db.get(App, inst.app_id)
    if app is None:
        raise NotFoundError("App not found")
    version = db.get(AppVersion, inst.version_id) if inst.version_id else None
    previous_restart_count = inst.restart_count

    stop_service(db, instance_id=inst.id)
    new_inst = start_service(db, app=app, version=version)
    new_inst.restart_count = previous_restart_count + 1
    db.add(new_inst)
    db.commit()
    db.refresh(new_inst)
    return new_inst


def check_health(db: Session, *, instance_id: _uuid.UUID | str) -> None:
    """Hit the instance's health endpoint, update status, restart if policy says so."""
    inst = db.get(ServiceInstance, instance_id)
    if inst is None:
        return
    if inst.status == "stopped":
        return
    app = db.get(App, inst.app_id)
    version = db.get(AppVersion, inst.version_id) if inst.version_id else None
    manifest = _manifest_for_version(version)
    hc = _health_config(manifest)
    policy = _restart_policy(manifest)
    path = str(hc.get("path") or "/health")
    timeout = float(hc.get("timeout_seconds") or 5)

    healthy = False
    if inst.port is None:
        # No port allocated → fall back to process liveness.
        healthy = _process_alive(inst.pid)
    else:
        url = f"http://127.0.0.1:{inst.port}{path}"
        try:
            resp = httpx.get(url, timeout=timeout)
            healthy = resp.status_code < 500
        except Exception:
            healthy = False

    now = datetime.now(timezone.utc)
    inst.last_health = now
    inst.status = "healthy" if healthy else "unhealthy"
    db.add(inst)
    db.commit()

    if healthy:
        return

    # Restart per policy
    mode = str(policy.get("policy") or "on_failure")
    max_attempts = int(policy.get("max_attempts") or 3)
    if mode in ("no", "never"):
        return
    if inst.restart_count >= max_attempts:
        logger.warning(
            "service exhausted restarts instance=%s count=%s",
            inst.id,
            inst.restart_count,
        )
        return
    if app is None:
        return
    logger.info(
        "service unhealthy → restarting instance=%s attempt=%s/%s",
        inst.id,
        inst.restart_count + 1,
        max_attempts,
    )
    try:
        restart_service(db, instance_id=inst.id)
    except Exception:
        logger.exception("restart failed for instance=%s", inst.id)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _terminate(instance_id: str, *, pid: int | None) -> None:
    proc = _RUNNING.pop(instance_id, None)
    if proc is not None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            try:
                proc.send_signal(signal.SIGTERM)
            except Exception:
                pass
        try:
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return
    # No in-process handle (e.g. restarted worker) → signal by pid if we have it.
    if pid:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                logger.debug("could not signal pid=%s", pid)


def _process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def list_instances(db: Session) -> list[ServiceInstance]:
    from sqlalchemy import select

    return list(
        db.execute(select(ServiceInstance).order_by(ServiceInstance.started_at.desc())).scalars()
    )
