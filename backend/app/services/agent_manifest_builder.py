"""HWAXAgent program-catalog (manifest) builder — NEXT_STEPS §2.3.

Produces the JSON document served at ``GET /api/v1/launcher-agents/manifest``
(contract: ``contracts/hwax-agent/manifest.schema.json``, ``schema_version: 1``).
The launcher renders ``programs`` as its tile grid and uses each program's
``package``/``entry``/``lifecycle`` to install and launch it.

Data sources (intersection):
  1. ``apps`` WHERE ``app_type='windows_gui'`` AND status is publishable
     (not DRAFT / not ARCHIVED — see ``_HIDDEN_STATUSES``).
  2. ``installer_packages`` WHERE ``os='windows-x64'`` for those apps; the
     newest by ``uploaded_at`` wins. An app with no Windows installer is
     omitted entirely.
  3. ``App.extra['windows_install']`` enriches entry / requirements / lifecycle
     / ui / category when present; otherwise schema-valid defaults are used.

``package.url`` is the HEAXHub download endpoint
``{base_url}/api/v1/installers/{id}/download`` (a 302 redirect, §2.5) rather
than a presigned object-storage URL — manifest cache lifetime and presigned
expiry would otherwise drift and cause "403 at install time" (backend-plan §6).
``base_url`` is supplied by the caller (the §2.4 router derives it from the
incoming request so it is correct whether the launcher reaches us directly or
through the portal's ``/heax-hub`` sub-path).
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.app import App, AppStatus, AppType
from app.db.models.installer_package import InstallerPackage

WINDOWS_OS = "windows-x64"
MANIFEST_SCHEMA_VERSION = 1

# Statuses hidden from the launcher catalog. DRAFT = not yet published,
# ARCHIVED = retired; BETA / STABLE / DEPRECATED remain installable. (App has no
# boolean `disabled` column — this is the publishable-status mapping of the
# plan's "disabled=False" predicate.)
_HIDDEN_STATUSES = frozenset({AppStatus.DRAFT, AppStatus.ARCHIVED})

# Whitelists so junk under App.extra['windows_install'] can never leak into the
# manifest (the contract sets additionalProperties:false on every sub-object).
_ENTRY_KEYS = ("executable", "args_template", "working_dir")
_REQUIREMENTS_KEYS = ("requires_admin", "min_windows", "depends_on")
_LIFECYCLE_KEYS = ("post_install_check", "rollback_on_failure")
_UI_KEYS = ("icon_url", "color_accent", "show_in_tray")

_PACKAGE_TYPE_BY_SUFFIX = (
    (".msix", "msix"),
    (".msi", "msi"),
    (".zip", "zip"),
    (".exe", "exe"),
)
_VALID_PACKAGE_TYPES = frozenset({"zip", "exe", "msi", "msix"})


def is_servable_installer_app(app: App) -> bool:
    """Whether an app's installer may be served to a launcher.

    Mirrors the manifest's status gate (not DRAFT / not ARCHIVED) so a draft or
    retired installer id can't be pulled out-of-band through the download
    endpoint. ``app_type`` is deliberately NOT checked: the agent self-update
    download goes through the same endpoint and the "hwax-agent" app need not be
    windows_gui.
    """
    return app.status not in _HIDDEN_STATUSES

_CACHE_TTL_SECONDS = 30.0
_manifest_cache: dict[str, tuple[float, dict[str, Any]]] = {}


# ── helpers ──────────────────────────────────────────────────────────────────


def _isoformat(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _infer_package_type(url: str) -> str:
    """Infer the contract Package.type from the installer URL/filename suffix."""
    lowered = url.lower().split("?", 1)[0]
    for suffix, kind in _PACKAGE_TYPE_BY_SUFFIX:
        if lowered.endswith(suffix):
            return kind
    return "exe"  # safest default: agent runs it as an executable installer


def _resolve_package_type(pkg: InstallerPackage) -> str:
    """Prefer the real ``package_format`` captured at upload; fall back to URL
    inference for legacy rows (NULL format). Disk-stored installer_urls have no
    extension, so without ``package_format`` inference always yields 'exe'."""
    fmt = (pkg.package_format or "").lower()
    if fmt in _VALID_PACKAGE_TYPES:
        return fmt
    return _infer_package_type(pkg.installer_url)


def _whitelist(src: Any, keys: Iterable[str]) -> dict[str, Any]:
    if not isinstance(src, dict):
        return {}
    return {k: src[k] for k in keys if k in src and src[k] is not None}


def _windows_install(app: App) -> dict[str, Any]:
    extra = app.extra if isinstance(app.extra, dict) else {}
    wi = extra.get("windows_install")
    return wi if isinstance(wi, dict) else {}


def _build_entry(wi: dict[str, Any], app_id: str) -> dict[str, Any]:
    entry = _whitelist(wi.get("entry"), _ENTRY_KEYS)
    if not entry.get("executable"):
        # No launch metadata yet (populated by the manifest v3 migration, §3.2).
        # Fall back to a conventional name so the program stays schema-valid.
        entry["executable"] = f"{app_id}.exe"
    return entry


def _build_program(app: App, pkg: InstallerPackage, *, base_url: str) -> dict[str, Any]:
    wi = _windows_install(app)

    package: dict[str, Any] = {
        "type": _resolve_package_type(pkg),
        "url": f"{base_url.rstrip('/')}/api/v1/installers/{pkg.id}/download",
        "sha256": pkg.sha256.lower(),
    }
    if pkg.size_bytes is not None:
        package["size_bytes"] = pkg.size_bytes

    program: dict[str, Any] = {
        "id": app.id,
        "name": app.name,
        "version": pkg.version,
        "package": package,
        "entry": _build_entry(wi, app.id),
    }

    if app.description:
        program["description"] = app.description[:1024]
    category = wi.get("category")
    if isinstance(category, str) and category:
        program["category"] = category[:64]
    released_at = _isoformat(pkg.uploaded_at)
    if released_at:
        program["released_at"] = released_at

    requirements = _whitelist(wi.get("requirements"), _REQUIREMENTS_KEYS)
    if requirements:
        program["requirements"] = requirements
    lifecycle = _whitelist(wi.get("lifecycle"), _LIFECYCLE_KEYS)
    if lifecycle:
        program["lifecycle"] = lifecycle
    ui = _whitelist(wi.get("ui"), _UI_KEYS)
    if ui:
        program["ui"] = ui

    if isinstance(app.tags, list) and app.tags:
        program["tags"] = [str(t)[:32] for t in app.tags][:50]
    if app.visibility is not None:
        program["visibility"] = app.visibility.value

    return program


# ── public api ───────────────────────────────────────────────────────────────


def build_manifest(
    db: Session, *, base_url: str, generated_at: datetime | None = None
) -> dict[str, Any]:
    """Build the manifest snapshot. Pure read; no caching (see ``cached_manifest``)."""
    apps = (
        db.execute(
            select(App).where(
                App.app_type == AppType.WINDOWS_GUI,
                App.status.notin_(_HIDDEN_STATUSES),
            )
        )
        .scalars()
        .all()
    )
    app_by_id = {a.id: a for a in apps}

    programs: list[dict[str, Any]] = []
    if app_by_id:
        pkgs = (
            db.execute(
                select(InstallerPackage)
                .where(
                    InstallerPackage.os == WINDOWS_OS,
                    InstallerPackage.app_id.in_(list(app_by_id.keys())),
                )
                .order_by(InstallerPackage.uploaded_at.desc())
            )
            .scalars()
            .all()
        )
        latest: dict[str, InstallerPackage] = {}
        for pkg in pkgs:
            latest.setdefault(pkg.app_id, pkg)  # desc order ⇒ first seen is newest
        for app_id, pkg in latest.items():
            programs.append(_build_program(app_by_id[app_id], pkg, base_url=base_url))

    programs.sort(key=lambda p: p["id"])
    gen = generated_at or datetime.now(timezone.utc)
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": _isoformat(gen),
        "programs": programs,
    }


def manifest_etag(manifest: dict[str, Any]) -> str:
    """Strong ETag over the ``programs`` list only.

    Excludes ``generated_at`` (which moves on every rebuild even when the catalog
    is unchanged), so an identical catalog yields a stable ETag and the launcher's
    conditional GET collapses to a 304. Returned in quoted HTTP-ETag form.
    """
    payload = json.dumps(
        manifest.get("programs", []), sort_keys=True, separators=(",", ":")
    )
    return '"' + hashlib.sha256(payload.encode("utf-8")).hexdigest() + '"'


def cached_manifest(db: Session, *, base_url: str) -> dict[str, Any]:
    """``build_manifest`` behind a 30s per-base_url TTL cache.

    Manifest requests are infrequent but each rebuild costs two DB queries; a
    short TTL avoids hammering the DB without making the catalog stale. The
    cache is keyed by ``base_url`` because the embedded download URLs differ per
    public origin (direct vs portal). ``generated_at`` is intentionally frozen
    for the TTL window — the launcher caches keyed by it.

    In a multi-worker deployment this cache is instance-local (backend-plan §6
    risk 6); the ≤30s divergence is acceptable for Phase 1.
    """
    now = time.monotonic()
    cached = _manifest_cache.get(base_url)
    if cached is not None and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]
    manifest = build_manifest(db, base_url=base_url)
    _manifest_cache[base_url] = (now, manifest)
    return manifest


def invalidate_cache() -> None:
    """Drop all cached manifests (call after a publish so launchers see it fast)."""
    _manifest_cache.clear()
