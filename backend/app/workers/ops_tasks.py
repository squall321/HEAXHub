"""Operations / housekeeping Celery tasks (SA-C).

These run on the Celery beat schedule (see ``celery_app.py``). They are
deliberately defensive — none of them should ever crash the worker, and all
update the audit_log so operators can verify what happened.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.core.logger import get_logger
from app.db.models.app import App, AppStatus
from app.db.models.service_instance import ServiceInstance
from app.db.session import SessionLocal
from app.services import service_manager
from app.services.audit_service import log as audit_log
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

# Repo root → ``scripts/rotate_job_storage.sh`` lives here. We resolve from this
# file (backend/app/workers/) up to the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ROTATE_SCRIPT = _REPO_ROOT / "scripts" / "rotate_job_storage.sh"


# ---------------------------------------------------------------------------
# A. Rotate old jobs — wraps scripts/rotate_job_storage.sh so the operator can
# run the same code path manually from a shell.
# ---------------------------------------------------------------------------


@celery_app.task(name="ops_tasks.rotate_old_jobs")
def rotate_old_jobs(days_keep: int = 90) -> dict[str, object]:
    """Archive job_storage entries older than ``days_keep`` days.

    Delegates the heavy lifting to ``scripts/rotate_job_storage.sh`` so the
    same code path is reachable from the operator shell:

        scripts/rotate_job_storage.sh 90
    """
    settings = get_settings()
    storage_root = Path(settings.job_storage_root).resolve()
    script = _ROTATE_SCRIPT

    if not script.exists():
        return {"ok": False, "error": f"rotate script missing: {script}"}
    if not storage_root.exists():
        return {"ok": False, "error": f"job_storage_root missing: {storage_root}"}

    env = os.environ.copy()
    env["JOB_STORAGE_ROOT"] = str(storage_root)
    try:
        result = subprocess.run(  # noqa: S603
            ["bash", str(script), str(int(days_keep))],
            env=env,
            capture_output=True,
            text=True,
            timeout=60 * 60,
            check=False,
        )
    except Exception as exc:
        logger.exception("rotate_job_storage.sh failed to launch")
        return {"ok": False, "error": str(exc)}

    archived = _count_archived(result.stderr)
    payload: dict[str, object] = {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "archived": archived,
        "stdout_tail": result.stdout[-2000:] if result.stdout else "",
        "stderr_tail": result.stderr[-2000:] if result.stderr else "",
        "days_keep": int(days_keep),
        "storage_root": str(storage_root),
    }
    with SessionLocal() as db:
        audit_log(
            db,
            actor_user_id=None,
            action="ops.rotate_jobs",
            target_type="system",
            target_id="job_storage",
            meta={
                "archived": archived,
                "returncode": result.returncode,
                "days_keep": int(days_keep),
            },
        )
    return payload


def _count_archived(stderr: str) -> int:
    """Parse the ``done: archived=N failed=M`` summary line from the script."""
    if not stderr:
        return 0
    for line in reversed(stderr.splitlines()):
        if "done:" in line and "archived=" in line:
            for tok in line.split():
                if tok.startswith("archived="):
                    try:
                        return int(tok.split("=", 1)[1])
                    except ValueError:
                        return 0
    return 0


# ---------------------------------------------------------------------------
# B. Recover service instances — bookkeeping after a worker / box reboot.
# ---------------------------------------------------------------------------


_RECOVERABLE_STATUSES = ("starting", "healthy", "unhealthy")


@celery_app.task(name="ops_tasks.recover_service_instances")
def recover_service_instances() -> dict[str, object]:
    """Reconcile ServiceInstance rows against actual OS state.

    For every row whose status suggests the daemon should be running, verify
    that the recorded PID is alive AND (if a port is recorded) that the port
    is still listening. Anything that fails the check is marked ``stopped``
    and a restart is attempted via :func:`service_manager.restart_service`.
    """
    inspected = 0
    marked_stopped = 0
    restart_attempted = 0
    restart_failed = 0

    with SessionLocal() as db:
        rows = list(
            db.execute(
                select(ServiceInstance).where(ServiceInstance.status.in_(_RECOVERABLE_STATUSES))
            ).scalars()
        )
        ids = [r.id for r in rows]

    for instance_id in ids:
        with SessionLocal() as db:
            inst = db.get(ServiceInstance, instance_id)
            if inst is None:
                continue
            inspected += 1
            alive = _process_alive(inst.pid) and (
                inst.port is None or _port_listening(inst.port)
            )
            if alive:
                continue

            inst.status = "stopped"
            inst.stopped_at = datetime.now(timezone.utc)
            db.add(inst)
            db.commit()
            db.refresh(inst)
            marked_stopped += 1

            audit_log(
                db,
                actor_user_id=None,
                action="ops.service_recovered",
                target_type="service_instance",
                target_id=str(inst.id),
                meta={
                    "app_id": inst.app_id,
                    "pid": inst.pid,
                    "port": inst.port,
                    "action": "mark_stopped",
                },
            )

            # Attempt a restart so the service comes back online.
            try:
                service_manager.restart_service(db, instance_id=inst.id)
                restart_attempted += 1
            except Exception as exc:
                restart_failed += 1
                logger.exception("recover restart failed for instance=%s", inst.id)
                audit_log(
                    db,
                    actor_user_id=None,
                    action="ops.service_restart_failed",
                    target_type="service_instance",
                    target_id=str(inst.id),
                    meta={"error": str(exc)},
                )

    return {
        "inspected": inspected,
        "marked_stopped": marked_stopped,
        "restart_attempted": restart_attempted,
        "restart_failed": restart_failed,
    }


def _process_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _port_listening(port: int) -> bool:
    """Return True if *any* process is bound to ``port`` on loopback."""
    if not port:
        return False
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(0.5)
        # connect_ex returns 0 if something is listening
        result = s.connect_ex(("127.0.0.1", int(port)))
        return result == 0
    except OSError:
        return False
    finally:
        s.close()


# ---------------------------------------------------------------------------
# C. Cleanup stale workspaces — drop archived app workspaces older than 30d.
# ---------------------------------------------------------------------------


_INTEGRATION_BUCKET = "_integrations"


@celery_app.task(name="ops_tasks.cleanup_stale_workspaces")
def cleanup_stale_workspaces(max_age_days: int = 30) -> dict[str, object]:
    """Delete ``app_workspaces/{id}/`` for archived apps older than N days.

    The ``_integrations`` bucket is always preserved.
    """
    settings = get_settings()
    workspace_root = Path(settings.workspace_root).resolve()
    if not workspace_root.exists():
        return {"ok": False, "error": f"workspace_root missing: {workspace_root}"}

    cutoff = datetime.now(timezone.utc) - timedelta(days=int(max_age_days))
    removed: list[str] = []
    skipped: list[str] = []

    with SessionLocal() as db:
        archived_apps = list(
            db.execute(select(App).where(App.status == AppStatus.ARCHIVED)).scalars()
        )

    for app in archived_apps:
        if app.id == _INTEGRATION_BUCKET:
            skipped.append(app.id)
            continue
        ws = workspace_root / app.id
        if not ws.exists():
            continue
        # Guard: never touch anything outside workspace_root.
        try:
            ws_resolved = ws.resolve()
            ws_resolved.relative_to(workspace_root)
        except ValueError:
            skipped.append(app.id)
            continue
        if app.updated_at and app.updated_at > cutoff:
            continue
        try:
            shutil.rmtree(ws_resolved)
            removed.append(app.id)
        except Exception:
            logger.exception("failed to remove stale workspace %s", ws_resolved)
            skipped.append(app.id)

    if removed:
        with SessionLocal() as db:
            audit_log(
                db,
                actor_user_id=None,
                action="ops.cleanup_workspaces",
                target_type="system",
                target_id="app_workspaces",
                meta={"removed": removed, "skipped": skipped, "max_age_days": int(max_age_days)},
            )
    return {"removed": removed, "skipped": skipped}
