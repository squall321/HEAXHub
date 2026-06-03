"""Auto-discovery of integrations/ → App + AppVersion rows.

The ``integrations/`` directory at the repo root holds one workspace per
first-party app HEAXHub ships out of the box (heax-demo-cli, heax-demo-streamlit,
...). Each directory contains a ``.portal/manifest.yaml`` that declares an id,
a version, a stack, and a launch mode.

This scanner is run twice:

* once on uvicorn startup (see ``app.main:lifespan``), so a fresh deployment
  has its registry populated before the first request lands;
* every 5 minutes from Celery beat (see
  :mod:`app.workers.integration_tasks`), so version bumps committed to disk
  pick up without a reboot.

It is deliberately conservative:

* Only ``upsert`` semantics — we never delete an App row.
* No actual builds are kicked off here; ``current_version_id`` is set so the
  app immediately appears in the catalogue, and service-mode apps get a
  best-effort ``service_manager.start_service`` call. Build/runtime failures
  must not block discovery of the next integration.

If the manifest file is malformed, the unknown stack name, or the seed admin is
missing, we log and skip — the scan loop must always finish.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logger import get_logger
from app.db.models.app import (
    App,
    AppStatus,
    AppType,
    AppVisibility,
    ExecutionTarget,
)
from app.db.models.app_version import AppVersion, BuildStatus
from app.db.models.user import User, UserRole
from app.services import stack_resolver
from app.services.stack_resolver import StackSpec

logger = get_logger(__name__)


# Project root is two levels up from backend/app/services/.
# integrations/ sits at the same level as backend/.
_REPO_ROOT = Path(__file__).resolve().parents[3]
INTEGRATIONS_ROOT: Path = _REPO_ROOT / "integrations"


ScanAction = Literal["created", "updated", "unchanged", "skipped"]


@dataclass(slots=True)
class ScanResult:
    """Single integration directory outcome."""

    slug: str
    action: ScanAction
    app_id: str | None = None
    version: str | None = None
    reason: str | None = None  # populated on "skipped"


# ---------------------------------------------------------------------------
# Manifest + admin helpers
# ---------------------------------------------------------------------------


def _load_manifest(manifest_path: Path) -> dict[str, Any] | None:
    """Parse manifest.yaml. Returns None on any error (logs the cause)."""
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("manifest unreadable: %s (%s)", manifest_path, exc)
        return None
    if not isinstance(raw, dict):
        logger.warning("manifest must be a mapping: %s", manifest_path)
        return None
    return raw


def _system_user(db: Session) -> User | None:
    """Resolve a sane ``owner_user_id`` for system-discovered apps.

    Prefers the seeded admin email (configured via ``SEED_ADMIN_EMAIL``);
    falls back to the first admin in the DB. Returns ``None`` if no admin
    exists — in that case the scanner will skip writes until one is created.
    """
    # Local import keeps this module test-friendly when settings is monkeypatched.
    from app.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    seed_email = (settings.seed_admin_email or "").strip().lower()
    if seed_email:
        user = db.execute(
            select(User).where(User.email == seed_email)
        ).scalar_one_or_none()
        if user is not None:
            return user
    return db.execute(
        select(User).where(User.role == UserRole.ADMIN).order_by(User.created_at.asc())
    ).scalars().first()


def _coerce_app_type(value: str | None, default: AppType) -> AppType:
    if not value:
        return default
    try:
        return AppType(value)
    except ValueError:
        logger.warning("unknown app_type=%r, using %s", value, default.value)
        return default


def _coerce_execution_target(value: str | None, default: ExecutionTarget) -> ExecutionTarget:
    if not value:
        return default
    try:
        return ExecutionTarget(value)
    except ValueError:
        logger.warning(
            "unknown execution_target=%r, using %s", value, default.value
        )
        return default


def _resolve_visibility(manifest: dict[str, Any]) -> AppVisibility:
    """Read ``permissions.visibility`` from the manifest with a TEAM default."""
    perms = manifest.get("permissions") or {}
    raw = perms.get("visibility") if isinstance(perms, dict) else None
    if not raw:
        return AppVisibility.TEAM
    try:
        return AppVisibility(str(raw))
    except ValueError:
        logger.warning("unknown visibility=%r, using team", raw)
        return AppVisibility.TEAM


# ---------------------------------------------------------------------------
# Service-mode best-effort start
# ---------------------------------------------------------------------------


def _maybe_start_service(db: Session, *, app: App, version: AppVersion, stack: StackSpec) -> None:
    """If the stack runs in service mode and no live instance exists, start one.

    Failures are logged but never raised — discovery must succeed even when
    the service runtime isn't fully wired (e.g. Caddy down in dev).
    """
    if stack.launch_mode != "service":
        return

    # Lazy import to keep the test path lightweight (service_manager pulls in
    # httpx, secret_manager, port_allocator transitively).
    try:
        from app.db.models.service_instance import ServiceInstance  # noqa: PLC0415
        from app.services import service_manager  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        logger.debug("service_manager not importable — skipping auto-start for %s", app.id)
        return

    existing = db.execute(
        select(ServiceInstance)
        .where(ServiceInstance.app_id == app.id)
        .where(ServiceInstance.version_id == version.id)
        .where(ServiceInstance.status != "stopped")
    ).scalars().first()
    if existing is not None:
        logger.debug(
            "service already running for app=%s version=%s instance=%s",
            app.id, version.version, existing.id,
        )
        return

    try:
        instance = service_manager.start_service(db, app=app, version=version)
        logger.info(
            "auto-started service for app=%s version=%s instance=%s",
            app.id, version.version, instance.id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "auto-start failed for app=%s version=%s: %s",
            app.id, version.version, exc,
        )


# ---------------------------------------------------------------------------
# Single-dir processing
# ---------------------------------------------------------------------------


def _process_dir(
    db: Session, *, slug: str, integration_dir: Path, system_user_id: Any
) -> ScanResult:
    manifest_path = integration_dir / ".portal" / "manifest.yaml"
    manifest = _load_manifest(manifest_path)
    if manifest is None:
        return ScanResult(slug=slug, action="skipped", reason="manifest invalid")

    app_id = str(manifest.get("id") or "").strip()
    if not app_id:
        return ScanResult(slug=slug, action="skipped", reason="manifest.id missing")

    version_str = str(manifest.get("version") or "").strip() or "0.1.0"

    build_block = manifest.get("build") or {}
    stack_name = str(build_block.get("stack") or "").strip() if isinstance(build_block, dict) else ""
    if not stack_name:
        return ScanResult(
            slug=slug,
            action="skipped",
            app_id=app_id,
            reason="manifest.build.stack missing",
        )

    try:
        stack = stack_resolver.resolve(stack_name)
    except Exception as exc:  # noqa: BLE001 — NotFoundError, etc.
        return ScanResult(
            slug=slug,
            action="skipped",
            app_id=app_id,
            reason=f"unknown stack '{stack_name}': {exc}",
        )

    workspace_path = str(integration_dir)
    name = str(manifest.get("name") or app_id)
    description = manifest.get("description")
    tags = manifest.get("tags") or []
    visibility = _resolve_visibility(manifest)
    app_type = _coerce_app_type(manifest.get("app_type"), AppType(stack.app_type))
    execution_target = _coerce_execution_target(
        manifest.get("execution_target"), ExecutionTarget(stack.execution_target)
    )

    app = db.get(App, app_id)
    created_app = False
    if app is None:
        app = App(
            id=app_id,
            name=name,
            description=description,
            owner_user_id=system_user_id,
            app_type=app_type,
            execution_target=execution_target,
            status=AppStatus.STABLE,
            visibility=visibility,
            # No real upstream repo for these — they live in-tree. Use the
            # workspace path so source_fetcher / refresh tasks don't crash on
            # an empty URL.
            upstream_repo_url=f"file://{workspace_path}",
            tags=list(tags) if isinstance(tags, list) else [],
            workspace_path=workspace_path,
            extra={"stack": stack.name, "discovered": True},
        )
        db.add(app)
        db.flush()
        created_app = True
        logger.info("integrations scan: created app %s (stack=%s)", app_id, stack.name)
    else:
        # Keep workspace_path and visibility in sync with what's on disk.
        # Don't touch owner/tags/name on an existing row — operators may have
        # tweaked those through the admin UI.
        if app.workspace_path != workspace_path:
            app.workspace_path = workspace_path

    # Look for an existing version row with the same string version.
    existing_version = db.execute(
        select(AppVersion)
        .where(AppVersion.app_id == app_id)
        .where(AppVersion.version == version_str)
    ).scalars().first()

    if existing_version is None:
        version = AppVersion(
            app_id=app_id,
            version=version_str,
            manifest_snapshot=manifest,
            # In-tree integrations don't go through the build pipeline — mark
            # success so publish/run paths don't gate on build.
            build_status=BuildStatus.SUCCESS,
        )
        db.add(version)
        db.flush()
        app.current_version_id = version.id
        db.commit()
        db.refresh(app)
        db.refresh(version)
        _maybe_start_service(db, app=app, version=version, stack=stack)
        return ScanResult(
            slug=slug,
            action="created" if created_app else "updated",
            app_id=app_id,
            version=version_str,
        )

    # Same version already exists. Make sure current_version_id points at it
    # (it may have been cleared) and refresh the manifest snapshot in case
    # someone edited the YAML without bumping the version.
    if app.current_version_id != existing_version.id:
        app.current_version_id = existing_version.id
        db.commit()
        db.refresh(app)
    if existing_version.manifest_snapshot != manifest:
        existing_version.manifest_snapshot = manifest
        db.commit()
        db.refresh(existing_version)
    return ScanResult(
        slug=slug,
        action="created" if created_app else "unchanged",
        app_id=app_id,
        version=version_str,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def scan_integrations(db: Session, *, root: Path | None = None) -> list[ScanResult]:
    """Walk ``integrations/`` and reconcile App/AppVersion rows.

    ``root`` overrides the discovered ``INTEGRATIONS_ROOT`` — handy for tests.
    """
    base = (root or INTEGRATIONS_ROOT).resolve()
    results: list[ScanResult] = []
    if not base.exists():
        logger.info("integrations root absent: %s — nothing to scan", base)
        return results

    sys_user = _system_user(db)
    if sys_user is None:
        logger.warning(
            "no admin user found — skipping integrations scan. Seed admin or "
            "set SEED_ADMIN_EMAIL to an existing admin.",
        )
        return results

    # Sort for deterministic ordering, primarily for tests.
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        manifest = child / ".portal" / "manifest.yaml"
        if not manifest.exists():
            continue
        try:
            results.append(
                _process_dir(
                    db,
                    slug=child.name,
                    integration_dir=child,
                    system_user_id=sys_user.id,
                )
            )
        except Exception as exc:  # noqa: BLE001 — never break the loop
            logger.exception("scan failed for %s", child)
            db.rollback()
            results.append(
                ScanResult(slug=child.name, action="skipped", reason=str(exc))
            )
    return results
