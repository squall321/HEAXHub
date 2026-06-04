"""Idempotent build of an integrations/<slug>/ workspace.

The :mod:`integrations_scanner` decides *what* to register. This module owns
*how* to make a registered integration runnable — installs Python venvs, runs
``pnpm install && pnpm build`` for Node services, etc.

Design notes
------------
* **Idempotent.** Each builder probes for a sentinel (e.g. ``.venv/bin/PYTHON``
  with the right interpreter and ``.heaxhub_build_ok`` mtime newer than
  ``pyproject.toml``) and skips work when the artifact is already up to date.
* **No subprocess spawned at request time.** Builders may take minutes; the
  scanner calls :func:`build` in a Celery worker, never inline in uvicorn.
* **Best-effort.** A build failure is logged and surfaced via the return
  value; the caller (scanner) MUST not crash. The App row stays in DB and
  the operator can re-trigger the build.
* **Reads stack from** ``manifest.build.stack`` first (authoritative), then
  falls back to the global ``config/stacks.yaml`` for the runtime/install
  template.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.core.logger import get_logger
from app.services.stack_resolver import StackSpec, load_stacks

logger = get_logger(__name__)

# Sentinel file we drop in the integration workspace once a build finishes
# cleanly. Its mtime is compared against pyproject.toml / package.json to
# decide whether a rebuild is needed.
_SENTINEL = ".heaxhub_build_ok"

# Bound build time per integration so a runaway pnpm install doesn't park
# the worker forever. 10 minutes covers a cold pnpm install + next build on
# modest hardware; tweak per-host via HEAXHUB_BUILD_TIMEOUT_SECONDS.
_BUILD_TIMEOUT = int(os.environ.get("HEAXHUB_BUILD_TIMEOUT_SECONDS", "600"))


@dataclass(slots=True)
class BuildResult:
    """Outcome of :func:`build` for a single integration."""

    slug: str
    action: str  # "skipped" | "built" | "failed"
    stack: str | None
    duration_seconds: float
    error: str | None = None


def build(workspace: Path, *, manifest: dict[str, Any]) -> BuildResult:
    """Ensure the workspace has its install artifacts ready.

    Returns a :class:`BuildResult` — never raises. The caller decides what to
    do with failure (typically: log + leave App row stable, retry next scan).
    """
    slug = workspace.name
    started = time.monotonic()

    build_section = manifest.get("build") or {}
    stack_name = (
        build_section.get("stack")
        or build_section.get("type")
        or "unknown"
    )
    spec: StackSpec | None = load_stacks().get(stack_name)
    if spec is None:
        return BuildResult(
            slug=slug,
            action="failed",
            stack=stack_name,
            duration_seconds=time.monotonic() - started,
            error=f"unknown stack '{stack_name}' (see config/stacks.yaml)",
        )

    try:
        if spec.runtime == "python_venv":
            changed = _build_python(workspace, spec, build_section)
        elif spec.runtime == "nodejs":
            changed = _build_nodejs(workspace, spec, build_section)
        elif spec.runtime == "windows_agent":
            # Windows installers are produced offline; nothing to build live.
            changed = False
        else:
            return BuildResult(
                slug=slug,
                action="failed",
                stack=stack_name,
                duration_seconds=time.monotonic() - started,
                error=f"unsupported runtime '{spec.runtime}' for live build",
            )
    except subprocess.TimeoutExpired as exc:
        return BuildResult(
            slug=slug,
            action="failed",
            stack=stack_name,
            duration_seconds=time.monotonic() - started,
            error=f"build timeout after {_BUILD_TIMEOUT}s: {exc.cmd}",
        )
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or exc.stdout or b"")[-2000:]
        return BuildResult(
            slug=slug,
            action="failed",
            stack=stack_name,
            duration_seconds=time.monotonic() - started,
            error=f"exit={exc.returncode} cmd={exc.cmd!r} tail={tail!r}",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("unexpected build error for %s", slug)
        return BuildResult(
            slug=slug,
            action="failed",
            stack=stack_name,
            duration_seconds=time.monotonic() - started,
            error=f"{type(exc).__name__}: {exc}",
        )

    return BuildResult(
        slug=slug,
        action="built" if changed else "skipped",
        stack=stack_name,
        duration_seconds=time.monotonic() - started,
    )


# ---------------------------------------------------------------------------
# Python venv builder
# ---------------------------------------------------------------------------


def _build_python(
    workspace: Path, spec: StackSpec, build_section: dict[str, Any]
) -> bool:
    """Create .venv + ``pip install -e .``. Returns True when work was done."""
    pyproject = workspace / "pyproject.toml"
    if not pyproject.exists():
        # Nothing to install — caller may still launch a bare script.
        return False

    venv = workspace / ".venv"
    interp = _pick_python(spec, build_section)
    interp_marker = venv / ".heaxhub_python"

    needs_rebuild = (
        not (venv / "bin" / "python").exists()
        or not interp_marker.exists()
        or interp_marker.read_text(encoding="utf-8").strip() != interp
        or _stale(workspace / _SENTINEL, pyproject)
    )
    if not needs_rebuild:
        logger.info("python build skipped (up-to-date): %s", workspace)
        return False

    # Recreate the venv on interpreter change to avoid mixed binaries.
    if venv.exists() and (
        not interp_marker.exists()
        or interp_marker.read_text(encoding="utf-8").strip() != interp
    ):
        shutil.rmtree(venv)

    logger.info("creating venv with %s for %s", interp, workspace.name)
    _run([interp, "-m", "venv", str(venv)], cwd=workspace)
    interp_marker.write_text(interp)

    pip = str(venv / "bin" / "pip")
    _run([pip, "install", "--quiet", "--upgrade", "pip"], cwd=workspace)
    _run([pip, "install", "--quiet", "-e", "."], cwd=workspace)

    (workspace / _SENTINEL).touch()
    return True


def _pick_python(spec: StackSpec, build_section: dict[str, Any]) -> str:
    """Pick the Python interpreter command to use for ``python -m venv``."""
    wanted = (
        build_section.get("python_version")
        or (spec.extra or {}).get("python_version")
        or ""
    )
    candidates: list[str] = []
    if wanted:
        candidates.append(f"python{wanted}")
        major_minor = wanted.split(".")
        if len(major_minor) >= 2:
            candidates.append(f"python{major_minor[0]}.{major_minor[1]}")
    candidates += ["python3.12", "python3.11", "python3"]
    for cand in candidates:
        if shutil.which(cand):
            return cand
    return "python3"


# ---------------------------------------------------------------------------
# Node.js builder
# ---------------------------------------------------------------------------


def _build_nodejs(
    workspace: Path, spec: StackSpec, build_section: dict[str, Any]
) -> bool:
    """Run ``pnpm install`` + (optional) ``pnpm build``. Returns True when work
    was done."""
    pkg_json = workspace / "package.json"
    if not pkg_json.exists():
        return False

    sentinel = workspace / _SENTINEL
    if not _stale(sentinel, pkg_json) and (workspace / "node_modules").exists():
        # node_modules + sentinel newer than package.json → up to date.
        # We don't probe pnpm-lock.yaml strictly; pnpm itself catches drift.
        logger.info("nodejs build skipped (up-to-date): %s", workspace)
        return False

    pnpm = shutil.which("pnpm") or shutil.which("npm")
    if pnpm is None:
        raise FileNotFoundError(
            "pnpm/npm not on PATH — install with `corepack enable && "
            "corepack prepare pnpm@latest --activate`"
        )
    use_pnpm = pnpm.endswith("pnpm")

    install_cmd: list[str]
    if use_pnpm:
        install_cmd = [pnpm, "install", "--frozen-lockfile"]
        if not (workspace / "pnpm-lock.yaml").exists():
            # No lockfile yet — do a regular install so we don't fail hard.
            install_cmd = [pnpm, "install"]
    else:
        install_cmd = [pnpm, "ci"] if (workspace / "package-lock.json").exists() else [pnpm, "install"]

    logger.info("running %s in %s", install_cmd, workspace.name)
    _run(install_cmd, cwd=workspace)

    scripts = json.loads(pkg_json.read_text(encoding="utf-8")).get("scripts", {})
    if "build" in scripts:
        build_cmd = [pnpm, "build"] if use_pnpm else [pnpm, "run", "build"]
        logger.info("running %s in %s", build_cmd, workspace.name)
        _run(build_cmd, cwd=workspace)

    sentinel.touch()
    return True


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def _run(cmd: list[str], *, cwd: Path) -> None:
    """Run a command, raising on non-zero exit, with our timeout cap."""
    subprocess.run(  # noqa: S603
        cmd,
        cwd=str(cwd),
        check=True,
        timeout=_BUILD_TIMEOUT,
        capture_output=True,
    )


def _stale(sentinel: Path, source: Path) -> bool:
    """True when sentinel is missing or older than the source file."""
    if not sentinel.exists():
        return True
    try:
        return sentinel.stat().st_mtime < source.stat().st_mtime
    except FileNotFoundError:
        return True
