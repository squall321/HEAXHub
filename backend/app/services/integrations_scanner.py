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
from app.services import audit_service, stack_resolver
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


@dataclass(slots=True)
class SourceSpec:
    """Parsed ``source:`` block from ``.portal/manifest.yaml``.

    Mirrors the subset of ``source_fetcher`` fields HEAXHub uses to fetch
    upstream code per-integration. When ``manifest.source`` is absent the
    workspace is the in-tree ``integrations/<slug>/`` directory and no
    SourceSpec is produced.
    """

    type: str = "git"
    url: str = ""
    ref: str = "main"
    subpath: str = ""

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any]) -> "SourceSpec | None":
        """Return a ``SourceSpec`` when the manifest carries a ``source`` block.

        Returns ``None`` if absent (legacy in-tree mode) or if the block is
        not a mapping. Other malformed values raise ``ValueError`` so the
        operator gets a loud, actionable error instead of a silently broken
        workspace.
        """
        if not isinstance(manifest, dict):
            return None
        block = manifest.get("source")
        if block is None:
            return None
        if not isinstance(block, dict):
            raise ValueError("manifest.source must be a mapping")

        stype = str(block.get("type") or "git").strip() or "git"
        url = str(block.get("url") or "").strip()
        ref = str(block.get("ref") or "main").strip() or "main"
        subpath = str(block.get("subpath") or "").strip()

        if stype == "git" and not url:
            raise ValueError("manifest.source.url is required for type=git")

        return cls(type=stype, url=url, ref=ref, subpath=subpath)


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


@dataclass(slots=True)
class BuildOutcome:
    """What ``_build_and_launch`` did so the caller can persist + report it.

    ``rebuilt`` is True only when a SIF/host build actually executed (cache
    miss). It stays False on cache hits, so an unchanged demo never gets
    re-reported as 'updated'.
    """

    status: BuildStatus
    rebuilt: bool = False
    sif_path: str | None = None
    build_log_path: str | None = None
    commit: str | None = None
    error: str | None = None


def _build_and_launch(
    db: Session,
    *,
    app: App,
    version: AppVersion,
    stack: StackSpec,
    integration_dir: Path,
    manifest: dict[str, Any],
    commit_gated: bool = False,
) -> BuildOutcome:
    """Fetch source, build SIF (or fall back to legacy host-PATH builder),
    then launch. Records the build outcome on the AppVersion row.

    ``commit_gated`` (used on the same-version rescan path) narrows the rebuild
    trigger to a real upstream commit move: if the fetch reports the commit is
    unchanged and a built SIF already exists, the expensive SIF build is
    skipped even when cosmetic manifest fields drifted. This is the
    conservative trigger the operators asked for — a description/tag edit must
    not stampede a rebuild of every demo. New versions (``commit_gated=False``)
    always build.

    When manifest.source is present:
      1) integration_fetcher.fetch_for_integration clones the upstream into
         var/integration_workspaces/<slug>/upstream/
      2) integration_sif_builder.build_sif renders the stack .def template
         and invokes apt_runner.run_build → var/sifs/<slug>.sif (atomic swap)
      3) integration_launcher.launch is called with sif_path so it dispatches
         via apptainer instance start/exec.

    When manifest.source is absent the legacy in-tree builder + host-PATH
    launcher runs (backwards compatibility with pre-2.0 manifests).

    The AppVersion row is moved to BUILDING before the build and finalized to
    SUCCESS (+ sif_path/build_log_path/git_commit_hash) or FAILED
    (+ build_log_path) afterwards. All steps are best-effort: failures are
    logged + persisted, never raised, so discovery of the next integration
    continues. The returned :class:`BuildOutcome` lets the caller decide
    whether a same-version scan counts as 'updated' (rebuilt) or 'unchanged'.
    """
    slug = integration_dir.name

    # ── parse source block, if any ────────────────────────────────────
    try:
        source = SourceSpec.from_manifest(manifest)
    except ValueError as exc:
        logger.warning("source block invalid for %s: %s", app.id, exc)
        return _record_build(
            db, app=app, version=version, manifest=manifest,
            outcome=BuildOutcome(status=BuildStatus.FAILED, error=str(exc)),
        )

    # Mark BUILDING up-front so a crash mid-build leaves an honest status
    # behind (instead of the old hard-coded SUCCESS).
    if version.build_status != BuildStatus.BUILDING:
        version.build_status = BuildStatus.BUILDING
        db.commit()

    sif_path: str | None = None
    build_log_path: str | None = None
    commit: str | None = None
    rebuilt = False

    if source is not None and source.url:
        # ── fetch upstream into managed workspace ─────────────────────
        try:
            from app.services import integration_fetcher  # noqa: PLC0415
            fr = integration_fetcher.fetch_for_integration(slug, source)
            if fr.action == "failed":
                logger.warning("fetch failed for %s: %s", app.id, fr.error)
                return _record_build(
                    db, app=app, version=version, manifest=manifest,
                    outcome=BuildOutcome(
                        status=BuildStatus.FAILED,
                        error=f"fetch failed: {fr.error}",
                    ),
                )
            commit = fr.commit
            if fr.action in {"cloned", "updated"}:
                logger.info("integration fetched: %s (%s) commit=%s",
                            app.id, fr.action, (fr.commit or "?")[:8])
        except Exception as exc:  # noqa: BLE001
            logger.exception("fetcher crashed for %s: %s", app.id, exc)
            return _record_build(
                db, app=app, version=version, manifest=manifest,
                outcome=BuildOutcome(
                    status=BuildStatus.FAILED, error=f"fetcher crashed: {exc}"
                ),
            )

        # ── conservative gate on the rescan path ──────────────────────
        # When commit-gated, a fetch that didn't move the commit (action
        # "skipped") and an already-built SIF means there is nothing new to
        # build — skip straight to relaunch. This sidesteps build_sif's
        # whole-manifest hash, which is too eager (a description edit would
        # otherwise rebuild). We still backfill metadata so the row catches up.
        if commit_gated and fr.action == "skipped":
            existing_sif = _existing_sif_path(slug)
            if existing_sif is not None:
                logger.debug("commit unchanged for %s — skipping SIF build", app.id)
                outcome = _record_build(
                    db, app=app, version=version, manifest=manifest,
                    outcome=BuildOutcome(
                        status=BuildStatus.SUCCESS,
                        rebuilt=False,
                        sif_path=str(existing_sif),
                        commit=commit,
                    ),
                )
                _maybe_launch(
                    db, app=app, stack=stack, integration_dir=integration_dir,
                    manifest=manifest, source=source, sif_path=str(existing_sif),
                )
                return outcome

        # ── build per-demo SIF via apptainer ──────────────────────────
        try:
            from app.services import integration_sif_builder  # noqa: PLC0415
            sr = integration_sif_builder.build_sif(slug, manifest, fr)
            commit = sr.commit or commit
            if sr.log_path is not None:
                build_log_path = str(sr.log_path)
            if sr.action == "failed":
                logger.warning("SIF build failed for %s: %s", app.id,
                               (sr.error or "")[:300])
                return _record_build(
                    db, app=app, version=version, manifest=manifest,
                    outcome=BuildOutcome(
                        status=BuildStatus.FAILED,
                        build_log_path=build_log_path,
                        commit=commit,
                        error=sr.error,
                    ),
                )
            if sr.action == "built":
                logger.info("SIF built: %s (%s)", app.id, sr.sif)
                rebuilt = True
            elif sr.action == "skipped":
                logger.debug("SIF skipped for %s (cached or no template)", app.id)
            sif_path = str(sr.sif) if sr.sif else None
        except Exception as exc:  # noqa: BLE001
            logger.exception("sif_builder crashed for %s: %s", app.id, exc)
            return _record_build(
                db, app=app, version=version, manifest=manifest,
                outcome=BuildOutcome(
                    status=BuildStatus.FAILED, error=f"sif_builder crashed: {exc}"
                ),
            )
    else:
        # ── legacy host-PATH builder for in-tree integrations ─────────
        try:
            from app.services import integration_builder  # noqa: PLC0415
            br = integration_builder.build(integration_dir, manifest=manifest)
            if br.log_path:
                build_log_path = str(br.log_path)
            if br.action == "failed":
                logger.warning("build failed for %s: %s", app.id, br.error)
                return _record_build(
                    db, app=app, version=version, manifest=manifest,
                    outcome=BuildOutcome(
                        status=BuildStatus.FAILED,
                        build_log_path=build_log_path,
                        error=br.error,
                    ),
                )
            if br.action == "built":
                logger.info("integration built: %s (stack=%s, %.1fs)",
                            app.id, br.stack, br.duration_seconds)
                rebuilt = True
        except Exception as exc:  # noqa: BLE001
            logger.exception("builder crashed for %s: %s", app.id, exc)
            return _record_build(
                db, app=app, version=version, manifest=manifest,
                outcome=BuildOutcome(
                    status=BuildStatus.FAILED, error=f"builder crashed: {exc}"
                ),
            )

    # Build succeeded (or was a cache hit) → record SUCCESS before launching
    # so a launch crash doesn't leave the row stuck on BUILDING.
    outcome = _record_build(
        db, app=app, version=version, manifest=manifest,
        outcome=BuildOutcome(
            status=BuildStatus.SUCCESS,
            rebuilt=rebuilt,
            sif_path=sif_path,
            build_log_path=build_log_path,
            commit=commit,
        ),
    )

    _maybe_launch(
        db, app=app, stack=stack, integration_dir=integration_dir,
        manifest=manifest, source=source, sif_path=sif_path,
    )
    return outcome


def _existing_sif_path(slug: str) -> Path | None:
    """Return the built SIF for ``slug`` if it exists on disk, else None."""
    from app.services import integration_sif_builder  # noqa: PLC0415

    candidate = integration_sif_builder.SIF_DIR / f"{slug}.sif"
    return candidate if candidate.exists() else None


def _maybe_launch(
    db: Session,
    *,
    app: App,
    stack: StackSpec,
    integration_dir: Path,
    manifest: dict[str, Any],
    source: "SourceSpec | None",
    sif_path: str | None,
) -> None:
    """Launch service-mode integrations (best-effort, never raises).

    The launcher's own ``already_running`` liveness probe makes this idempotent
    for healthy services, so it is safe to call on every scan.
    """
    if stack.launch_mode != "service":
        return
    try:
        from app.services import integration_launcher  # noqa: PLC0415
        from dataclasses import asdict  # noqa: PLC0415
        lr = integration_launcher.launch(
            integration_dir, manifest=manifest, db=db,
            slug=integration_dir.name,
            source=asdict(source) if source else None,
            sif_path=Path(sif_path) if sif_path else None,
        )
        if lr.action == "failed":
            logger.warning("launch failed for %s: %s", app.id, lr.error)
        elif lr.action == "started":
            logger.info("integration started: %s pid=%s port=%s",
                        app.id, lr.pid, lr.port)
        else:
            logger.debug("integration %s: %s", app.id, lr.action)
    except Exception as exc:  # noqa: BLE001
        logger.exception("launcher crashed for %s: %s", app.id, exc)


def _record_build(
    db: Session,
    *,
    app: App,
    version: AppVersion,
    manifest: dict[str, Any],
    outcome: BuildOutcome,
) -> BuildOutcome:
    """Persist the build result onto the AppVersion row + notify on failure.

    Only writes columns we have real values for (never blanks an existing
    sif_path on a cache-hit success). Best-effort: a DB error here is logged,
    not raised.
    """
    try:
        version.build_status = outcome.status
        if outcome.sif_path:
            version.sif_path = outcome.sif_path
        if outcome.build_log_path:
            version.build_log_path = outcome.build_log_path
        if outcome.commit:
            version.git_commit_hash = outcome.commit[:64]
        db.commit()
        db.refresh(version)
    except Exception as exc:  # noqa: BLE001
        logger.exception("failed to persist build status for %s: %s", app.id, exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass

    if outcome.status == BuildStatus.FAILED:
        _notify_build_failed(
            db, app=app, version=version, manifest=manifest,
            log_path=outcome.build_log_path, error=outcome.error,
        )
    return outcome


def _notify_build_failed(
    db: Session,
    *,
    app: App,
    version: AppVersion,
    manifest: dict[str, Any],
    log_path: str | None,
    error: str | None,
) -> None:
    """Tell an operator a build failed: email if configured, else audit log.

    Always writes an audit ``integration.build.failed`` entry as the durable
    record; the email is a best-effort heads-up on top of it.
    """
    meta = {
        "app_id": app.id,
        "version": version.version,
        "log_path": log_path,
        "error": (error or "")[:1000],
    }
    audit_service.safe_log(
        db,
        actor_user_id=None,
        action="integration.build.failed",
        target_type="app_version",
        target_id=str(version.id),
        meta=meta,
    )

    # Best-effort operator email. Never let a mail failure escape.
    try:
        from app.config import get_settings  # noqa: PLC0415
        from app.services import mail_service  # noqa: PLC0415

        settings = get_settings()
        to = (settings.seed_admin_email or "").strip()
        if not to:
            return
        body = (
            f"Integration build FAILED.\n\n"
            f"app: {app.id}\nversion: {version.version}\n"
            f"log: {log_path or '(none)'}\n\n"
            f"--- error ---\n{(error or '(no detail)')[:2000]}\n"
        )
        mail_service.send_mail(
            to=to,
            subject=f"[HEAXHub] integration build failed: {app.id}@{version.version}",
            body=body,
        )
    except Exception:  # noqa: BLE001
        logger.exception("build-failed notification mail failed for %s", app.id)


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
            # Start PENDING; _build_and_launch flips it to BUILDING and then
            # SUCCESS/FAILED based on what actually happens. (Was hard-coded
            # SUCCESS before the build had even run.)
            build_status=BuildStatus.PENDING,
        )
        db.add(version)
        db.flush()
        app.current_version_id = version.id
        db.commit()
        db.refresh(app)
        db.refresh(version)
        _build_and_launch(
            db, app=app, version=version, stack=stack,
            integration_dir=integration_dir, manifest=manifest,
        )
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

    # Trigger expansion: re-run build/launch even when the version string is
    # unchanged, so a source push picks up without a manual version bump.
    # ``commit_gated=True`` keeps this conservative — a SIF rebuild fires only
    # when the upstream commit actually moves, never on cosmetic manifest edits
    # (description/tags). That avoids stampeding a rebuild of every demo. The
    # downstream content hashes (build_sif / integration_builder) still guard
    # the build itself, and the launcher's already_running probe makes the
    # relaunch idempotent for healthy services.
    outcome = _build_and_launch(
        db, app=app, version=existing_version, stack=stack,
        integration_dir=integration_dir, manifest=manifest,
        commit_gated=True,
    )

    if created_app:
        action: ScanAction = "created"
    elif outcome.rebuilt:
        action = "updated"
    else:
        action = "unchanged"
    return ScanResult(
        slug=slug,
        action=action,
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
