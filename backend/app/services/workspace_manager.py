"""Workspace + job storage directory helpers."""
from __future__ import annotations

import os
import re
from pathlib import Path

from app.config import get_settings
from app.core.errors import ValidationError
from app.core.logger import get_logger

_APP_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


def _ensure_valid_app_id(app_id: str) -> None:
    if not _APP_ID_RE.match(app_id):
        raise ValidationError(f"Invalid app_id '{app_id}'")


def workspace_root() -> Path:
    return get_settings().workspace_root


def job_storage_root() -> Path:
    return get_settings().job_storage_root


def app_workspace_path(app_id: str) -> Path:
    _ensure_valid_app_id(app_id)
    return (workspace_root() / app_id).resolve()


def create_app_workspace(app_id: str) -> Path:
    """Create the full directory skeleton for an app workspace.

    {root}/{app_id}/
      upstream/  overlay/.portal/  venv/  sif/  build/
    """
    base = app_workspace_path(app_id)
    base.mkdir(parents=True, exist_ok=True)
    for sub in ("upstream", "overlay/.portal", "venv", "sif", "build"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    return base


def job_storage_path(job_id: str, *, created_at: str | None = None) -> Path:
    """job_storage/{YYYY}/{MM}/{job_id}/ — created_at YYYYMMDD prefix expected in job_id."""
    # job_id format: job_YYYYMMDD_NNNN
    parts = job_id.split("_")
    if len(parts) >= 3 and len(parts[1]) == 8 and parts[1].isdigit():
        year = parts[1][:4]
        month = parts[1][4:6]
    elif created_at:
        year = created_at[:4]
        month = created_at[4:6]
    else:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        year = f"{now.year:04d}"
        month = f"{now.month:02d}"
    return (job_storage_root() / year / month / job_id).resolve()


def create_job_storage(job_id: str) -> Path:
    base = job_storage_path(job_id)
    base.mkdir(parents=True, exist_ok=True)
    for sub in ("input", "work", "output", "logs"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    return base


def safe_join(base: Path, rel: str) -> Path:
    """Join `base + rel`, ensuring the result stays within base (no .. traversal)."""
    candidate = (base / rel).resolve()
    base_resolved = base.resolve()
    try:
        candidate.relative_to(base_resolved)
    except ValueError as exc:
        raise ValidationError(f"Path traversal blocked: {rel}") from exc
    return candidate


def ensure_within_storage(path: Path) -> None:
    """Raise unless `path` is under JOB_STORAGE_ROOT or WORKSPACE_ROOT."""
    resolved = path.resolve()
    for root in (job_storage_root(), workspace_root()):
        try:
            resolved.relative_to(root.resolve())
            return
        except ValueError:
            continue
    raise ValidationError(f"Path escapes managed roots: {resolved}")


def lock_upstream_readonly(workspace: Path) -> None:
    """Best-effort `chmod -R a-w upstream/` on the workspace's upstream dir.

    Failures are logged but never raised — the upstream lock is a safety hint, not
    a hard guarantee. Symlinks are skipped (chmod follows them and may break
    sibling directories).
    """
    log = get_logger(__name__)
    upstream = workspace / "upstream"
    if not upstream.exists():
        return
    # If upstream is a symlink (local_path source with sync=symlink), do nothing.
    if upstream.is_symlink():
        return
    try:
        for root, dirs, files in os.walk(upstream):
            for name in dirs + files:
                p = Path(root) / name
                if p.is_symlink():
                    continue
                try:
                    mode = p.stat().st_mode
                    p.chmod(mode & ~0o222)
                except OSError:
                    continue
        try:
            mode = upstream.stat().st_mode
            upstream.chmod(mode & ~0o222)
        except OSError:
            pass
    except Exception:
        log.exception("lock_upstream_readonly failed for %s", workspace)


def list_files(base: Path) -> list[dict[str, object]]:
    """Recursively list files under base, returning relative paths + sizes."""
    if not base.exists():
        return []
    out: list[dict[str, object]] = []
    for root, _dirs, files in os.walk(base):
        for fname in files:
            full = Path(root) / fname
            rel = full.relative_to(base)
            out.append(
                {
                    "path": str(rel).replace(os.sep, "/"),
                    "size": full.stat().st_size,
                }
            )
    return out
