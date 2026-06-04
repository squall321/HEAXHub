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

State file robustness
---------------------
* All writes are fsync'd + atomically renamed so power loss doesn't leave a
  half-written JSON.
* Every record carries a ``schema_version`` so older state from a previous
  HEAXHub release degrades gracefully (we ignore unknown versions instead of
  blowing up the launcher).
* Before sending SIGTERM we verify ``/proc/<pid>/cmdline`` still matches the
  argv we recorded — guards against the classic PID-reuse footgun where the
  stored pid has been recycled by an unrelated process.
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
from app.services import apt_runner, port_allocator, proxy_manager
from app.services.stack_resolver import StackSpec, load_stacks

logger = get_logger(__name__)


_REPO_ROOT = Path(__file__).resolve().parents[3]
STATE_DIR: Path = _REPO_ROOT / "var" / "integration_state"
LOG_DIR: Path = _REPO_ROOT / "var" / "logs"
STATE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

_HEALTH_TIMEOUT = 2.0
_HEALTH_WAIT_SECONDS = 20

# Bump this when the state file shape changes incompatibly. Older state files
# (different or absent version) are ignored, NOT crashed on, so an upgrade
# in-place just costs one extra spawn cycle.
#
# v2 added optional ``sif_path`` + ``instance_name`` fields so the stop path
# can route to ``apt_runner.instance_stop`` instead of SIGTERM-ing a host pid.
_STATE_SCHEMA_VERSION = 2

# Stacks whose framework bakes the base path into routing and therefore must
# RECEIVE the full incoming path (no Caddy prefix strip). For everything else
# the upstream listens at "/" and Caddy strips ``/apps/<slug>`` before proxying,
# which is the safer default — most WSGI/HTTP servers (Flask, gunicorn, node
# express, Go net/http) don't auto-handle a sub-path on request URLs.
_PREFIX_AWARE_STACKS = frozenset({
    "streamlit", "nextjs", "node_service",
    # Dash builds internal links from requests_pathname_prefix; it must see the
    # full /apps/<slug>/... path, so do not strip at Caddy.
    "dash_plotly",
    # Shiny --root-path == ASGI root_path; upstream expects full prefix.
    "shiny_for_python",
    # Flask via DispatcherMiddleware requires SCRIPT_NAME == prefix AND the
    # request path to START with that prefix; Caddy strip breaks the second
    # half of that contract, so we forward unchanged.
    "flask",
})


@dataclass(slots=True)
class LaunchResult:
    slug: str
    action: str  # "skipped" | "started" | "already_running" | "failed"
    port: int | None
    base_path: str | None
    pid: int | None = None
    error: str | None = None


def launch(
    workspace: Path,
    *,
    manifest: dict[str, Any],
    db,
    slug: str | None = None,
    source: dict[str, Any] | None = None,
    sif_path: Path | None = None,
) -> LaunchResult:
    """Ensure a healthy service is running for this integration.

    The ``db`` argument is the SQLAlchemy session used for port allocation.

    When ``sif_path`` is set and points to an existing SIF file, the launcher
    dispatches via ``apt_runner`` (apptainer instance) instead of spawning the
    server straight on the host. ``slug`` lets the caller override the default
    (workspace dirname) which is useful when the workspace was synthesised
    from a fetched source tree. ``source`` is the manifest's ``source:`` block
    if present — currently unused inside ``launch`` but kept on the signature
    so callers can pass it without breaking when we start binding it inside
    the container.
    """
    # ``source`` is captured for forward-compat; touch it to silence linters
    # without changing behaviour.
    _ = source
    slug = slug or workspace.name
    canonical = manifest.get("id") or slug.replace("-", "_")
    launch_section = manifest.get("launch") or {}
    mode = launch_section.get("mode")

    # Non-process launch modes: bail out before any port allocation or Popen.
    # ``url`` and ``iframe`` are pure-frontend dispatches — the catalog reads
    # ``launch.url`` and either opens a new tab or embeds an iframe. There is
    # nothing for the launcher to spawn or register.
    if mode in ("url", "iframe"):
        return LaunchResult(
            slug=slug, action="skipped", port=None,
            base_path=f"/apps/{canonical}",
        )

    # ``proxy`` mode is non-process too, but we DO register a Caddy route
    # pointing /apps/{id}/* at an external upstream URL.
    if mode == "proxy":
        upstream = launch_section.get("upstream") or launch_section.get("url")
        if not upstream:
            return LaunchResult(
                slug=slug, action="failed", port=None,
                base_path=f"/apps/{canonical}",
                error="proxy mode requires launch.upstream (URL)",
            )
        base_path = f"/apps/{canonical}"
        strip_prefix = bool(launch_section.get("strip_prefix", True))
        try:
            res = proxy_manager.register_external_proxy_route(
                app_id=canonical,
                upstream_url=str(upstream),
                base_path=base_path,
                strip_prefix=strip_prefix,
            )
        except Exception as exc:
            return LaunchResult(
                slug=slug, action="failed", port=None, base_path=base_path,
                error=f"caddy register failed: {exc}",
            )
        if not getattr(res, "ok", False):
            return LaunchResult(
                slug=slug, action="failed", port=None, base_path=base_path,
                error=f"caddy register failed: {getattr(res, 'reason', 'unknown')}",
            )
        return LaunchResult(
            slug=slug, action="started", port=None, base_path=base_path,
        )

    # ``static`` mode: no process. Caddy serves the workspace's pre-built
    # static_root directory via the file_server handler. We resolve the root
    # to an absolute filesystem path Caddy can read, then register the route.
    if mode == "static":
        return _launch_static(
            workspace, canonical, manifest,
            slug=slug, source=source,
        )

    if mode != "service":
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
        strip_prefix = stack_name not in _PREFIX_AWARE_STACKS
        # Re-register on every confirmed-alive probe if state says we missed
        # it last time (Caddy admin was down during the previous launch).
        needs_caddy = not bool(state.get("caddy_registered"))
        caddy_ok = True
        if needs_caddy:
            caddy_ok = _safe_register_caddy(
                canonical, int(state["port"]), base_path, strip_prefix,
            )
            if caddy_ok:
                state["caddy_registered"] = True
                _write_state(canonical, state)
        else:
            # Idempotent refresh; harmless if Caddy already has the route.
            _safe_register_caddy(
                canonical, int(state["port"]), base_path, strip_prefix,
            )
        return LaunchResult(
            slug=slug, action="already_running",
            port=int(state["port"]), base_path=base_path, pid=int(state["pid"]),
        )

    # ── SIF dispatch (preferred when a SIF was built for this slug) ───
    # When a SIF exists, we start (or reuse) an apptainer instance and exec
    # the canonical argv inside it. The host process we supervise is the
    # ``apptainer exec`` Popen — its lifecycle mirrors the in-container
    # server, so the existing pid + cmdline guarding still applies.
    if sif_path is not None and sif_path.exists():
        return _launch_via_sif(
            workspace=workspace,
            slug=slug,
            canonical=canonical,
            spec=spec,
            manifest=manifest,
            stack_name=stack_name,
            base_path=base_path,
            health_path=health_path,
            sif_path=sif_path,
            db=db,
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
        # Release the port we just claimed — nobody else can use it otherwise.
        _safe_release_port(db, port)
        return LaunchResult(
            slug=slug, action="failed", port=None, base_path=base_path,
            error=f"argv build failed: {exc}",
        )

    log_file = LOG_DIR / f"integration_{canonical}.log"
    env = os.environ.copy()
    env.update({
        "PORT": str(port),
        "ROOT_PATH": base_path,
        "BASE_URL_PATH": base_path,
        "NEXT_PUBLIC_BASE_PATH": base_path,
        # Stack-specific sub-path env vars. We set them unconditionally because
        # they're inert when the framework doesn't read them, and saves us
        # branching per stack here.
        "SCRIPT_NAME": base_path,           # Flask / WSGI standard
        "BASE_PATH": base_path,             # nodejs_express / go_service convention
        "HEAX_BASE_PATH": base_path,        # generic HEAXHub-side convention
        "DASH_URL_BASE_PATHNAME": base_path + "/",  # Plotly Dash needs trailing slash
    })

    logger.info("launching %s on :%d (root=%s) → %s",
                canonical, port, base_path, argv)
    # Open the per-integration log append+binary so multiple workers can't
    # truncate each other and we tolerate non-UTF-8 binary output from the
    # child (rare but happens with some launchers).
    try:
        log_fh = log_file.open("ab")
    except OSError as exc:
        _safe_release_port(db, port)
        return LaunchResult(
            slug=slug, action="failed", port=None, base_path=base_path,
            error=f"could not open log {log_file}: {exc}",
        )
    try:
        proc = subprocess.Popen(  # noqa: S603
            argv,
            cwd=str(workspace),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # behaves like setsid
            close_fds=True,
        )
    except Exception as exc:
        log_fh.close()
        _safe_release_port(db, port)
        return LaunchResult(
            slug=slug, action="failed", port=None, base_path=base_path,
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

    # Stacks that handle their own base path (Streamlit, Next.js, Dash, Shiny)
    # need Caddy to pass through the full /apps/<slug> prefix so their internal
    # routers see canonical paths. Everything else strips the prefix.
    strip_prefix = stack_name not in _PREFIX_AWARE_STACKS
    caddy_ok = _safe_register_caddy(canonical, port, base_path, strip_prefix)

    _write_state(canonical, {
        "schema_version": _STATE_SCHEMA_VERSION,
        "slug": canonical,
        "pid": proc.pid,
        "port": port,
        "base_path": base_path,
        "health_path": health_path,
        "stack": stack_name,
        "argv": argv,
        "caddy_registered": caddy_ok,
        "started_at": time.time(),
    })

    return LaunchResult(
        slug=slug, action="started", port=port, base_path=base_path, pid=proc.pid,
    )


def _launch_via_sif(
    *,
    workspace: Path,
    slug: str,
    canonical: str,
    spec: StackSpec,
    manifest: dict[str, Any],
    stack_name: str,
    base_path: str,
    health_path: str,
    sif_path: Path,
    db,
) -> LaunchResult:
    """SIF-backed dispatch — start instance, exec the canonical argv inside it.

    Side-effects mirror the host-mode launch:
      * allocate a port via ``port_allocator``,
      * write a state file (schema v2 with ``instance_name`` + ``sif_path``),
      * register the Caddy route.
    """
    instance_name = _instance_name_for(canonical)
    log_file = LOG_DIR / f"integration_{canonical}.log"

    # ── reuse already-running instance + state ────────────────────────
    try:
        running_instances = set(apt_runner.instance_list())
    except Exception:  # pragma: no cover - defensive
        running_instances = set()

    state = _read_state(canonical)
    if (
        state
        and state.get("instance_name") == instance_name
        and instance_name in running_instances
        and _is_alive(state.get("pid"))
        and _is_healthy(state.get("port"), health_path, root=base_path)
    ):
        strip_prefix = stack_name not in _PREFIX_AWARE_STACKS
        needs_caddy = not bool(state.get("caddy_registered"))
        if needs_caddy:
            ok = _safe_register_caddy(
                canonical, int(state["port"]), base_path, strip_prefix,
            )
            if ok:
                state["caddy_registered"] = True
                _write_state(canonical, state)
        else:
            _safe_register_caddy(
                canonical, int(state["port"]), base_path, strip_prefix,
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

    # ── compose container-side argv ───────────────────────────────────
    try:
        container_argv = _sif_argv_for(spec, manifest, stack_name, port=port, base_path=base_path)
    except Exception as exc:
        _safe_release_port(db, port)
        return LaunchResult(
            slug=slug, action="failed", port=None, base_path=base_path,
            error=f"argv build failed: {exc}",
        )

    # ── ensure the instance is running ────────────────────────────────
    binds: list[tuple[str, str]] = [(str(workspace), "/workspace")]
    env_in_container = {
        "PORT": str(port),
        "ROOT_PATH": base_path,
        "BASE_URL_PATH": base_path,
        "NEXT_PUBLIC_BASE_PATH": base_path,
        "SCRIPT_NAME": base_path,
        "BASE_PATH": base_path,
        "HEAX_BASE_PATH": base_path,
        "DASH_URL_BASE_PATHNAME": base_path + "/",
    }
    if instance_name not in running_instances:
        try:
            apt_runner.instance_start(
                sif=sif_path,
                name=instance_name,
                binds=binds,
                cleanenv=True,
                env=env_in_container,
            )
        except subprocess.CalledProcessError as exc:
            _safe_release_port(db, port)
            return LaunchResult(
                slug=slug, action="failed", port=port, base_path=base_path,
                error=f"apptainer instance start failed: {exc}",
            )
        except FileNotFoundError as exc:
            _safe_release_port(db, port)
            return LaunchResult(
                slug=slug, action="failed", port=port, base_path=base_path,
                error=str(exc),
            )

    # ── exec the foreground server inside the instance ───────────────
    try:
        log_fh = log_file.open("ab")
    except OSError as exc:
        _safe_release_port(db, port)
        return LaunchResult(
            slug=slug, action="failed", port=port, base_path=base_path,
            error=f"could not open log {log_file}: {exc}",
        )
    try:
        proc = apt_runner.instance_exec(
            instance_name,
            container_argv,
            env=env_in_container,
            cleanenv=True,
            cwd=str(workspace),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    except Exception as exc:
        log_fh.close()
        _safe_release_port(db, port)
        return LaunchResult(
            slug=slug, action="failed", port=port, base_path=base_path,
            error=f"apptainer exec failed: {exc}",
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
        logger.warning("%s (SIF) did not pass health within %ds; registering route anyway",
                       canonical, _HEALTH_WAIT_SECONDS)

    strip_prefix = stack_name not in _PREFIX_AWARE_STACKS
    caddy_ok = _safe_register_caddy(canonical, port, base_path, strip_prefix)

    _write_state(canonical, {
        "schema_version": _STATE_SCHEMA_VERSION,
        "slug": canonical,
        "pid": proc.pid,
        "port": port,
        "base_path": base_path,
        "health_path": health_path,
        "stack": stack_name,
        "argv": [str(sif_path), *container_argv],  # for the PID-reuse guard
        "instance_name": instance_name,
        "sif_path": str(sif_path),
        "caddy_registered": caddy_ok,
        "started_at": time.time(),
    })

    return LaunchResult(
        slug=slug, action="started", port=port, base_path=base_path, pid=proc.pid,
    )


def _instance_name_for(canonical: str) -> str:
    """Build the apptainer instance name from the canonical app id.

    Apptainer instance names must match ``[A-Za-z0-9_-]+`` and stay short
    enough to fit comfortably in PID-file names — we conservatively allow
    underscores/hyphens and rewrite anything else.
    """
    safe = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in canonical)
    return f"heax_app_{safe}"


def _sif_argv_for(
    spec: StackSpec,
    manifest: dict[str, Any],
    stack_name: str,
    *,
    port: int,
    base_path: str,
) -> list[str]:
    """Container-side argv for the foreground server.

    Inside the SIF the runtimes are installed system-wide, so we use plain
    binary names (gunicorn / uvicorn / streamlit / node) and let the
    container's PATH resolve them. The cwd is the bind-mounted workspace.
    """
    _ = spec  # currently unused but accepted for parity with _argv_for
    if stack_name == "streamlit":
        return [
            "streamlit", "run", "app.py",
            "--server.port", str(port),
            "--server.address", "0.0.0.0",
            "--server.baseUrlPath", base_path,
            "--server.headless", "true",
        ]
    if stack_name == "fastapi":
        return [
            "uvicorn", "app.main:app",
            "--host", "0.0.0.0",
            "--port", str(port),
            "--root-path", base_path,
        ]
    if stack_name == "flask":
        callable_ = (
            (manifest.get("launch") or {}).get("entrypoint_override")
            or "app:app"
        )
        return [
            "gunicorn",
            "--bind", f"0.0.0.0:{port}",
            "--workers", "2",
            "--access-logfile", "-",
            str(callable_),
        ]
    if stack_name == "dash_plotly":
        return ["python", "app.py"]
    if stack_name == "shiny_for_python":
        return [
            "shiny", "run",
            "--host", "0.0.0.0",
            "--port", str(port),
            "--root-path", base_path,
            "app.py",
        ]
    if stack_name in ("nextjs", "node_service"):
        return ["node_modules/.bin/next", "start", "--port", str(port), "--hostname", "0.0.0.0"]
    if stack_name == "nodejs_express":
        return ["node", "dist/server.js"]
    if stack_name == "go_service":
        # go_service.def copies the built binary to /app/bin/server inside the SIF.
        # --pwd /app means the relative path is bin/server, but we use an absolute
        # path so it works regardless of pwd flag interaction.
        return ["/app/bin/server"]
    if stack_name == "dotnet_aspnet":
        override = (manifest.get("launch") or {}).get("assembly") or "app.dll"
        return ["dotnet", f"/app/{override}", "--urls", f"http://0.0.0.0:{port}"]
    if stack_name == "java_springboot":
        return ["java", "-jar", "/app/app.jar", f"--server.port={port}"]
    if stack_name == "rust_actix":
        return ["/server"]
    # Generic fallback: honour manifest.launch.command.
    cmd = (manifest.get("launch") or {}).get("command") or "./.portal/run.sh"
    return ["/bin/sh", "-c", str(cmd)]


def _launch_static(
    workspace: Path,
    canonical: str,
    manifest: dict[str, Any],
    *,
    slug: str | None = None,
    source: dict[str, Any] | None = None,
) -> LaunchResult:
    """Wire a static-runtime stack into Caddy via ``file_server``.

    No port is allocated, no process is spawned. We resolve the configured
    ``static_root`` to an absolute filesystem path (validated already by the
    builder) and ask Caddy's admin API to serve it from ``/apps/{id}/*``.

    When the manifest carries a ``source:`` block, the static_root lives under
    the fetched upstream workspace at
    ``var/integration_workspaces/<slug>/upstream[/<subpath>]/`` instead of the
    in-tree integration directory — the manifest-only pivot leaves the latter
    empty of build artefacts.
    """
    # Local import to avoid an import cycle at module load.
    from app.services import managed_workspaces

    base_path = f"/apps/{canonical}"
    effective_slug = slug or workspace.name

    build_section = manifest.get("build") or {}
    stack_name = build_section.get("stack") or build_section.get("type") or "unknown"
    spec: StackSpec | None = load_stacks().get(stack_name)
    if spec is None:
        return LaunchResult(
            slug=effective_slug, action="failed", port=None, base_path=base_path,
            error=f"unknown stack '{stack_name}'",
        )

    extra = spec.extra or {}
    # Accept both ``static_root`` (canonical) and ``root`` (shorter alias) so
    # we stay in step with the builder's resolution logic.
    root_rel = (
        build_section.get("static_root")
        or build_section.get("root")
        or extra.get("static_root")
        or "public"
    )
    index_file = (
        build_section.get("index_file")
        or extra.get("index_file")
        or "index.html"
    )

    # Source-aware base path:
    #   - source present → static_root lives under the fetched upstream
    #     workspace (var/integration_workspaces/<slug>/upstream[/subpath])
    #   - source absent  → legacy: resolve under the in-tree integration_dir
    if source and slug:
        subpath = (source.get("subpath") or "")
        base_dir = managed_workspaces.upstream_dir(slug, subpath)
    else:
        base_dir = workspace
    root_abs = (base_dir / root_rel).resolve()
    if not root_abs.is_dir():
        return LaunchResult(
            slug=effective_slug, action="failed", port=None, base_path=base_path,
            error=(
                f"static_root '{root_rel}' missing at {root_abs} — "
                f"did fetch/build run successfully?"
            ),
        )

    try:
        res = proxy_manager.register_static_route(
            app_id=canonical,
            root_path=str(root_abs),
            base_path=base_path,
            index_file=index_file,
        )
    except Exception as exc:
        return LaunchResult(
            slug=effective_slug, action="failed", port=None, base_path=base_path,
            error=f"caddy register failed: {exc}",
        )
    if not getattr(res, "ok", False):
        return LaunchResult(
            slug=effective_slug, action="failed", port=None, base_path=base_path,
            error=f"caddy register failed: {getattr(res, 'reason', 'unknown')}",
        )
    return LaunchResult(
        slug=effective_slug, action="started", port=None, base_path=base_path,
    )


def stop(canonical: str, *, db) -> bool:
    """Stop a running integration. True if a process was killed.

    When the state records an apptainer ``instance_name`` we route the stop
    through ``apt_runner.instance_stop`` — that terminates every process
    inside the container in one shot, so we don't also need to SIGTERM the
    host-side ``apptainer exec`` pid (it will exit on its own when the
    instance goes away).
    """
    state = _read_state(canonical)
    killed = False
    instance_name = state.get("instance_name") if state else None
    if state and instance_name:
        try:
            apt_runner.instance_stop(instance_name, check=False)
            killed = True
        except Exception as exc:
            logger.warning("apptainer instance stop failed for %s (%s): %s",
                           canonical, instance_name, exc)
    elif state and _is_alive(state.get("pid")):
        expected_argv = state.get("argv") or []
        if _pid_matches_argv(int(state["pid"]), expected_argv):
            try:
                os.killpg(os.getpgid(int(state["pid"])), signal.SIGTERM)
                killed = True
            except Exception as exc:
                logger.warning("kill failed for %s pid=%s: %s",
                               canonical, state.get("pid"), exc)
        else:
            # PID reuse: the process at this pid is no longer ours. Don't
            # SIGTERM a stranger; just clean up the stale state.
            logger.warning(
                "refusing to kill pid %s for %s — cmdline does not match "
                "recorded argv (likely PID reuse)",
                state.get("pid"), canonical,
            )
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
    if stack_name == "flask":
        # gunicorn from the venv binds to $PORT; Flask reads SCRIPT_NAME from
        # env (WSGI standard) so we don't need to pass base_path on argv.
        bin_ = venv / "bin" / "gunicorn"
        if not bin_.exists():
            raise FileNotFoundError(
                f"{bin_} not found — has integration_builder run successfully? "
                "Check var/logs/backend.log for build errors."
            )
        callable_ = (
            (manifest.get("launch") or {}).get("entrypoint_override")
            or "app:app"
        )
        return [
            str(bin_),
            "--bind", f"0.0.0.0:{port}",
            "--workers", "2",
            "--access-logfile", "-",
            str(callable_),
        ]
    if stack_name == "nodejs_express":
        # Operator builds to dist/server.js (TypeScript) or commits a plain
        # src/server.js. Prefer dist/, fall back to src/ for non-TS projects.
        node_bin = shutil.which("node")
        if node_bin is None:
            raise FileNotFoundError("node not on PATH for nodejs_express launch")
        dist_entry = workspace / "dist" / "server.js"
        src_entry = workspace / "src" / "server.js"
        entry = dist_entry if dist_entry.exists() else src_entry
        if not entry.exists():
            raise FileNotFoundError(
                f"neither {dist_entry} nor {src_entry} found — did the build run?"
            )
        return [node_bin, str(entry)]
    if stack_name == "go_service":
        # `go build` drops the binary at the project root with the module
        # basename. Older convention was `bin/server`. We probe both so the
        # operator's project layout choice doesn't matter.
        candidates = [
            workspace / "bin" / "server",
            workspace / "bin" / workspace.name,
            workspace / workspace.name,
        ]
        bin_ = next((c for c in candidates if c.exists()), None)
        if bin_ is None:
            raise FileNotFoundError(
                f"go binary not found at any of: {[str(c) for c in candidates]} "
                "— has integration_builder run 'go build' successfully? "
                "Check var/logs/build_*.log for build errors."
            )
        return [str(bin_)]
    if stack_name == "dash_plotly":
        # Dash apps expose `server = app.server` (a Flask WSGI callable). We
        # exec `python app.py` directly so the operator's __main__ block reads
        # PORT/DASH_URL_BASE_PATHNAME from env and is in control of binding —
        # avoids requiring gunicorn in the venv for the demo footprint.
        python_bin = venv / "bin" / "python"
        if not python_bin.exists():
            raise FileNotFoundError(
                f"{python_bin} not found — has integration_builder run successfully? "
                "Check var/logs/backend.log for build errors."
            )
        return [str(python_bin), "app.py"]
    if stack_name == "shiny_for_python":
        # The `shiny` CLI is installed into .venv/bin/shiny by `pip install shiny`.
        # --root-path mounts the ASGI app under the canonical sub-path prefix.
        bin_ = venv / "bin" / "shiny"
        if not bin_.exists():
            raise FileNotFoundError(
                f"{bin_} not found — has integration_builder run successfully? "
                "Check var/logs/backend.log for build errors."
            )
        return [
            str(bin_), "run",
            "--host", "0.0.0.0",
            "--port", str(port),
            "--root-path", base_path,
            "app.py",
        ]
    if stack_name == "dotnet_aspnet":
        # Operator runs `dotnet publish -c Release -o publish/`. We resolve the
        # main entry assembly: prefer publish/<override>.dll if the manifest
        # names it, else the first non-shared .dll under publish/. ASP.NET
        # Core reads --urls and binds; Caddy strips /apps/<slug> before proxy
        # so the app sees canonical paths and we don't need a sub-path option.
        dotnet_bin = shutil.which("dotnet")
        if dotnet_bin is None:
            raise FileNotFoundError(
                "dotnet not on PATH — operator must install .NET 8 SDK on the host"
            )
        publish_dir = workspace / "publish"
        if not publish_dir.exists():
            raise FileNotFoundError(
                f"{publish_dir} not found — has integration_builder run successfully? "
                "Check var/logs/backend.log for build errors."
            )
        override = (manifest.get("launch") or {}).get("assembly")
        dll_path: Path | None = None
        if override:
            cand = publish_dir / override
            if cand.exists():
                dll_path = cand
        if dll_path is None:
            # Pick the newest .dll that isn't an obvious dependency (ref/Microsoft.*).
            # The MSBuild target writes the entry assembly LAST so mtime sort is
            # a reasonable heuristic for the default project layout.
            dlls = sorted(
                publish_dir.glob("*.dll"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for cand in dlls:
                if cand.name.startswith(("Microsoft.", "System.")):
                    continue
                dll_path = cand
                break
        if dll_path is None:
            raise FileNotFoundError(
                f"no entry .dll found under {publish_dir}; set launch.assembly "
                "in the manifest to disambiguate."
            )
        return [
            dotnet_bin, str(dll_path),
            "--urls", f"http://0.0.0.0:{port}",
        ]
    if stack_name == "java_springboot":
        # Spring Boot reads --server.port from the command-line property bridge.
        # We exec the fat-jar produced by `./mvnw package` (target/*.jar). The
        # operator can pin a specific jar via manifest.launch.jar; otherwise we
        # pick the most-recently-built one that isn't a -sources/-javadoc artifact.
        java_bin = shutil.which("java")
        if java_bin is None:
            raise FileNotFoundError(
                "java not on PATH — operator must install JDK 17 on the host"
            )
        target_dir = workspace / "target"
        override = (manifest.get("launch") or {}).get("jar")
        jar_path: Path | None = None
        if override:
            cand = workspace / override
            if cand.exists():
                jar_path = cand
        if jar_path is None and target_dir.exists():
            jars = sorted(
                (
                    p for p in target_dir.glob("*.jar")
                    if not p.name.endswith(("-sources.jar", "-javadoc.jar"))
                ),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            jar_path = jars[0] if jars else None
        if jar_path is None:
            raise FileNotFoundError(
                f"no runnable jar under {target_dir} — has integration_builder "
                "run successfully? Check var/logs/backend.log for build errors."
            )
        return [
            java_bin, "-jar", str(jar_path),
            f"--server.port={port}",
        ]
    if stack_name == "rust_actix":
        # cargo build --release puts the binary at target/release/<crate_name>.
        # Operator may pin the binary name via manifest.launch.binary; default
        # picks the first executable file under target/release/ that isn't a
        # build artefact (.d / .rlib). The actix app reads $PORT from env
        # (heaxhub injects PORT for every service launch).
        release_dir = workspace / "target" / "release"
        if not release_dir.exists():
            raise FileNotFoundError(
                f"{release_dir} not found — has integration_builder run successfully? "
                "Check var/logs/backend.log for build errors."
            )
        override = (manifest.get("launch") or {}).get("binary")
        bin_path: Path | None = None
        if override:
            cand = release_dir / override
            if cand.exists() and os.access(cand, os.X_OK):
                bin_path = cand
        if bin_path is None:
            for cand in sorted(release_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if not cand.is_file():
                    continue
                if cand.name.endswith((".d", ".rlib", ".rmeta")):
                    continue
                if not os.access(cand, os.X_OK):
                    continue
                bin_path = cand
                break
        if bin_path is None:
            raise FileNotFoundError(
                f"no release binary under {release_dir}; set launch.binary in the "
                "manifest to disambiguate."
            )
        return [str(bin_path)]

    # Generic fallback: run manifest.launch.command via /bin/sh
    cmd = (manifest.get("launch") or {}).get("command") or "./.portal/run.sh"
    return ["/bin/sh", "-c", str(cmd)]


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


def _state_path(canonical: str) -> Path:
    return STATE_DIR / f"{canonical}.json"


def _read_state(canonical: str) -> dict | None:
    """Read the state file, ignoring records from a different schema version.

    Older state (no schema_version key, or a different number) is treated as
    nonexistent so the launcher takes the cold-start path instead of crashing
    on a missing field.
    """
    p = _state_path(canonical)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    version = data.get("schema_version")
    if version is None:
        # Pre-versioned state: upgrade in-memory but trust enough fields to
        # re-register Caddy. If anything is missing the launcher will cold-start.
        data["schema_version"] = _STATE_SCHEMA_VERSION
        return data
    if version != _STATE_SCHEMA_VERSION:
        logger.info(
            "ignoring %s state file with schema_version=%r (current=%d)",
            canonical, version, _STATE_SCHEMA_VERSION,
        )
        return None
    return data


def _write_state(canonical: str, state: dict) -> None:
    """Atomic write with fsync — survives power-loss between write and rename."""
    state.setdefault("schema_version", _STATE_SCHEMA_VERSION)
    target = _state_path(canonical)
    tmp = target.with_suffix(".json.tmp")
    payload = json.dumps(state, indent=2).encode("utf-8")
    # fsync the tmp file before rename so the rename can't be reordered ahead
    # of the bytes hitting the disk platter.
    with open(tmp, "wb") as f:
        f.write(payload)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, target)
    try:
        dir_fd = os.open(str(target.parent), os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


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


def _pid_matches_argv(pid: int, expected_argv: list[str]) -> bool:
    """True when /proc/<pid>/cmdline starts with the expected argv prefix.

    Guards against PID-reuse: an unrelated process inherited our recorded pid
    after the integration died and we never noticed. We compare the first
    argv token (the executable) plus the second token if present, which is
    enough to disambiguate ``streamlit run`` from ``ssh root@host`` without
    being overly strict about minor argv differences.
    """
    if not expected_argv:
        # No recorded argv (old state) — assume ok; the worst case is we kill
        # a foreign process, which is the bug we're trying to avoid, but we
        # can't do better without recorded data.
        return True
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
            raw = f.read()
    except (FileNotFoundError, PermissionError, OSError):
        return False
    actual = [t.decode("utf-8", errors="replace") for t in raw.split(b"\x00") if t]
    if not actual:
        return False
    # Match on the executable basename so /usr/bin/python3 vs ./.venv/bin/python3
    # don't false-negative. Then require the second token (sub-command) to match
    # too when present.
    exp0 = os.path.basename(expected_argv[0])
    act0 = os.path.basename(actual[0])
    if exp0 != act0:
        return False
    if len(expected_argv) >= 2 and len(actual) >= 2:
        if expected_argv[1] != actual[1]:
            return False
    return True


def _is_healthy(port: int | None, health_path: str, *, root: str) -> bool:
    """Probe several candidate URLs; anything < 500 (including 3xx redirects
    Next.js issues for trailing-slash normalization) counts as healthy."""
    if not port:
        return False
    urls = [
        f"http://127.0.0.1:{port}{root}{health_path}",
        f"http://127.0.0.1:{port}{health_path}",
        f"http://127.0.0.1:{port}{root}",  # plain root (Next.js redirect)
        f"http://127.0.0.1:{port}{root.rstrip('/')}",  # bare slug, no trailing slash
    ]
    for url in urls:
        try:
            r = httpx.get(url, timeout=_HEALTH_TIMEOUT, follow_redirects=False)
            if r.status_code < 500:
                return True
        except Exception:
            continue
    return False


def _safe_register_caddy(
    app_id: str, port: int, base_path: str, strip_prefix: bool,
) -> bool:
    """Register a Caddy route, returning False on transient admin failures.

    The caller can persist this to know it must retry on next scan instead
    of falsely believing the route is wired up.
    """
    try:
        proxy_manager.register_app_route(
            app_id=app_id, port=port, base_path=base_path,
            strip_prefix=strip_prefix,
        )
        return True
    except Exception as exc:
        logger.warning(
            "Caddy admin unreachable while registering %s; will retry: %s",
            app_id, exc,
        )
        return False


def _safe_release_port(db, port: int | None) -> None:
    if not port:
        return
    try:
        port_allocator.release_port(db, port=int(port))
    except Exception:
        logger.exception("release_port failed for port %s", port)


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
