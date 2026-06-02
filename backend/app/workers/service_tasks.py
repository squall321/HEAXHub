"""Celery tasks for service-mode (long-running daemon) instances."""
from __future__ import annotations

from sqlalchemy import select

from app.core.logger import get_logger
from app.db.models.service_instance import ServiceInstance
from app.db.session import SessionLocal
from app.services import service_manager
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

_HEALTHCHECKABLE = ("starting", "healthy", "unhealthy")


@celery_app.task(name="service_tasks.service_health_loop")
def service_health_loop() -> dict[str, int]:
    """Iterate over non-stopped instances and run a health probe on each."""
    checked = 0
    with SessionLocal() as db:
        rows = list(
            db.execute(
                select(ServiceInstance).where(ServiceInstance.status.in_(_HEALTHCHECKABLE))
            ).scalars()
        )
        ids = [r.id for r in rows]
    for instance_id in ids:
        with SessionLocal() as db:
            try:
                service_manager.check_health(db, instance_id=instance_id)
            except Exception:
                logger.exception("health check failed for instance=%s", instance_id)
        checked += 1
    return {"checked": checked}
