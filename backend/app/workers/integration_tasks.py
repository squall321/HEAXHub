"""Celery tasks for the integrations subsystem.

Two periodic passes run here (see ``celery_app.beat_schedule``):

* ``scan_integrations_periodic`` — discovery + build. Walks ``integrations/``,
  upserts App/AppVersion rows, and on a NEW version string builds + launches.
  The startup path in ``app.main:lifespan`` calls the scanner synchronously so a
  fresh boot has its registry populated before the first request.

* ``reconcile_integrations`` — self-heal. Caddy routes live only in the admin
  API's in-memory config, so a Caddy restart wipes every ``/apps/<id>`` route
  even while the upstream service is still alive. The scanner does NOT fix this:
  it early-returns on the unchanged-version path and never re-invokes
  ``launch()``. This reconcile pass re-invokes ``launch()`` for every
  service/static/proxy-mode integration on a short interval. ``launch()`` is
  idempotent — when the instance is alive + healthy and the route is present it
  re-PUTs the (idempotent) Caddy route and returns ``already_running``; when the
  instance is dead it cold-starts it (this is the P0-1 instance auto-recovery).
  No builds are triggered here — that is the scanner's job exclusively.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.core.logger import get_logger
from app.db.session import SessionLocal
from app.services import integrations_scanner
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

# Same path rules the scanner / sif_builder use so reconcile resolves the exact
# same artifacts without re-deriving them through the build pipeline.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_INTEGRATIONS_ROOT = _REPO_ROOT / "integrations"
_SIF_DIR = _REPO_ROOT / "var" / "sifs"

# launch() handles these by registering a route (or cold-starting a process).
# Everything else (url/iframe/job-runner) has nothing for reconcile to restore.
_RECONCILE_MODES = frozenset({"service", "static", "proxy"})


@celery_app.task(name="integration_tasks.scan_integrations_periodic")
def scan_integrations_periodic() -> dict[str, object]:
    """Run the integration discovery pass and return a summary dict."""
    with SessionLocal() as db:
        results = integrations_scanner.scan_integrations(db)

    summary: dict[str, int] = {}
    for r in results:
        summary[r.action] = summary.get(r.action, 0) + 1
    logger.info("integrations scan: %s", summary)
    return {
        "count": len(results),
        "by_action": summary,
        "items": [
            {
                "slug": r.slug,
                "action": r.action,
                "app_id": r.app_id,
                "version": r.version,
                "reason": r.reason,
            }
            for r in results
        ],
    }


def reconcile_integrations() -> dict[str, object]:
    """Re-register routes + restart dead instances for live integrations.

    Idempotent and build-free. For every ``integrations/<slug>`` whose manifest
    launch mode is service/static/proxy, re-invoke ``integration_launcher.launch``
    with the SAME path rules the scanner uses (workspace = the integration dir,
    sif = ``var/sifs/<slug>.sif`` when present). ``launch`` itself decides
    whether that is a no-op (already_running) or a recovery (started/failed).

    Returns a summary dict counting results by ``launch`` action so the caller
    (celery task / admin endpoint / startup) can surface what happened.
    """
    # Lazy import — integration_launcher pulls in apt_runner/httpx and we want
    # this module importable by celery_app's include list without that weight
    # until the task actually runs.
    from app.services import integration_launcher  # noqa: PLC0415

    by_action: dict[str, int] = {}
    items: list[dict[str, Any]] = []

    if not _INTEGRATIONS_ROOT.exists():
        logger.info("reconcile: integrations root absent: %s", _INTEGRATIONS_ROOT)
        return {"count": 0, "by_action": by_action, "items": items}

    with SessionLocal() as db:
        for child in sorted(p for p in _INTEGRATIONS_ROOT.iterdir() if p.is_dir()):
            slug = child.name
            manifest_path = child / ".portal" / "manifest.yaml"
            if not manifest_path.exists():
                continue
            try:
                manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            except Exception as exc:  # noqa: BLE001 — never break the loop
                logger.warning("reconcile: manifest load failed for %s: %s", slug, exc)
                continue
            if not isinstance(manifest, dict):
                continue

            mode = (manifest.get("launch") or {}).get("mode")
            if mode not in _RECONCILE_MODES:
                continue

            sif_path = _SIF_DIR / f"{slug}.sif"
            sif_arg = sif_path if sif_path.exists() else None

            # Pass the manifest's source: block through so launch() (static mode
            # in particular) resolves artefacts under the fetched upstream
            # workspace (var/integration_workspaces/<slug>/upstream) rather than
            # the manifest-only integrations/<slug> dir, which has no build
            # output. Without this, static_html-style demos 404 on reconcile.
            source_block = (
                manifest.get("source")
                if isinstance(manifest.get("source"), dict)
                else None
            )

            try:
                lr = integration_launcher.launch(
                    child, manifest=manifest, db=db, slug=slug,
                    source=source_block, sif_path=sif_arg,
                )
            except Exception as exc:  # noqa: BLE001 — launch is best-effort
                logger.exception("reconcile: launch crashed for %s", slug)
                by_action["failed"] = by_action.get("failed", 0) + 1
                items.append({"slug": slug, "action": "failed", "error": str(exc)})
                continue

            by_action[lr.action] = by_action.get(lr.action, 0) + 1
            items.append({
                "slug": slug,
                "action": lr.action,
                "port": lr.port,
                "base_path": lr.base_path,
                "error": lr.error,
            })
            if lr.action == "started":
                logger.info("reconcile: recovered %s port=%s pid=%s", slug, lr.port, lr.pid)
            elif lr.action == "failed":
                logger.warning("reconcile: %s failed: %s", slug, lr.error)

    logger.info("reconcile integrations: %s", by_action)
    return {"count": len(items), "by_action": by_action, "items": items}


@celery_app.task(name="integration_tasks.reconcile_integrations")
def reconcile_integrations_periodic() -> dict[str, object]:
    """Beat-scheduled wrapper around :func:`reconcile_integrations`."""
    return reconcile_integrations()
