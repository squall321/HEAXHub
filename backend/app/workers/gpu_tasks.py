"""Celery tasks for the GPU inventory."""
from __future__ import annotations

from app.core.logger import get_logger
from app.db.session import SessionLocal
from app.services import gpu_manager
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="gpu_tasks.refresh_gpu_inventory")
def refresh_gpu_inventory() -> dict[str, int]:
    """Re-scan ``nvidia-smi`` and upsert ``gpu_devices`` rows."""
    with SessionLocal() as db:
        count = gpu_manager.register_gpus(db)
    return {"registered": count}
