"""Launch service-mode integrations as long-running host processes.

For each ``launch.mode == "service"`` integration the scanner picks up, this
module:

1. Allocates (or reuses) a port via :mod:`port_allocator`.
2. Spawns the service via ``setsid nohup`` so it survives this worker's exit.
3. Registers a Caddy route ``/apps/{slug}/*`` → ``127.0.0.1:<port>`` via the
   admin API (see :mod:`proxy_manager`).
4. Records the PID + port in a tiny on-disk state file at
   ``var/integration_state/{slug}.json`` so a restart can probe liveness and
   avoid double-spawning.

It is best-effort: failures log + return without raising. Job-runner mode
integrations are no-ops here (they spawn per-job, not as long-running daemons).
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

from app.core.logger import get_logger
from app.services import port_allocator, proxy_manager
from app.services.stack_resolver import StackSpec, load_stacks

logger = get_logger(__name__)


_REPO_ROOT = Path(__file__).resolve().parents[3]
STATE_DIR: Path = _REPO_ROOT / "var" / "integration_state"
LOG_DIR: Path = _REPO_ROOT / "var" / "logs"
STATE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

_HEALTH_TIMEOUT = 2.0
_HEALTH_WAIT_SECONDS = 20


@dataclass(slots=True)
class LaunchResult:
    slug: str
    action: str  # "skipped" | "started" | "already_running" | "failed"
    port: int | None
    base_path: str | None
    pid: int | None = None
    error: str | None = None


def launch(
    workspace: Path, *, manifest: dict[str, Any], db
) -> LaunchResult:
    """Ensure a healthy service is running for this integration.

    The ``db`` argument is the SQLAlchemy session used for port allocation.
    """
    slug = workspace.name
    canonical = manifest.get("id") or slug.replace("-", "_")
    launch_section = manifest.get("launch") or {}
    if launch_section.get("mode") != "service":
        return LaunchResult(
            slug=slug, action="skipped", port=None, base_path=None,
            error="not a service-mode integration",
        )

    build_section = manifest.get("build") or {}
    stack_name = build_section.get("stack") or build_section.get("type") or "unknown"
    spec: StackSpec | None = load_stacks().get(stack_name)
    if spec is None:
        return LaunchResult(
            slug=slug, action="failed", port=None, base_path=None,
            error=f"unknown stack '{stack_name}'",
        )

    base_path = f"/apps/{canonical}"
    health_path = launch_section.get("health_check", {}).get(
        "path"
    ) or spec.health_path or "/"

    # ── existing process probe ────────────────────────────────────────
    state = _read_state(canonical)
    if state and _is_alive(state.get("pid")) and _is_healthy(
        state.get("port"), health_path, root=base_path
    ):
        proxy_manager.register_app_route(
            app_id=canonical, port=int(state["port"]), base_path=base_path
        )
        return LaunchResult(
            slug=slug, action="already_running",
            port=int(state["port"]), base_path=base_path, pid=int(state["pid"]),
        )

    # ── allocate port ─────────────────────────────────────────────────
    try:
        port = port_allocator.allocate_port(db, app_id=canonical, scope="app")
    except Exception as exc:
        return LaunchResult(
            slug=slug, action="failed", port=None, base_path=base_path,
            error=f"port allocation failed: {exc}",
        )

    # ── compose command ──────────────────────────────────────────────
    try:
        argv = _argv_for(workspace, spec, manifest, port=port, base_path=base_path)
    except Exception as exc:
        return LaunchResult(
            slug=slug, action="failed", port=port, base_path=base_path,
            error=f"argv build failed: {exc}",
        )

    log_file = LOG_DIR / f"integration_{canonical}.log"
    env = os.environ.copy()
    env.update({
        "PORT": str(port),
        "ROOT_PATH": base_path,
        "BASE_URL_PATH": base_path,
        "NEXT_PUBLIC_BASE_PATH": base_path,
    })

    logger.info("launching %s on :%d (root=%s) → %s",
                canonical, port, base_path, argv)
    try:
        proc = subprocess.Popen(  # noqa: S603
            argv,
            cwd=str(workspace),
            env=env,
            stdout=log_file.open("ab"),
            stderr=subprocess.STDOUT,
            start_new_session=True,  # behaves like setsid
            close_fds=True,
        )
    except Exception as exc:
        return LaunchResult(
            slug=slug, action="failed", port=port, base_path=base_path,
            error=f"Popen failed: {exc}",
        )

    # ── wait for health ──────────────────────────────────────────────
    healthy = False
    for _ in range(_HEALTH_WAIT_SECONDS):
        time.sleep(1)
        if proc.poll() is not None:
            return LaunchResult(
                slug=slug, action="failed", port=port, base_path=base_path,
                pid=proc.pid,
                error=f"process exited early code={proc.returncode}; tail of {log_file}",
            )
        if _is_healthy(port, health_path, root=base_path):
            healthy = True
            break

    if not healthy:
        # process is up but health didn't respond — could be a slow Next.js
        # cold start. Register the route anyway and let the operator decide.
        logger.warning("%s did not pass health within %ds; registering route anyway",
                       canonical, _HEALTH_WAIT_SECONDS)

    proxy_manager.register_app_route(
        app_id=canonical, port=port, base_path=base_path
    )

    _write_state(canonical, {"slug": canonical, "pid": proc.pid, "port": port,
                             "base_path": base_path, "health_path": health_path,
                             "started_at": time.time()})

    return LaunchResult(
        slug=slug, action="started", port=port, base_path=base_path, pid=proc.pid,
    )


def stop(canonical: str, *, db) -> bool:
    """Stop a running integration. True if a process was killed."""
    state = _read_state(canonical)
    killed = False
    if state and _is_alive(state.get("pid")):
        try:
            os.killpg(os.getpgid(int(state["pid"])), signal.SIGTERM)
            killed = True
        except Exception as exc:
            logger.warning("kill failed for %s pid=%s: %s",
                           canonical, state.get("pid"), exc)
    proxy_manager.unregister_app_route(app_id=canonical)
    if state and state.get("port"):
        try:
            port_allocator.release_port(db, port=int(state["port"]))
        except Exception:
            logger.exception("release_port failed for %s", canonical)
    _delete_state(canonical)
    return killed


# ---------------------------------------------------------------------------
# Command composition
# ---------------------------------------------------------------------------


def _argv_for(
    workspace: Path,
    spec: StackSpec,
    manifest: dict[str, Any],
    *,
    port: int,
    base_path: str,
) -> list[str]:
    """Decide the argv. Prefer manifest.launch.command, else stack template."""
    stack_name = (manifest.get("build") or {}).get("stack")
    venv = workspace / ".venv"

    if stack_name == "streamlit":
        bin_ = venv / "bin" / "streamlit"
        if not bin_.exists():
            raise FileNotFoundError(
                f"{bin_} not found — has integration_builder run successfully? "
                "Check var/logs/backend.log for build errors."
            )
        return [
            str(bin_), "run", "app.py",
            "--server.port", str(port),
            "--server.address", "0.0.0.0",
            "--server.baseUrlPath", base_path,
            "--server.headless", "true",
        ]
    if stack_name == "fastapi":
        bin_ = venv / "bin" / "uvicorn"
        if not bin_.exists():
            raise FileNotFoundError(
                f"{bin_} not found — has integration_builder run successfully? "
                "Check var/logs/backend.log for build errors."
            )
        return [
            str(bin_), "app.main:app",
            "--host", "0.0.0.0",
            "--port", str(port),
            "--root-path", base_path,
        ]
    if stack_name in ("nextjs", "node_service"):
        pnpm = shutil.which("pnpm") or shutil.which("npm")
        if pnpm is None:
            raise FileNotFoundError("pnpm/npm not on PATH for service launch")
        # Use node_modules/.bin/next directly for tighter control.
        next_bin = workspace / "node_modules" / ".bin" / "next"
        if next_bin.exists():
            return [str(next_bin), "start", "--port", str(port), "--hostname", "0.0.0.0"]
        return [pnpm, "start", "--", "--port", str(port), "--hostname", "0.0.0.0"]

    # Generic fallback: run manifest.launch.command via /bin/sh
    cmd = (manifest.get("launch") or {}).get("command") or "./.portal/run.sh"
    return ["/bin/sh", "-c", str(cmd)]


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


def _state_path(canonical: str) -> Path:
    return STATE_DIR / f"{canonical}.json"


def _read_state(canonical: str) -> dict | None:
    p = _state_path(canonical)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_state(canonical: str, state: dict) -> None:
    """Atomic write: tmp file + rename so a crash mid-write doesn't corrupt."""
    target = _state_path(canonical)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(target)


def _delete_state(canonical: str) -> None:
    try:
        _state_path(canonical).unlink()
    except FileNotFoundError:
        pass


def _is_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _is_healthy(port: int | None, health_path: str, *, root: str) -> bool:
    """Probe several candidate URLs; anything < 500 (including 3xx redirects
    Next.js issues for trailing-slash normalization) counts as healthy."""
    if not port:
        return False
    urls = [
        f"http://127.0.0.1:{port}{root}{health_path}",
        f"http://127.0.0.1:{port}{health_path}",
        f"http://127.0.0.1:{port}{root}",  # plain root (Next.js redirect)
    ]
    for url in urls:
        try:
            r = httpx.get(url, timeout=_HEALTH_TIMEOUT, follow_redirects=False)
            if r.status_code < 500:
                return True
        except Exception:
            continue
    return False


def read_manifest(workspace: Path) -> dict[str, Any] | None:
    """Convenience used by the scanner to feed launch()."""
    manifest = workspace / ".portal" / "manifest.yaml"
    if not manifest.exists():
        return None
    try:
        return yaml.safe_load(manifest.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("manifest load failed for %s: %s", workspace, exc)
        return None
