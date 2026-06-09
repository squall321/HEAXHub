"""Build the HWAXAgent program catalog (``programs.json``).

The agent-facing payload follows ``contracts/hwax-agent/manifest.schema.json``
(schema_version=1). Source data:

  - ``apps`` rows with ``app_type='windows_gui'`` and ``status != 'archived'``
  - For each app, the latest ``installer_packages`` row with ``os`` starting
    with ``'windows'`` (newest ``uploaded_at`` wins).
  - Optional ``apps.extra.windows_install`` block provides entry/lifecycle
    overrides; sensible defaults fill the rest.

Output is computed fresh each call (no cache yet — Phase 2). The caller is
responsible for adding ``ETag`` / ``If-None-Match`` headers if it wants the
launcher to skip downloads when nothing changed; we expose a stable
:func:`compute_etag` so the same payload always yields the same ETag.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.app import App, AppStatus, AppType
from app.db.models.installer_package import InstallerPackage
from app.db.models.windows_agent import WindowsAgent


_DEFAULT_PACKAGE_TYPE = "zip"  # most common for in-house tools per v2 plan §1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pick_latest_installer(
    db: Session, app_id: str
) -> InstallerPackage | None:
    return db.execute(
        select(InstallerPackage)
        .where(InstallerPackage.app_id == app_id)
        .where(InstallerPackage.os.like("windows%"))
        .order_by(InstallerPackage.uploaded_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _build_program(app: App, pkg: InstallerPackage) -> dict[str, Any]:
    """Translate one (App, InstallerPackage) pair into a program entry."""
    extra = app.extra or {}
    win = (extra.get("windows_install") or {}) if isinstance(extra, dict) else {}

    pkg_type = str(win.get("installer_type") or _DEFAULT_PACKAGE_TYPE).lower()
    if pkg_type not in {"zip", "exe", "msi", "msix"}:
        pkg_type = _DEFAULT_PACKAGE_TYPE

    entry_exec = win.get("entry") or f"{app.id}.exe"

    program: dict[str, Any] = {
        "id": app.id,
        "name": app.name,
        "version": str(pkg.version),
        "package": {
            "type": pkg_type,
            "url": f"/api/v1/installers/{pkg.id}/download",
            "sha256": pkg.sha256,
            "size_bytes": int(pkg.size_bytes) if pkg.size_bytes else 0,
        },
        "entry": {
            "executable": str(entry_exec),
            "args_template": list(win.get("args_template") or []),
            "working_dir": win.get("working_dir"),
        },
        "requirements": {
            "requires_admin": bool(win.get("requires_admin", False)),
            "min_windows": str(win.get("min_windows") or "10.0.19041"),
            "depends_on": list(win.get("depends_on") or []),
        },
        "ui": {
            "color_accent": str(win.get("color_accent") or "#f59e0b"),
            "show_in_tray": bool(win.get("show_in_tray", True)),
        },
        "tags": list(app.tags or []),
        "visibility": app.visibility.value if hasattr(app.visibility, "value") else str(app.visibility),
        "released_at": pkg.uploaded_at.isoformat() if pkg.uploaded_at else None,
    }
    if app.description:
        program["description"] = app.description
    if win.get("post_install_check"):
        program["lifecycle"] = {
            "post_install_check": win["post_install_check"],
            "rollback_on_failure": bool(win.get("rollback_on_failure", True)),
        }
    return program


def build_manifest(
    db: Session,
    *,
    agent: WindowsAgent | None = None,
) -> dict[str, Any]:
    """Build the full agent-facing manifest payload.

    ``agent`` is accepted for future per-agent filtering (e.g. pool-based
    visibility, depends_on resolution). The Phase 1 implementation returns
    the catalog for every windows_gui app the hub has; the launcher itself
    will filter by its own platform.
    """
    _ = agent  # Phase 2 — pool-based filter

    rows = db.execute(
        select(App)
        .where(App.app_type == AppType.WINDOWS_GUI)
        .where(App.status != AppStatus.ARCHIVED)
        .order_by(App.name.asc())
    ).scalars().all()

    programs: list[dict[str, Any]] = []
    for app in rows:
        pkg = _pick_latest_installer(db, app.id)
        if pkg is None:
            continue  # No Windows installer published yet.
        programs.append(_build_program(app, pkg))

    return {
        "schema_version": 1,
        "generated_at": _now_iso(),
        "programs": programs,
    }


def compute_etag(payload: dict[str, Any]) -> str:
    """Stable hash of the ``programs`` list — excludes ``generated_at`` so
    repeated calls with no catalog change return the same ETag."""
    blob = json.dumps(payload.get("programs", []), sort_keys=True, default=str)
    return '"' + hashlib.sha256(blob.encode()).hexdigest()[:16] + '"'


def is_servable_installer_app(app: App) -> bool:
    """Whether an app's installer may be served to a launcher / portal download.

    Mirrors the manifest's status gate (not ARCHIVED) so a retired app's
    installer can't be pulled via the download endpoints even by a valid
    launcher JWT (or the public portal route) that guesses its id. ``app_type``
    is deliberately NOT checked — the agent self-update download serves the
    ``hwax-agent`` app, which is ``desktop_agent`` (not ``windows_gui``).
    """
    return app.status != AppStatus.ARCHIVED


_SCHEMA_CACHE: dict[str, Any] | None = None


def _load_schema() -> dict[str, Any] | None:
    """Load ``contracts/hwax-agent/manifest.schema.json`` once, return None
    if it's not present (e.g. during tests in a slim checkout)."""
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is not None:
        return _SCHEMA_CACHE
    # contracts/ is at the repo root, three parents up from this file.
    schema_path = Path(__file__).resolve().parents[3] / "contracts" / "hwax-agent" / "manifest.schema.json"
    if not schema_path.is_file():
        return None
    try:
        with schema_path.open("r", encoding="utf-8") as fh:
            _SCHEMA_CACHE = json.load(fh)
    except Exception:
        return None
    return _SCHEMA_CACHE


def validate_against_contract(payload: dict[str, Any]) -> None:
    """Best-effort jsonschema validation against the contract.

    Imported lazily so the rest of the module works in environments without
    jsonschema. Raises ``jsonschema.ValidationError`` on mismatch; a no-op
    when the schema file isn't available.
    """
    schema = _load_schema()
    if schema is None:
        return
    try:
        import jsonschema  # noqa: PLC0415
    except ImportError:
        return
    jsonschema.Draft202012Validator(schema).validate(payload)
