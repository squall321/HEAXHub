"""Celery task for running a job end-to-end."""
from __future__ import annotations

from datetime import datetime, timezone

from app.core.logger import get_logger
from app.db.models.job import Job, JobStatus
from app.db.session import SessionLocal
from app.runners.registry import runner_for_target
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="job_tasks.run_job")
def run_job(job_id: str) -> dict[str, object]:
    """Pick the right runner, drive the lifecycle, persist results."""
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return {"ok": False, "error": "job not found"}
        if job.status == JobStatus.CANCELED:
            return {"ok": False, "error": "job already canceled"}
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        db.commit()
        target = job.execution_target
        job_snapshot = job  # we'll re-fetch later in fresh session

    runner = runner_for_target(target)
    result_status: str = "failed"
    error_message: str | None = None

    if getattr(runner, "is_async", False):
        # Fire-and-forget runners (e.g. SlurmRunner) submit to an external
        # scheduler and return an opaque handle. The job is finalized by a
        # separate poller, so we just persist the handle and return.
        try:
            handle = runner.start(job_snapshot)
        except Exception as exc:
            logger.exception("Job %s submission failed", job_id)
            with SessionLocal() as db:
                job = db.get(Job, job_id)
                if job is None:
                    return {"ok": False, "error": "job disappeared"}
                job.status = JobStatus.FAILED
                job.finished_at = datetime.now(timezone.utc)
                job.error_message = str(exc)
                db.commit()
            return {"ok": False, "job_id": job_id, "error": str(exc)}

        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if job is None:
                return {"ok": False, "error": "job disappeared"}
            meta = dict(job.runtime_meta or {})
            if runner.name == "slurm" and handle:
                meta["slurm_job_id"] = str(handle)
            elif handle is not None:
                meta[f"{runner.name}_handle"] = str(handle)
            job.runtime_meta = meta
            db.commit()
        return {"ok": True, "job_id": job_id, "status": "submitted", "handle": handle}

    try:
        exit_code = runner.start(job_snapshot)
        result = runner.collect_results(job_snapshot)
        result_status = result.status
        if exit_code not in (None, 0) and result_status == "success":
            result_status = "warning"
        summary = result.summary
    except Exception as exc:
        logger.exception("Job %s execution failed", job_id)
        error_message = str(exc)
        summary = {}
        result_status = "failed"

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return {"ok": False, "error": "job disappeared"}
        if job.status == JobStatus.CANCELED:
            # Preserve cancellation
            return {"ok": False, "canceled": True}
        finished = datetime.now(timezone.utc)
        job.finished_at = finished
        job.duration_sec = (
            int((finished - job.started_at).total_seconds()) if job.started_at else None
        )
        job.result_summary = summary
        if result_status == "success":
            job.status = JobStatus.SUCCESS
        elif result_status == "warning":
            job.status = JobStatus.SUCCESS
            job.error_message = "completed with warnings"
        else:
            job.status = JobStatus.FAILED
            job.error_message = error_message or "Job failed"
        db.commit()

    return {"ok": result_status != "failed", "job_id": job_id, "status": result_status}
