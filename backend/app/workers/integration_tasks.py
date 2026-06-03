"""Celery task wrapping :func:`integrations_scanner.scan_integrations`.

Runs every 5 minutes via beat (see ``celery_app.beat_schedule``). The startup
path in ``app.main:lifespan`` calls the scanner synchronously so a fresh boot
has its registry populated before the first request; the periodic task picks
up version bumps committed to disk afterwards without an app restart.
"""
from __future__ import annotations

from app.core.logger import get_logger
from app.db.session import SessionLocal
from app.services import integrations_scanner
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


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
