"""Admin endpoints."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.config import get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.models.app import App, AppStatus
from app.db.models.audit_log import AuditLog
from app.db.models.gpu_device import GpuDevice
from app.db.models.gpu_holding import GpuHolding
from app.db.models.job import Job, JobStatus
from app.db.models.license_holding import LicenseHolding
from app.db.models.license_pool import LicensePool
from app.db.models.service_instance import ServiceInstance
from app.db.models.submission import Submission, SubmissionStatus
from app.db.models.user import User, UserRole, UserStatus
from app.deps import AdminUser, DbSession
from app.schemas.auth import UserPublic
from app.schemas.common import Paginated
from app.services import (
    audit_service,
    github_integration,
    gpu_manager,
    integration_workspaces,
    license_manager,
    secret_manager,
    service_manager,
)
from app.services import change_request as change_request_service
from app.services.audit_service import log as audit_log
from app.services.license_providers import UNKNOWN_AVAILABLE, get_provider
from app.workers.sync_tasks import refresh_upstream

router = APIRouter(prefix="/admin", tags=["admin"])


class RolePatch(BaseModel):
    role: UserRole


@router.get("/users", response_model=Paginated[UserPublic])
def list_users(
    db: DbSession,
    _admin: AdminUser,
    q: str | None = None,
    role: UserRole | None = None,
    page: int = 1,
    page_size: int = 50,
) -> Paginated[UserPublic]:
    stmt = select(User).order_by(User.created_at.desc())
    if q:
        like = f"%{q.lower()}%"
        from sqlalchemy import func as sa_func, or_
        stmt = stmt.where(
            or_(
                sa_func.lower(User.email).like(like),
                sa_func.lower(User.display_name).like(like),
                sa_func.lower(User.organization).like(like),
            )
        )
    if role:
        stmt = stmt.where(User.role == role)
    rows = list(db.execute(stmt).scalars())
    total = len(rows)
    page = max(1, page)
    page_size = max(1, min(page_size, 500))
    offset = (page - 1) * page_size
    items = [UserPublic.model_validate(u) for u in rows[offset : offset + page_size]]
    return Paginated(items=items, total=total, page=page, page_size=page_size)


@router.patch("/users/{user_id}/role", response_model=UserPublic)
def patch_user_role(
    user_id: uuid.UUID, payload: RolePatch, db: DbSession, _admin: AdminUser
) -> UserPublic:
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found")
    previous_role = user.role
    user.role = payload.role
    db.commit()
    db.refresh(user)

    audit_service.safe_log(
        db,
        actor_user_id=_admin.id,
        action="user.role_change",
        target_type="user",
        target_id=str(user.id),
        meta={
            "previous_role": getattr(previous_role, "value", str(previous_role)),
            "new_role": getattr(user.role, "value", str(user.role)),
        },
    )
    return UserPublic.model_validate(user)


@router.get("/updates")
def list_updates(db: DbSession, _admin: AdminUser) -> list[dict[str, Any]]:
    """List upstream-update audit entries that the operator should review."""
    rows = (
        db.execute(
            select(AuditLog)
            .where(AuditLog.action == "upstream.update_available")
            .order_by(AuditLog.created_at.desc())
            .limit(200)
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "app_id": r.target_id,
            "meta": r.meta,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/updates/{audit_id}/approve")
def approve_update(audit_id: int, db: DbSession, _admin: AdminUser) -> dict[str, Any]:
    """Trigger an upstream sync + rebuild for the app referenced by this audit entry."""
    row = db.get(AuditLog, audit_id)
    if row is None:
        raise NotFoundError("Update entry not found")
    if row.target_type != "app" or not row.target_id:
        raise ValidationError("Audit entry does not reference an app")

    app = db.get(App, row.target_id)
    if app is None:
        raise NotFoundError("App not found")

    audit_log(
        db,
        actor_user_id=_admin.id,
        action="upstream.update_approved",
        target_type="app",
        target_id=app.id,
        meta={"audit_source_id": row.id, "tag": (row.meta or {}).get("latest_tag")},
    )
    # Enqueue async refresh — pulls latest commit/tag and triggers rebuild.
    refresh_upstream.delay(app.id)
    return {"detail": "Rebuild enqueued.", "app_id": app.id}


@router.post("/updates/{audit_id}/ignore")
def ignore_update(audit_id: int, db: DbSession, _admin: AdminUser) -> dict[str, str]:
    row = db.get(AuditLog, audit_id)
    if row is None:
        raise NotFoundError("Update entry not found")

    audit_log(
        db,
        actor_user_id=_admin.id,
        action="upstream.update_ignored",
        target_type=row.target_type,
        target_id=row.target_id,
        meta=row.meta,
    )
    return {"detail": "Ignored."}


@router.get("/stats")
def system_stats(db: DbSession, _admin: AdminUser) -> dict[str, Any]:
    """Aggregate counters for the admin dashboard."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func as sa_func

    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)

    return {
        "jobs_today": int(
            db.execute(
                select(sa_func.count()).select_from(Job).where(Job.created_at >= since_24h)
            ).scalar_one()
        ),
        "active_users_today": int(
            db.execute(
                select(sa_func.count(sa_func.distinct(Job.executor_user_id)))
                .select_from(Job)
                .where(Job.created_at >= since_24h)
            ).scalar_one()
        ),
        "build_queue_depth": int(
            db.execute(
                select(sa_func.count())
                .select_from(Job)
                .where(Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
            ).scalar_one()
        ),
        "pending_submissions": int(
            db.execute(
                select(sa_func.count())
                .select_from(Submission)
                .where(
                    Submission.status.in_(
                        [SubmissionStatus.PENDING, SubmissionStatus.UNDER_REVIEW]
                    )
                )
            ).scalar_one()
        ),
        # 추가 정보 (프론트 미사용이지만 useful)
        "users_total": int(db.execute(select(sa_func.count()).select_from(User)).scalar_one()),
        "apps_stable": int(
            db.execute(
                select(sa_func.count()).select_from(App).where(App.status == AppStatus.STABLE)
            ).scalar_one()
        ),
    }


@router.get("/audit")
def list_audit(
    db: DbSession,
    _admin: AdminUser,
    actor: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    if actor:
        try:
            stmt = stmt.where(AuditLog.actor_user_id == uuid.UUID(actor))
        except ValueError:
            pass
    rows = list(db.execute(stmt).scalars())
    total = len(rows)
    page = max(1, page)
    page_size = max(1, min(page_size, 1000))
    offset = (page - 1) * page_size
    items = [
        {
            "id": r.id,
            "actor_user_id": str(r.actor_user_id) if r.actor_user_id else None,
            "action": r.action,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "meta": r.meta,
            "ip_address": r.ip_address,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows[offset : offset + page_size]
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/system/health")
def system_health(db: DbSession, _admin: AdminUser) -> dict[str, Any]:
    """System health snapshot matching frontend expectations."""
    from sqlalchemy import func as sa_func

    # DB
    db_ok = True
    try:
        db.execute(select(sa_func.count()).select_from(User)).scalar_one()
    except Exception:
        db_ok = False

    # Redis + celery beat heartbeat
    redis_ok = True
    queue_depth = 0
    beat_running = False
    try:
        import redis as _redis
        r = _redis.Redis.from_url(get_settings().redis_url)
        r.ping()
        # Default celery queue
        queue_depth = int(r.llen("celery") or 0)
        # Celery beat writes its last-tick state under a few known keys. We
        # accept any of them as a heartbeat.
        for key in (
            "celery-beat:last_run",
            "celerybeat:schedule",
            "_kombu.binding.celery",
        ):
            try:
                if r.exists(key):
                    beat_running = True
                    break
            except Exception:
                continue
    except Exception:
        redis_ok = False

    active_jobs = int(
        db.execute(
            select(sa_func.count())
            .select_from(Job)
            .where(Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
        ).scalar_one()
    )

    # Last rotation timestamp from audit_log
    last_rotation_at: str | None = None
    try:
        row = db.execute(
            select(AuditLog)
            .where(AuditLog.action == "ops.rotate_jobs")
            .order_by(AuditLog.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row and row.created_at:
            last_rotation_at = row.created_at.isoformat()
    except Exception:
        pass

    # Service-instance counts
    service_instances_healthy = 0
    service_instances_unhealthy = 0
    try:
        service_instances_healthy = int(
            db.execute(
                select(sa_func.count())
                .select_from(ServiceInstance)
                .where(ServiceInstance.status == "healthy")
            ).scalar_one()
        )
        service_instances_unhealthy = int(
            db.execute(
                select(sa_func.count())
                .select_from(ServiceInstance)
                .where(ServiceInstance.status.in_(["unhealthy", "starting"]))
            ).scalar_one()
        )
    except Exception:
        pass

    # secret_manager fail-safe — surface configuration status to ops.
    try:
        secrets_configured = secret_manager.is_configured()
    except Exception:  # noqa: BLE001
        secrets_configured = False

    return {
        "status": "ok" if (db_ok and redis_ok) else "degraded",
        "queue_depth": queue_depth,
        "active_jobs": active_jobs,
        "db_ok": db_ok,
        "redis_ok": redis_ok,
        "beat_running": beat_running,
        "last_rotation_at": last_rotation_at,
        "service_instances_healthy": service_instances_healthy,
        "service_instances_unhealthy": service_instances_unhealthy,
        "secrets_configured": secrets_configured,
    }


# ---------------------------------------------------------------------------
# GitHub integration admin endpoints (SA3)
# ---------------------------------------------------------------------------


@router.get("/integrations")
def get_integration_status(_admin: AdminUser) -> dict[str, Any]:
    """Inspect the runtime GitHub integration config. Never leaks the bot token."""
    settings = get_settings()
    token_configured = bool(settings.github_bot_token)
    github_user: dict[str, Any] | None = None
    error: str | None = None
    if token_configured:
        try:
            github_user = github_integration.verify_github_token(settings.github_bot_token)
        except Exception as exc:
            error = str(exc)
    # Local workspace status for each configured integration repo.
    workspaces = [
        {
            "repo_url": w.repo_url,
            "slug": w.slug,
            "path": str(w.path),
            "upstream": str(w.upstream),
            "cloned": w.cloned,
            "commit_sha": w.commit_sha,
            "last_sync_at": w.last_sync_at.isoformat() if w.last_sync_at else None,
            "error": w.error,
        }
        for w in integration_workspaces.list_all()
    ]
    return {
        "integration_repo_url": settings.integration_repo_url,
        "integration_repo_urls": settings.integration_repo_url_list,
        "integration_workspaces": workspaces,
        "github_bot_username": settings.github_bot_username,
        "github_token_configured": token_configured,
        "github_user": github_user,
        "error": error,
    }


@router.get("/integrations/builtin")
def list_builtin_integrations(_admin: AdminUser) -> dict[str, Any]:
    """Status table of in-tree integrations/ — what each demo's live state is.

    For each integrations/<slug>/:
      - slug (directory name)
      - id (manifest.id, the canonical App.id)
      - name + version + stack + launch_mode
      - has_venv: bool   — integration_builder sentinel present
      - state file:      — pid + port + base_path + started_at (if launched)
      - process_alive    — os.kill 0
      - http_health      — curl 200 via Caddy /apps/<id>/<health_path>
    """
    from pathlib import Path  # noqa: PLC0415

    import yaml as _yaml  # noqa: PLC0415

    from app.services import integration_launcher  # noqa: PLC0415

    root = Path(__file__).resolve().parents[3] / "integrations"
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return {"root": str(root), "exists": False, "items": []}

    for child in sorted(p for p in root.iterdir() if p.is_dir()):
        slug = child.name
        manifest_path = child / ".portal" / "manifest.yaml"
        manifest: dict[str, Any] = {}
        if manifest_path.exists():
            try:
                manifest = _yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            except Exception as exc:  # noqa: BLE001
                manifest = {"__error__": str(exc)}

        canonical = str(manifest.get("id") or slug.replace("-", "_"))
        state = integration_launcher._read_state(canonical)  # noqa: SLF001
        alive = integration_launcher._is_alive(state.get("pid")) if state else False  # noqa: SLF001
        healthy = False
        if state and state.get("port"):
            healthy = integration_launcher._is_healthy(  # noqa: SLF001
                int(state["port"]),
                state.get("health_path") or "/",
                root=state.get("base_path") or f"/apps/{canonical}",
            )

        rows.append({
            "slug": slug,
            "id": canonical,
            "name": manifest.get("name"),
            "version": manifest.get("version"),
            "stack": (manifest.get("build") or {}).get("stack"),
            "launch_mode": (manifest.get("launch") or {}).get("mode"),
            "has_venv": (child / ".venv" / "bin").exists(),
            "build_sentinel": (child / ".heaxhub_build_ok").exists(),
            "state": state,
            "process_alive": alive,
            "http_healthy": healthy,
            "log_file": f"var/logs/integration_{canonical}.log",
        })
    return {"root": str(root), "exists": True, "count": len(rows), "items": rows}


@router.post("/integrations/sync")
def sync_integration_repos(
    _admin: AdminUser,
    repo_url: str | None = None,
) -> dict[str, Any]:
    """Re-pull integration repos. Pass ``?repo_url=...`` to sync one, or
    omit to sync all."""
    settings = get_settings()
    configured = settings.integration_repo_url_list
    if repo_url is not None and repo_url not in configured:
        raise ValidationError(
            f"repo_url '{repo_url}' is not in INTEGRATION_REPO_URLS"
        )
    results = [integration_workspaces.sync_one(repo_url)] if repo_url else \
        integration_workspaces.sync_all()
    return {
        "synced": [
            {
                "repo_url": w.repo_url,
                "upstream": str(w.upstream),
                "commit_sha": w.commit_sha,
                "error": w.error,
            }
            for w in results
        ],
    }


@router.post("/integrations/proxy-sync")
def proxy_sync_integrations(db: DbSession, admin: AdminUser) -> dict[str, Any]:
    """Manually trigger a reconcile pass: re-register lost Caddy routes and
    restart dead service instances for every live integration.

    Idempotent + build-free — same code path the beat task runs on a 45s
    interval. Operators hit this after a Caddy restart to immediately re-inject
    the ``/apps/<id>`` routes instead of waiting for the next tick.
    """
    from app.workers.integration_tasks import reconcile_integrations  # noqa: PLC0415

    summary = reconcile_integrations()
    audit_service.safe_log(
        db,
        actor_user_id=admin.id,
        action="integrations.proxy_sync",
        target_type="system",
        target_id="integrations",
        meta={"by_action": summary.get("by_action")},
    )
    return summary


@router.post("/integrations/test-request")
def create_integration_test_request(
    db: DbSession,
    admin: AdminUser,
    repo_url: str | None = None,
) -> dict[str, Any]:
    """One-click demo: create a Submission for the chosen integration repo and
    immediately produce a draft ChangeRequest. Returns both ids.

    Pass ``?repo_url=...`` to pick one of the configured integration repos
    (must match an entry in `integration_repo_urls`). If omitted, uses the
    first configured repo.
    """
    settings = get_settings()
    configured = settings.integration_repo_url_list
    if not configured:
        raise ValidationError(
            "No integration repo configured (INTEGRATION_REPO_URL or INTEGRATION_REPO_URLS)"
        )
    if repo_url is None:
        repo_url = configured[0]
    elif repo_url not in configured:
        raise ValidationError(
            f"repo_url '{repo_url}' is not in INTEGRATION_REPO_URLS"
        )

    # Generate a per-call submission so repeated demo runs don't collide.
    proposed_app_id = f"integration_demo_{uuid.uuid4().hex[:8]}"
    sub = Submission(
        submitter_user_id=admin.id,
        proposed_app_id=proposed_app_id,
        name="HEAXHub Integration Demo",
        description="Auto-generated by /admin/integrations/test-request",
        upstream_repo_url=repo_url,
        status=SubmissionStatus.UNDER_REVIEW,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)

    cr = change_request_service.create_draft(
        db,
        submission_id=sub.id,
        repo_url=repo_url,
        actor=admin,
        app_id=proposed_app_id,
    )
    return {
        "submission_id": str(sub.id),
        "change_request_id": str(cr.id),
        "status": cr.status,
    }


# ---------------------------------------------------------------------------
# License pool admin endpoints (SA4)
# ---------------------------------------------------------------------------


class LicensePoolCreate(BaseModel):
    name: str
    total_tokens: int
    feature: str | None = None
    server: str | None = None
    check_command: str | None = None
    description: str | None = None


@router.get("/licenses")
def list_license_pools(db: DbSession, _admin: AdminUser) -> dict[str, Any]:
    pools = list(db.execute(select(LicensePool).order_by(LicensePool.name)).scalars())
    provider = get_provider(db)
    pool_items: list[dict[str, Any]] = []
    feature_seen: set[str] = set()
    provider_availability: dict[str, Any] = {}
    for p in pools:
        status = license_manager.pool_status(db, p.name)
        feature = p.feature or p.name
        if feature not in feature_seen:
            avail = provider.check_available(feature)
            provider_availability[feature] = (
                None if avail == UNKNOWN_AVAILABLE else avail
            )
            feature_seen.add(feature)
        pool_items.append(
            {
                "id": str(p.id),
                "name": p.name,
                "total_tokens": p.total_tokens,
                "feature": p.feature,
                "server": p.server,
                "description": p.description,
                "in_use": status["in_use"],
                "free": status["free"],
                "provider_available": provider_availability.get(feature),
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
        )
    return {
        "provider": provider.health(),
        "pools": pool_items,
        "provider_availability": provider_availability,
    }


@router.post("/licenses", status_code=201)
def create_license_pool(
    payload: LicensePoolCreate, db: DbSession, _admin: AdminUser
) -> dict[str, Any]:
    existing = db.execute(
        select(LicensePool).where(LicensePool.name == payload.name)
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("License pool name already exists")
    if payload.total_tokens <= 0:
        raise ValidationError("total_tokens must be positive")
    pool = LicensePool(
        name=payload.name,
        total_tokens=payload.total_tokens,
        feature=payload.feature,
        server=payload.server,
        check_command=payload.check_command,
        description=payload.description,
    )
    db.add(pool)
    db.commit()
    db.refresh(pool)
    audit_service.safe_log(
        db,
        actor_user_id=_admin.id,
        action="license_pool.create",
        target_type="license_pool",
        target_id=str(pool.id),
        meta={"name": pool.name, "total_tokens": pool.total_tokens, "feature": pool.feature},
    )
    return {"id": str(pool.id), "name": pool.name, "total_tokens": pool.total_tokens}


@router.delete("/licenses/{name}", status_code=204)
def delete_license_pool(name: str, db: DbSession, _admin: AdminUser) -> None:
    pool = db.execute(
        select(LicensePool).where(LicensePool.name == name)
    ).scalar_one_or_none()
    if pool is None:
        raise NotFoundError("License pool not found")
    active = db.execute(
        select(LicenseHolding)
        .where(LicenseHolding.pool_id == pool.id)
        .where(LicenseHolding.released_at.is_(None))
    ).first()
    if active is not None:
        raise ConflictError("Cannot delete a pool with active holdings")
    pool_id, pool_name = str(pool.id), pool.name
    db.delete(pool)
    db.commit()
    audit_service.safe_log(
        db,
        actor_user_id=_admin.id,
        action="license_pool.delete",
        target_type="license_pool",
        target_id=pool_id,
        meta={"name": pool_name},
    )


@router.get("/licenses/{name}/usage")
def license_usage(name: str, db: DbSession, _admin: AdminUser) -> dict[str, Any]:
    pool = db.execute(
        select(LicensePool).where(LicensePool.name == name)
    ).scalar_one_or_none()
    if pool is None:
        raise NotFoundError("License pool not found")
    status = license_manager.pool_status(db, pool.name)
    holdings = list(
        db.execute(
            select(LicenseHolding)
            .where(LicenseHolding.pool_id == pool.id)
            .order_by(LicenseHolding.acquired_at.desc())
            .limit(200)
        ).scalars()
    )
    return {
        "name": pool.name,
        "total": status["total"],
        "in_use": status["in_use"],
        "free": status["free"],
        "holdings": [
            {
                "id": str(h.id),
                "job_id": h.job_id,
                "tokens": h.tokens,
                "acquired_at": h.acquired_at.isoformat() if h.acquired_at else None,
                "released_at": h.released_at.isoformat() if h.released_at else None,
                "active": h.released_at is None,
            }
            for h in holdings
        ],
    }


# ---------------------------------------------------------------------------
# GPU admin endpoints (SA4)
# ---------------------------------------------------------------------------
#
# The frontend GpuGrid expects each device shaped as:
#   { id, index, uuid?, model, memory_mb, cuda_version?, status:
#     "available"|"in_use"|"offline", host?, current_job_id?, updated_at }
# Backend storage uses ``device_index`` / ``cuda_capability`` / status
# ``free|busy``. We translate at the API boundary so the frontend doesn't
# need to know about the storage names.


_GPU_STATUS_MAP = {"free": "available", "busy": "in_use", "offline": "offline"}


def _serialize_gpu_device(
    device: Any, active_holdings_by_device: dict[int, Any]
) -> dict[str, Any]:
    holding = active_holdings_by_device.get(device.id)
    return {
        "id": str(device.id),
        "index": device.device_index,
        "uuid": device.uuid,
        "model": device.model or "Unknown",
        "memory_mb": device.memory_mb or 0,
        "cuda_version": device.cuda_capability,
        "status": _GPU_STATUS_MAP.get(device.status, device.status),
        "host": device.host,
        "current_job_id": holding.job_id if holding else None,
        # GpuDevice has no updated_at column — surface the active holding's
        # acquired_at when busy, otherwise an empty string (frontend tolerates).
        "updated_at": (
            holding.acquired_at.isoformat()
            if holding and holding.acquired_at
            else ""
        ),
    }


@router.get("/gpus")
def list_gpus(db: DbSession, _admin: AdminUser) -> list[dict[str, Any]]:
    """Return the GPU inventory in the shape the frontend GpuGrid expects."""
    devices = list(
        db.execute(
            select(GpuDevice).order_by(GpuDevice.host, GpuDevice.device_index)
        ).scalars()
    )
    active = list(
        db.execute(
            select(GpuHolding).where(GpuHolding.released_at.is_(None))
        ).scalars()
    )
    by_device: dict[int, Any] = {h.device_id: h for h in active}
    return [_serialize_gpu_device(d, by_device) for d in devices]


@router.get("/gpus/holdings")
def list_gpu_holdings(db: DbSession, _admin: AdminUser) -> list[dict[str, Any]]:
    """Recent GPU holdings (active + released) for admin auditing."""
    rows = list(
        db.execute(
            select(GpuHolding).order_by(GpuHolding.acquired_at.desc()).limit(500)
        ).scalars()
    )
    return [
        {
            "id": str(h.id),
            "gpu_id": str(h.device_id),
            "job_id": h.job_id,
            "acquired_at": h.acquired_at.isoformat() if h.acquired_at else None,
            "released_at": h.released_at.isoformat() if h.released_at else None,
        }
        for h in rows
    ]


@router.post("/gpus/refresh")
def refresh_gpus(db: DbSession, _admin: AdminUser) -> dict[str, Any]:
    """Re-run ``nvidia-smi`` and upsert the inventory. Returns ``{ok, devices}``."""
    gpu_manager.register_gpus(db)
    devices = list(
        db.execute(
            select(GpuDevice).order_by(GpuDevice.host, GpuDevice.device_index)
        ).scalars()
    )
    active = list(
        db.execute(
            select(GpuHolding).where(GpuHolding.released_at.is_(None))
        ).scalars()
    )
    by_device: dict[int, Any] = {h.device_id: h for h in active}
    return {
        "ok": True,
        "devices": [_serialize_gpu_device(d, by_device) for d in devices],
    }


# ---------------------------------------------------------------------------
# Service instance admin endpoints (SA4)
# ---------------------------------------------------------------------------


@router.get("/services")
def list_services(db: DbSession, _admin: AdminUser) -> list[dict[str, Any]]:
    rows = service_manager.list_instances(db)
    return [
        {
            "id": str(r.id),
            "app_id": r.app_id,
            "version_id": str(r.version_id) if r.version_id else None,
            "pid": r.pid,
            "port": r.port,
            "status": r.status,
            "workdir": r.workdir,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "last_health": r.last_health.isoformat() if r.last_health else None,
            "restart_count": r.restart_count,
            "stopped_at": r.stopped_at.isoformat() if r.stopped_at else None,
        }
        for r in rows
    ]


@router.post("/services/{instance_id}/restart")
def restart_service_endpoint(
    instance_id: uuid.UUID, db: DbSession, _admin: AdminUser
) -> dict[str, Any]:
    inst = service_manager.restart_service(db, instance_id=instance_id)
    return {"id": str(inst.id), "status": inst.status, "pid": inst.pid, "port": inst.port}


@router.post("/services/{instance_id}/stop")
def stop_service_endpoint(
    instance_id: uuid.UUID, db: DbSession, _admin: AdminUser
) -> dict[str, str]:
    service_manager.stop_service(db, instance_id=instance_id)
    return {"detail": "stopped"}


# ---------------------------------------------------------------------------
# 누락 패키지 환류 (빌드 중 감지된 pip 누락 패키지 조회)
# ---------------------------------------------------------------------------


@router.get("/pkg-requests")
def list_pkg_requests(db: DbSession, _admin: AdminUser) -> dict[str, Any]:
    """빌드 중 감지된 pip 누락 패키지(JSONL 로그)를 최신순으로 반환한다."""
    import json
    from pathlib import Path

    # var/ 는 repo 루트 기준. admin.py 는 backend/app/api/v1 이므로 parents[4].
    req_file = Path(__file__).resolve().parents[4] / "var" / "pkg-requests.jsonl"
    items: list[dict[str, Any]] = []

    if req_file.exists():
        try:
            with req_file.open("r", encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        try:
                            items.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except OSError:
            pass

    # 최신 먼저(ts 기준 내림차순) 정렬.
    items.sort(key=lambda x: x.get("ts", ""), reverse=True)

    # 재실패로 같은 패키지가 여러 번 쌓이므로 패키지별 최신 1건만 남긴다.
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for it in items:
        pkg = it.get("package")
        if pkg in seen:
            continue
        seen.add(pkg)
        deduped.append(it)

    return {
        "total": len(deduped),
        "items": deduped[:200],  # 최근 200개
        "log_file": str(req_file),
    }
