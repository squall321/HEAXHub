"""Periodic Celery task that polls Slurm for in-flight HEAXHub jobs.

Strategy:

1. Read the set ``slurm:active_jobs`` from Redis — populated by
   :func:`SlurmRunner._store_slurm_id` at submit time.
2. For each ``(heaxhub_job_id, slurm_job_id)`` pair, check:

   * ``squeue --job <id> --noheader --format=%T`` for live state
     (``PENDING`` / ``RUNNING`` / ``COMPLETING`` etc).
   * If ``squeue`` returns nothing (job left the queue), fall back to
     ``sacct -j <id> --format=State,ExitCode --parsable2 --noheader``.

3. Translate Slurm states → :class:`JobStatus`:

   * ``PENDING``/``CONFIGURING``        → keep ``QUEUED``
   * ``RUNNING``/``COMPLETING``         → ``RUNNING``
   * ``COMPLETED``                      → ``SUCCESS`` + ``collect_results``
   * ``FAILED``/``NODE_FAIL``/``OUT_OF_MEMORY``/``TIMEOUT``/``BOOT_FAIL``
                                        → ``FAILED``
   * ``CANCELLED`` (any flavour)        → ``CANCELED``

4. On terminal states we ``_forget_slurm_id`` so the next poll is cheap.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from typing import Any

import redis

from app.config import get_settings
from app.core.logger import get_logger
from app.db.models.job import Job, JobStatus
from app.db.session import SessionLocal
from app.runners.slurm_runner import SlurmRunner, _forget_slurm_id, _get_slurm_id
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


_RUNNING_STATES = {"RUNNING", "COMPLETING"}
_QUEUED_STATES = {"PENDING", "CONFIGURING", "REQUEUED", "RESV_DEL_HOLD", "RESIZING"}
_SUCCESS_STATES = {"COMPLETED"}
_FAILED_STATES = {
    "FAILED",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "TIMEOUT",
    "BOOT_FAIL",
    "DEADLINE",
    "PREEMPTED",
}
_CANCELED_STATES = {"CANCELLED", "CANCELLED+", "REVOKED"}


def _redis() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url)


def _squeue_state(slurm_job_id: str) -> str | None:
    settings = get_settings()
    try:
        result = subprocess.run(  # noqa: S603
            [
                settings.slurm_squeue_bin,
                "--job",
                slurm_job_id,
                "--noheader",
                "--format=%T",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.exception("squeue invocation failed for %s", slurm_job_id)
        return None
    if result.returncode != 0:
        # squeue returns non-zero when job is unknown (already left the queue).
        return None
    raw = (result.stdout or "").strip().splitlines()
    if not raw:
        return None
    return raw[0].strip() or None


def _sacct_state(slurm_job_id: str) -> str | None:
    """Last-resort: read accounting record. Returns canonical state token."""
    settings = get_settings()
    try:
        result = subprocess.run(  # noqa: S603
            [
                settings.slurm_sacct_bin,
                "-j",
                slurm_job_id,
                "--format=State",
                "--parsable2",
                "--noheader",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.exception("sacct invocation failed for %s", slurm_job_id)
        return None
    if result.returncode != 0:
        return None
    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if not lines:
        return None
    # Top-level record is the first line; trim trailing '+' (truncation marker).
    state = lines[0].split("|", 1)[0].strip()
    return state.rstrip("+") or None


def _classify(slurm_state: str | None) -> JobStatus | None:
    if slurm_state is None:
        return None
    token = slurm_state.upper().strip()
    if token in _RUNNING_STATES:
        return JobStatus.RUNNING
    if token in _QUEUED_STATES:
        return JobStatus.QUEUED
    if token in _SUCCESS_STATES:
        return JobStatus.SUCCESS
    if token in _FAILED_STATES:
        return JobStatus.FAILED
    if token in _CANCELED_STATES or token.startswith("CANCELLED"):
        return JobStatus.CANCELED
    return None


def _finalize_job(job_id: str, target_status: JobStatus) -> None:
    """Write terminal status + collect results (if success) for a single job."""
    runner = SlurmRunner()
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        if job.status in (JobStatus.SUCCESS, JobStatus.FAILED, JobStatus.CANCELED):
            # Already finalised by another caller (e.g. UI cancel).
            return

        summary: dict[str, Any] = {}
        error_message: str | None = None

        if target_status == JobStatus.SUCCESS:
            try:
                result = runner.collect_results(job)
                summary = result.summary
                if result.status != "success":
                    target_status = JobStatus.FAILED
                    error_message = (
                        "; ".join(result.errors) if result.errors else "job failed"
                    )
            except Exception as exc:
                logger.exception("collect_results failed for job=%s", job_id)
                target_status = JobStatus.FAILED
                error_message = f"collect_results error: {exc}"
        elif target_status == JobStatus.FAILED:
            error_message = "Slurm reported job failure"
        elif target_status == JobStatus.CANCELED:
            error_message = "Slurm reported job cancelled"

        job.status = target_status
        job.finished_at = datetime.now(timezone.utc)
        if job.started_at and job.finished_at:
            job.duration_sec = int((job.finished_at - job.started_at).total_seconds())
        if summary:
            job.result_summary = summary
        if error_message:
            job.error_message = error_message[:2048]
        db.commit()


@celery_app.task(name="slurm_tasks.poll_slurm_jobs")
def poll_slurm_jobs() -> dict[str, Any]:
    """Refresh status for every active Slurm-backed HEAXHub job."""
    client = _redis()
    try:
        active = client.smembers("slurm:active_jobs") or set()
    except Exception:
        logger.exception("could not read slurm:active_jobs from Redis")
        return {"ok": False, "checked": 0}

    checked = 0
    transitions: list[dict[str, str]] = []

    for raw in active:
        job_id = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        slurm_job_id = _get_slurm_id(client, job_id)
        if not slurm_job_id:
            client.srem("slurm:active_jobs", job_id)
            continue
        checked += 1

        state = _squeue_state(slurm_job_id) or _sacct_state(slurm_job_id)
        classified = _classify(state)

        if classified is None:
            continue

        if classified == JobStatus.QUEUED or classified == JobStatus.RUNNING:
            # Sync DB state, but keep the slurm id active.
            with SessionLocal() as db:
                job = db.get(Job, job_id)
                if job is None:
                    _forget_slurm_id(client, job_id)
                    continue
                if job.status != classified and job.status in (
                    JobStatus.QUEUED,
                    JobStatus.RUNNING,
                ):
                    if classified == JobStatus.RUNNING and job.started_at is None:
                        job.started_at = datetime.now(timezone.utc)
                    job.status = classified
                    db.commit()
                    transitions.append(
                        {"job_id": job_id, "to": classified.value, "slurm_state": state or ""}
                    )
            continue

        # Terminal state — finalise + forget.
        _finalize_job(job_id, classified)
        _forget_slurm_id(client, job_id)
        transitions.append(
            {"job_id": job_id, "to": classified.value, "slurm_state": state or ""}
        )

    return {"ok": True, "checked": checked, "transitions": transitions}


__all__ = ["poll_slurm_jobs"]
