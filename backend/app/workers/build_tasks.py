"""Celery tasks for building app workspaces (venv / SIF / nodejs)."""
from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.core.logger import get_logger
from app.db.models.app_version import AppVersion, BuildStatus
from app.db.models.submission import Submission, SubmissionStatus
from app.db.session import SessionLocal
from app.runners.resource_limits import build_preexec
from app.services import interpreter_pool
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

# Builds are heavier than runtime jobs (pip download, image bake). Give them a
# more generous envelope while still capping runaway processes.
_BUILD_LIMITS: dict[str, object] = {
    "cpu_seconds": 7200,
    "memory_gb": 16,
    "file_size_gb": 20,
}


def _write_status(workspace: Path, payload: dict[str, object]) -> None:
    build_dir = workspace / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "status.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )


def _read_manifest_build_block(version_id: str) -> dict[str, Any]:
    """Return manifest.build dict from the AppVersion's manifest_snapshot (or {})."""
    try:
        with SessionLocal() as db:
            row = db.get(AppVersion, uuid.UUID(version_id))
            if row is None or not isinstance(row.manifest_snapshot, dict):
                return {}
            build = row.manifest_snapshot.get("build")
            return build if isinstance(build, dict) else {}
    except Exception:  # pragma: no cover — DB failure is non-fatal here
        logger.exception("could not read manifest_snapshot for version=%s", version_id)
        return {}


@celery_app.task(name="build_tasks.build_python_venv")
def build_python_venv(app_id: str, version_id: str) -> dict[str, object]:
    """Create venv and install Python dependencies for an app."""
    settings = get_settings()
    workspace = settings.workspace_root / app_id
    venv_dir = workspace / "venv"
    upstream = workspace / "upstream"
    build_log = workspace / "build" / "build.log"
    build_log.parent.mkdir(parents=True, exist_ok=True)

    with SessionLocal() as db:
        version = db.get(AppVersion, uuid.UUID(version_id))
        if version is None:
            return {"ok": False, "error": "version not found"}
        version.build_status = BuildStatus.BUILDING
        db.commit()

    # Resolve interpreter from manifest.build.python_version with fallbacks.
    build_block = _read_manifest_build_block(version_id)
    requested_py = build_block.get("python_version")
    requested_py_str = str(requested_py) if requested_py else None

    payload: dict[str, object] = {
        "app_id": app_id,
        "version_id": version_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "python_version_requested": requested_py_str,
    }
    success = False
    python_bin: str | None = None
    resolve_error: str | None = None
    try:
        python_bin = interpreter_pool.python_for(requested_py_str)
    except RuntimeError as exc:
        resolve_error = str(exc)
        payload["error"] = f"interpreter resolution failed: {exc}"
        logger.error(
            "build_python_venv: cannot resolve python %r for app=%s: %s",
            requested_py_str, app_id, exc,
        )

    if python_bin is not None:
        # Determine fallback "reason" classification.
        if requested_py_str is None:
            payload["reason"] = "no_version_requested_using_newest"
        elif python_bin.endswith(f"python{requested_py_str}") or requested_py_str in python_bin:
            payload["reason"] = "exact_or_minor_match"
        else:
            payload["reason"] = "fallback"
        payload["python_version_used"] = python_bin
        logger.info(
            "build_python_venv: using python=%s (requested=%r) for app=%s",
            python_bin, requested_py_str, app_id,
        )

    try:
        if python_bin is None:
            # Resolution already failed; jump to the finally / failure path.
            with build_log.open("w", encoding="utf-8") as log_fp:
                log_fp.write(
                    f"# Building venv for {app_id} (v {version_id})\n"
                    f"ERROR: {resolve_error}\n"
                )
            raise RuntimeError(resolve_error or "no python interpreter available")

        with build_log.open("w", encoding="utf-8") as log_fp:
            log_fp.write(f"# Building venv for {app_id} (v {version_id})\n")
            log_fp.write(
                f"# Python requested={requested_py_str!r} used={python_bin}\n"
            )
            log_fp.flush()

            # 1. Create venv with the resolved interpreter.
            subprocess.run(  # noqa: S603
                [python_bin, "-m", "venv", str(venv_dir)],
                check=True,
                stdout=log_fp,
                stderr=subprocess.STDOUT,
                timeout=settings.build_timeout_seconds,
                preexec_fn=build_preexec(_BUILD_LIMITS),
            )

            pip = venv_dir / "bin" / "pip"
            # 2. Upgrade pip
            subprocess.run(  # noqa: S603
                [str(pip), "install", "--upgrade", "pip", "wheel"],
                check=True,
                stdout=log_fp,
                stderr=subprocess.STDOUT,
                timeout=settings.build_timeout_seconds,
                preexec_fn=build_preexec(_BUILD_LIMITS),
            )

            # 3. Install requirements / project
            req = upstream / "requirements.txt"
            pyproject = upstream / "pyproject.toml"
            if req.exists():
                subprocess.run(  # noqa: S603
                    [str(pip), "install", "-r", str(req)],
                    check=True,
                    stdout=log_fp,
                    stderr=subprocess.STDOUT,
                    timeout=settings.build_timeout_seconds,
                    preexec_fn=build_preexec(_BUILD_LIMITS),
                )
            elif pyproject.exists():
                subprocess.run(  # noqa: S603
                    [str(pip), "install", str(upstream)],
                    check=True,
                    stdout=log_fp,
                    stderr=subprocess.STDOUT,
                    timeout=settings.build_timeout_seconds,
                    preexec_fn=build_preexec(_BUILD_LIMITS),
                )
            else:
                log_fp.write("No requirements.txt or pyproject.toml found; skipping deps.\n")

            log_fp.write("\n# Build completed successfully\n")
        success = True
    except subprocess.CalledProcessError as exc:
        payload["error"] = f"command failed (rc={exc.returncode})"
        logger.exception("build_python_venv failed for app=%s", app_id)
    except Exception as exc:
        payload["error"] = str(exc)
        logger.exception("build_python_venv error for app=%s", app_id)

    payload["finished_at"] = datetime.now(timezone.utc).isoformat()
    payload["ok"] = success
    _write_status(workspace, payload)

    with SessionLocal() as db:
        version = db.get(AppVersion, uuid.UUID(version_id))
        if version is not None:
            version.build_status = BuildStatus.SUCCESS if success else BuildStatus.FAILED
            version.build_log_path = str(build_log)
            version.venv_path = str(venv_dir) if success else None
            if success:
                version.released_at = datetime.now(timezone.utc)
            db.commit()

        sub_obj = db.execute(
            select(Submission)
            .where(Submission.proposed_app_id == app_id)
            .order_by(Submission.created_at.desc())
        ).scalars().first()
        if sub_obj is not None:
            sub_obj.status = SubmissionStatus.BUILT if success else SubmissionStatus.FAILED
            db.commit()

    return payload


# ---------------------------------------------------------------------------
# Node.js build
# ---------------------------------------------------------------------------


def _detect_package_manager(upstream: Path) -> str:
    """Prefer pnpm if pnpm-lock.yaml exists, then yarn, then npm."""
    if (upstream / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (upstream / "yarn.lock").exists():
        return "yarn"
    return "npm"


def _package_has_build_script(upstream: Path) -> bool:
    pkg = upstream / "package.json"
    if not pkg.exists():
        return False
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except Exception:
        return False
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return False
    return isinstance(scripts.get("build"), str) and bool(scripts["build"].strip())


def _resolve_node(
    requested_node_str: str | None, app_id: str, payload: dict[str, object]
) -> tuple[str | None, str | None]:
    """Resolve a node interpreter, recording outcomes into payload."""
    try:
        node_bin = interpreter_pool.node_for(requested_node_str)
    except RuntimeError as exc:
        payload["error"] = f"node resolution failed: {exc}"
        logger.error(
            "build_nodejs: cannot resolve node %r for app=%s: %s",
            requested_node_str, app_id, exc,
        )
        return None, str(exc)

    if node_bin is not None:
        payload["node_version_used"] = node_bin
        if requested_node_str is None:
            payload["reason"] = "no_version_requested_using_newest"
        else:
            payload["reason"] = "exact_or_minor_match"
    return node_bin, None


def _which_in_dir(cmd: str, node_bin_dir: Path | None) -> str | None:
    """Look for `cmd` in node_bin_dir first, then fall back to PATH."""
    if node_bin_dir is not None:
        candidate = node_bin_dir / cmd
        if candidate.exists():
            return str(candidate)
    return shutil.which(cmd)


def _resolve_pm_bin(
    pm: str, node_bin_dir: Path | None, app_id: str, payload: dict[str, object]
) -> tuple[str, str]:
    """Resolve package-manager binary, falling back to npm if missing."""
    pm_bin = _which_in_dir(pm, node_bin_dir)
    if pm_bin is not None:
        return pm, pm_bin

    fallback = _which_in_dir("npm", node_bin_dir)
    if fallback is None:
        raise RuntimeError(
            f"package manager '{pm}' not found and npm also missing"
        )
    logger.warning(
        "build_nodejs: %s not found; falling back to npm for app=%s", pm, app_id
    )
    payload["package_manager"] = "npm"
    payload["package_manager_fallback"] = True
    return "npm", fallback


def _make_node_env(node_bin_dir: Path | None) -> dict[str, str]:
    """Build subprocess env so the resolved node is first on PATH."""
    import os as _os
    base_path = _os.environ.get("PATH", "")
    new_path = f"{node_bin_dir}:{base_path}" if node_bin_dir is not None else base_path
    return {**_os.environ, "PATH": new_path}


def _node_install_cmd(pm: str, pm_bin: str, upstream: Path) -> list[str]:
    """Compute the install command for the chosen package manager."""
    if pm == "npm":
        if (upstream / "package-lock.json").exists():
            return [pm_bin, "ci"]
        return [pm_bin, "install"]
    if pm == "pnpm":
        if (upstream / "pnpm-lock.yaml").exists():
            return [pm_bin, "install", "--frozen-lockfile"]
        return [pm_bin, "install"]
    # yarn
    if (upstream / "yarn.lock").exists():
        return [pm_bin, "install", "--frozen-lockfile"]
    return [pm_bin, "install"]


def _run_install(
    pm: str, pm_bin: str, upstream: Path, log_fp, env: dict[str, str], timeout: int
) -> None:
    """Execute the dependency install step."""
    install_cmd = _node_install_cmd(pm, pm_bin, upstream)
    subprocess.run(  # noqa: S603
        install_cmd,
        cwd=str(upstream),
        check=True,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        env=env,
        preexec_fn=build_preexec(_BUILD_LIMITS),
    )


def _run_build(
    pm: str, pm_bin: str, upstream: Path, log_fp, env: dict[str, str], timeout: int
) -> None:
    """Execute the build step if package.json declares a build script."""
    if not _package_has_build_script(upstream):
        log_fp.write("No `scripts.build` in package.json; skipping build step.\n")
        return
    build_cmd = [pm_bin, "run", "build"] if pm == "npm" else [pm_bin, "build"]
    subprocess.run(  # noqa: S603
        build_cmd,
        cwd=str(upstream),
        check=True,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        env=env,
        preexec_fn=build_preexec(_BUILD_LIMITS),
    )


def _record_nodejs_status(
    version_id: str, app_id: str, success: bool, build_log: Path
) -> None:
    """Persist build status to AppVersion and the latest Submission."""
    with SessionLocal() as db:
        version = db.get(AppVersion, uuid.UUID(version_id))
        if version is not None:
            version.build_status = BuildStatus.SUCCESS if success else BuildStatus.FAILED
            version.build_log_path = str(build_log)
            if success:
                version.released_at = datetime.now(timezone.utc)
            db.commit()

        sub_obj = db.execute(
            select(Submission)
            .where(Submission.proposed_app_id == app_id)
            .order_by(Submission.created_at.desc())
        ).scalars().first()
        if sub_obj is not None:
            sub_obj.status = SubmissionStatus.BUILT if success else SubmissionStatus.FAILED
            db.commit()


@celery_app.task(name="build_tasks.build_nodejs")
def build_nodejs(app_id: str, version_id: str) -> dict[str, object]:
    """Install node dependencies (npm/pnpm/yarn) and run `<pm> run build` if defined."""
    settings = get_settings()
    workspace = settings.workspace_root / app_id
    upstream = workspace / "upstream"
    build_log = workspace / "build" / "build.log"
    build_log.parent.mkdir(parents=True, exist_ok=True)

    with SessionLocal() as db:
        version = db.get(AppVersion, uuid.UUID(version_id))
        if version is None:
            return {"ok": False, "error": "version not found"}
        version.build_status = BuildStatus.BUILDING
        db.commit()

    build_block = _read_manifest_build_block(version_id)
    requested_node = build_block.get("node_version")
    requested_node_str = str(requested_node) if requested_node else None

    payload: dict[str, object] = {
        "app_id": app_id,
        "version_id": version_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "node_version_requested": requested_node_str,
    }
    success = False
    node_bin, resolve_error = _resolve_node(requested_node_str, app_id, payload)

    pm = _detect_package_manager(upstream)
    payload["package_manager"] = pm

    # Bin dir of the resolved node — npm/pnpm/yarn shims usually live alongside it.
    node_bin_dir: Path | None = Path(node_bin).parent if node_bin is not None else None

    try:
        if node_bin is None:
            with build_log.open("w", encoding="utf-8") as log_fp:
                log_fp.write(
                    f"# Building nodejs for {app_id} (v {version_id})\n"
                    f"ERROR: {resolve_error}\n"
                )
            raise RuntimeError(resolve_error or "no node interpreter available")

        pm, pm_bin = _resolve_pm_bin(pm, node_bin_dir, app_id, payload)

        with build_log.open("w", encoding="utf-8") as log_fp:
            log_fp.write(f"# Building nodejs for {app_id} (v {version_id})\n")
            log_fp.write(
                f"# Node requested={requested_node_str!r} used={node_bin}\n"
            )
            log_fp.write(f"# Package manager: {pm} ({pm_bin})\n")
            log_fp.flush()

            env = _make_node_env(node_bin_dir)
            timeout = settings.build_timeout_seconds
            _run_install(pm, pm_bin, upstream, log_fp, env, timeout)
            _run_build(pm, pm_bin, upstream, log_fp, env, timeout)

            log_fp.write("\n# Build completed successfully\n")
        success = True
    except subprocess.CalledProcessError as exc:
        payload["error"] = f"command failed (rc={exc.returncode})"
        logger.exception("build_nodejs failed for app=%s", app_id)
    except Exception as exc:
        payload["error"] = str(exc)
        logger.exception("build_nodejs error for app=%s", app_id)

    payload["finished_at"] = datetime.now(timezone.utc).isoformat()
    payload["ok"] = success
    _write_status(workspace, payload)
    _record_nodejs_status(version_id, app_id, success, build_log)

    return payload


@celery_app.task(name="build_tasks.build_apptainer_sif")
def build_apptainer_sif(app_id: str, version_id: str) -> dict[str, object]:
    """Build an Apptainer SIF for the app by delegating to scripts/build_apptainer_sif.sh.

    The script is expected at <project_root>/scripts/build_apptainer_sif.sh. If it is
    missing or `apptainer` is unavailable, the task still marks the build successful
    when the SIF file is already present (developer-staged), otherwise records failure.
    """
    settings = get_settings()
    workspace = settings.workspace_root / app_id
    sif_path = workspace / "sif" / "app.sif"
    sif_path.parent.mkdir(parents=True, exist_ok=True)
    build_log = workspace / "build" / "build.log"
    build_log.parent.mkdir(parents=True, exist_ok=True)

    with SessionLocal() as db:
        version = db.get(AppVersion, uuid.UUID(version_id))
        if version is None:
            return {"ok": False, "error": "version not found"}
        version.build_status = BuildStatus.BUILDING
        db.commit()

    # Locate scripts/build_apptainer_sif.sh — assume project root is two levels up from settings.workspace_root.
    project_root = Path(settings.workspace_root).resolve().parent
    script = project_root / "scripts" / "build_apptainer_sif.sh"

    payload: dict[str, object] = {
        "app_id": app_id,
        "version_id": version_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    success = False

    if not script.exists():
        # Developer-staged SIF fallback: if an app.sif already exists, treat as success.
        if sif_path.exists():
            payload["note"] = (
                "build_apptainer_sif.sh missing; using pre-staged SIF at "
                f"{sif_path}."
            )
            success = True
        else:
            payload["error"] = f"build script missing at {script}"
    else:
        try:
            with build_log.open("w", encoding="utf-8") as log_fp:
                log_fp.write(
                    f"# Building Apptainer SIF for {app_id} via {script}\n"
                )
                log_fp.flush()
                subprocess.run(  # noqa: S603
                    ["bash", str(script), app_id],
                    check=True,
                    stdout=log_fp,
                    stderr=subprocess.STDOUT,
                    timeout=settings.build_timeout_seconds,
                    env={
                        **__import__("os").environ,
                        "WORKSPACE_ROOT": str(settings.workspace_root),
                        "APPTAINER_BIN": settings.apptainer_bin,
                    },
                    preexec_fn=build_preexec(_BUILD_LIMITS),
                )
            success = sif_path.exists()
            if not success:
                payload["error"] = "script finished without producing app.sif"
        except subprocess.CalledProcessError as exc:
            payload["error"] = f"script failed (rc={exc.returncode})"
            logger.exception("build_apptainer_sif failed for app=%s", app_id)
        except Exception as exc:
            payload["error"] = str(exc)
            logger.exception("build_apptainer_sif error for app=%s", app_id)

    payload["finished_at"] = datetime.now(timezone.utc).isoformat()
    payload["ok"] = success
    _write_status(workspace, payload)

    with SessionLocal() as db:
        version = db.get(AppVersion, uuid.UUID(version_id))
        if version is not None:
            version.build_status = BuildStatus.SUCCESS if success else BuildStatus.FAILED
            version.build_log_path = str(build_log)
            version.sif_path = str(sif_path) if success else None
            if success:
                version.released_at = datetime.now(timezone.utc)
            db.commit()

        sub_obj = db.execute(
            select(Submission)
            .where(Submission.proposed_app_id == app_id)
            .order_by(Submission.created_at.desc())
        ).scalars().first()
        if sub_obj is not None:
            sub_obj.status = SubmissionStatus.BUILT if success else SubmissionStatus.FAILED
            db.commit()

    return payload
