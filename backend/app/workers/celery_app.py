"""Celery application factory."""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready

from app.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "heaxhub",
    broker=_settings.redis_url,
    backend=_settings.redis_url,
    include=[
        "app.workers.build_tasks",
        "app.workers.sync_tasks",
        "app.workers.job_tasks",
        "app.workers.webhook_tasks",
        "app.workers.service_tasks",
        "app.workers.gpu_tasks",
        "app.workers.ops_tasks",
        "app.workers.slurm_tasks",
        "app.workers.integration_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_max_tasks_per_child=200,
    broker_connection_retry_on_startup=True,
    # Beat schedule — operated by `celery -A ... beat`. The schedule is defined
    # here so a single source of truth governs both worker and beat processes;
    # beat itself must still be started separately (see Makefile `beat` target).
    beat_schedule={
        "sync-check-upstream-updates": {
            "task": "sync_tasks.check_upstream_updates",
            "schedule": 6 * 60 * 60.0,  # 6 hours
        },
        "service-health-loop": {
            "task": "service_tasks.service_health_loop",
            "schedule": 30.0,  # 30 seconds
        },
        "refresh-gpu-inventory": {
            "task": "gpu_tasks.refresh_gpu_inventory",
            "schedule": 3600.0,  # hourly
        },
        "ops-rotate-old-jobs": {
            "task": "ops_tasks.rotate_old_jobs",
            "schedule": crontab(hour=3, minute=0),  # 03:00 UTC daily
        },
        "slurm-poll-jobs": {
            "task": "slurm_tasks.poll_slurm_jobs",
            "schedule": 30.0,  # 30 seconds
        },
        "scan-integrations-every-5min": {
            "task": "integration_tasks.scan_integrations_periodic",
            "schedule": 300.0,  # 5 minutes
        },
        # Self-heal: re-register Caddy routes (lost on Caddy restart) + restart
        # dead service instances. Idempotent + build-free; fast no-op when all
        # integrations are healthy. Short interval so a route/instance loss is
        # repaired within ~45s instead of waiting on the 5-minute scan.
        "reconcile-integrations-every-45s": {
            "task": "integration_tasks.reconcile_integrations",
            "schedule": 45.0,
        },
    },
)


# ---------------------------------------------------------------------------
# Boot-time recovery — runs ~5 minutes after each worker comes up so service
# instances orphaned by a crash get re-checked. We avoid putting this in the
# beat_schedule because it should fire once per worker boot, not on a clock.
# ---------------------------------------------------------------------------


@worker_ready.connect
def _schedule_recovery_after_boot(sender, **_kwargs) -> None:  # type: ignore[no-untyped-def]
    """Queue a one-shot recovery sweep 5 minutes after the worker comes up."""
    try:
        # Lazy import: ops_tasks imports celery_app at module top, so a top-level
        # import here would form a cycle.
        from app.workers.ops_tasks import recover_service_instances  # noqa: PLC0415

        recover_service_instances.apply_async(countdown=5 * 60)
    except Exception:  # pragma: no cover — defensive: never block worker boot
        import logging

        logging.getLogger(__name__).exception("failed to schedule recover_service_instances")
