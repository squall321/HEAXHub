"""Windows Worker Agent endpoints.

Two audiences share this router:

1. Agents themselves (token-auth, prefix ``/agents``) — heartbeat / poll / log / file / status.
2. Operators (admin-auth, prefix ``/admin/agents``) — register / list / disable.
"""
from __future__ import annotations

import json
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    Header,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.errors import ConflictError, NotFoundError, UnauthorizedError, ValidationError
from app.db.models.audit_log import AuditLog
from app.db.models.install_report import InstallReport
from app.db.models.job import Job, JobStatus
from app.db.models.windows_agent import WindowsAgent
from app.deps import AdminUser, DbSession
from app.runners import windows_agent_client
from app.services import agent_registry, agent_service, audit_service

router = APIRouter(tags=["agents"])


# ─────────────────────────── token auth dep ────────────────────────────────────


def get_agent_from_token(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> WindowsAgent:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("Missing agent token")
    token = authorization.split(" ", 1)[1].strip()
    agent = agent_registry.verify_token(db, token=token)
    if agent is None:
        raise UnauthorizedError("Invalid agent token")
    return agent


AgentAuth = Annotated[WindowsAgent, Depends(get_agent_from_token)]


# ─────────────────────────── schemas ───────────────────────────────────────────


class AgentHeartbeatIn(BaseModel):
    status: str = Field(..., description="online | busy | offline")
    agent_version: str | None = None


class AgentRegisterIn(BaseModel):
    name: str
    pool: str
    hostname: str | None = None
    capabilities: dict[str, Any] | None = None
    # 'launcher' = HWAXAgent Windows tray; 'service' = legacy polling worker.
    # NULL leaves the row unflagged (admin can backfill later).
    device_kind: str | None = Field(default=None, pattern=r"^(launcher|service)$")


class AgentOut(BaseModel):
    id: str
    name: str
    pool: str
    hostname: str | None
    agent_version: str | None
    status: str
    last_seen: str | None
    disabled: bool
    device_kind: str | None = None
    capabilities: dict[str, Any] | None
    created_at: str | None

    @classmethod
    def from_row(cls, row: WindowsAgent) -> "AgentOut":
        return cls(
            id=str(row.id),
            name=row.name,
            pool=row.pool,
            hostname=row.hostname,
            agent_version=row.agent_version,
            status=row.status,
            last_seen=row.last_seen.isoformat() if row.last_seen else None,
            disabled=row.disabled,
            device_kind=row.device_kind,
            capabilities=row.capabilities,
            created_at=row.created_at.isoformat() if row.created_at else None,
        )


class AgentRegisterOut(BaseModel):
    agent: AgentOut
    token: str = Field(..., description="One-time plaintext token; store it now.")


class InstallReportOut(BaseModel):
    """One launcher install attempt (omits the large log_excerpt for list views)."""

    id: str
    agent_id: str
    app_id: str
    version: str
    status: str
    exit_code: int | None
    started_at: str | None
    finished_at: str | None
    sha256_verified: bool | None
    error: str | None
    previous_version: str | None
    created_at: str | None

    @classmethod
    def from_row(cls, r: InstallReport) -> "InstallReportOut":
        return cls(
            id=str(r.id),
            agent_id=str(r.agent_id),
            app_id=r.app_id,
            version=r.version,
            status=r.status,
            exit_code=r.exit_code,
            started_at=r.started_at.isoformat() if r.started_at else None,
            finished_at=r.finished_at.isoformat() if r.finished_at else None,
            sha256_verified=r.sha256_verified,
            error=r.error,
            previous_version=r.previous_version,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )


class AgentAuditOut(BaseModel):
    """One audit_log row for a launcher agent (kind/severity live in meta)."""

    id: int
    action: str
    target_id: str
    created_at: str | None
    meta: dict[str, Any] | None

    @classmethod
    def from_row(cls, r: AuditLog) -> "AgentAuditOut":
        return cls(
            id=r.id,
            action=r.action,
            target_id=r.target_id,
            created_at=r.created_at.isoformat() if r.created_at else None,
            meta=r.meta,
        )


class AgentPollOut(BaseModel):
    job: dict[str, Any] | None


class JobStatusIn(BaseModel):
    status: str = Field(..., description="success | failed")
    exit_code: int | None = None
    message: str | None = None


# ─────────────────────────── agent-facing endpoints ────────────────────────────


@router.post("/agents/heartbeat", response_model=dict)
def agents_heartbeat(
    payload: AgentHeartbeatIn,
    db: DbSession,
    agent: AgentAuth,
) -> dict[str, str]:
    agent_registry.heartbeat(
        db,
        agent_id=agent.id,
        status=payload.status,
        agent_version=payload.agent_version,
    )
    return {"detail": "ok"}


@router.get("/agents/poll", response_model=AgentPollOut)
def agents_poll(
    db: DbSession,
    agent: AgentAuth,
    pool: str | None = None,
) -> AgentPollOut:
    """Return the next pending job for this agent, or ``{"job": null}``.

    The ``pool`` query parameter is informational only — the queue is keyed by
    the agent's own id, which is already bound to a single pool.
    """
    # Refresh heartbeat lazily on poll.
    agent_registry.heartbeat(db, agent_id=agent.id, status="online")
    job_payload = windows_agent_client.pop_next_job_for_agent(agent.id)
    if job_payload is None:
        # Also flush any pending cancel control messages so the agent can react.
        ctrl = windows_agent_client.pop_control_message(agent.id)
        if ctrl is not None:
            return AgentPollOut(job={"control": ctrl})
        return AgentPollOut(job=None)
    return AgentPollOut(job=job_payload)


@router.post("/agents/jobs/{job_id}/log")
def agents_post_log(
    job_id: str,
    db: DbSession,
    agent: AgentAuth,
    payload: dict[str, Any] = Body(...),
) -> dict[str, str]:
    """Accept a batch of log lines. Body: ``{"lines": ["a", "b", ...]}``."""
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError("Job not found")
    lines = payload.get("lines") or []
    if not isinstance(lines, list):
        raise ValidationError("`lines` must be a list of strings")

    # Append to the on-disk log file so /jobs/{id}/logs still works.
    log_path = Path(job.storage_path) / "logs" / "stdout.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fp:
        for line in lines:
            if not isinstance(line, str):
                line = str(line)
            fp.write(line + "\n")
            windows_agent_client.publish_agent_log(job_id, line)
    return {"detail": "ok"}


@router.post("/agents/jobs/{job_id}/files")
async def agents_upload_files(
    job_id: str,
    db: DbSession,
    agent: AgentAuth,
    output_zip: UploadFile | None = File(default=None),
    result_json: UploadFile | None = File(default=None),
) -> dict[str, Any]:
    """Multipart upload: ``output_zip`` (extracted into output/) + ``result_json``."""
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError("Job not found")

    output_dir = Path(job.storage_path) / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    extracted: list[str] = []
    if output_zip is not None:
        zip_dst = output_dir / "_upload.zip"
        with zip_dst.open("wb") as fp:
            fp.write(await output_zip.read())
        try:
            with zipfile.ZipFile(zip_dst, "r") as zf:
                for member in zf.namelist():
                    # Defensive against zip-slip
                    target = output_dir / member
                    target_resolved = target.resolve()
                    if output_dir.resolve() not in target_resolved.parents and target_resolved != output_dir.resolve():
                        continue
                    zf.extract(member, output_dir)
                    extracted.append(member)
        finally:
            try:
                zip_dst.unlink()
            except Exception:
                pass

    if result_json is not None:
        (output_dir / "result.json").write_bytes(await result_json.read())

    return {"detail": "ok", "extracted": extracted}


@router.post("/agents/jobs/{job_id}/status")
def agents_report_status(
    job_id: str,
    db: DbSession,
    agent: AgentAuth,
    payload: JobStatusIn,
) -> dict[str, str]:
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError("Job not found")
    now = datetime.now(timezone.utc)
    if payload.status == "success":
        job.status = JobStatus.SUCCESS
    elif payload.status == "failed":
        job.status = JobStatus.FAILED
        job.error_message = (payload.message or "")[:2048]
    else:
        raise ValidationError("status must be 'success' or 'failed'")
    job.finished_at = now
    if job.started_at is not None:
        delta = (now - job.started_at).total_seconds()
        job.duration_sec = int(delta)
    db.commit()

    # Mark the agent free again.
    agent_registry.heartbeat(db, agent_id=agent.id, status="online")
    windows_agent_client.publish_exit(job_id, payload.exit_code or 0)
    return {"detail": "ok"}


# ─────────────────────────── operator endpoints ────────────────────────────────


admin_router = APIRouter(prefix="/admin/agents", tags=["admin"])


@admin_router.post("", response_model=AgentRegisterOut)
def admin_register_agent(
    payload: AgentRegisterIn,
    db: DbSession,
    _admin: AdminUser,
) -> AgentRegisterOut:
    # Reject duplicate name early for a friendlier error.
    if db.execute(select(WindowsAgent).where(WindowsAgent.name == payload.name)).scalar_one_or_none() is not None:
        raise ConflictError("Agent name already exists")
    agent, token = agent_registry.register_agent(
        db,
        name=payload.name,
        pool=payload.pool,
        hostname=payload.hostname,
        capabilities=payload.capabilities,
        device_kind=payload.device_kind,
    )
    audit_service.safe_log(
        db,
        actor_user_id=_admin.id,
        action="agent.create",
        target_type="agent",
        target_id=str(agent.id),
        meta={
            "name": agent.name,
            "pool": agent.pool,
            "hostname": agent.hostname,
            "device_kind": agent.device_kind,
        },
    )
    return AgentRegisterOut(agent=AgentOut.from_row(agent), token=token)


@admin_router.get("", response_model=list[AgentOut])
def admin_list_agents(
    db: DbSession,
    _admin: AdminUser,
    pool: str | None = None,
    device_kind: str | None = Query(
        default=None, description="Filter, e.g. 'launcher' to see only HWAXAgents."
    ),
) -> list[AgentOut]:
    rows = agent_registry.list_agents(db, pool=pool, device_kind=device_kind)
    return [AgentOut.from_row(r) for r in rows]


@admin_router.post("/{agent_id}/rotate-token", response_model=AgentRegisterOut)
def admin_rotate_token(
    agent_id: uuid.UUID,
    db: DbSession,
    _admin: AdminUser,
) -> AgentRegisterOut:
    """Mint a fresh one-time enrollment token for an existing agent.

    For a lost/leaked token or a re-imaged workstation: the old enrollment token
    stops working, a disabled agent is re-enabled so it can re-enroll, and the
    old device's JWT chain is revoked. (The admin UI calls this route.)
    """
    result = agent_registry.rotate_enrollment_token(db, agent_id)
    if result is None:
        raise NotFoundError("Agent not found")
    agent, token = result
    agent_service.revoke_refresh_chain(db, agent.id)
    audit_service.safe_log(
        db,
        actor_user_id=_admin.id,
        action="agent.token.rotated",
        target_type="agent",
        target_id=str(agent.id),
        meta={"name": agent.name, "device_kind": agent.device_kind},
    )
    return AgentRegisterOut(agent=AgentOut.from_row(agent), token=token)


@admin_router.get("/{agent_id}", response_model=AgentOut)
def admin_get_agent(
    agent_id: uuid.UUID,
    db: DbSession,
    _admin: AdminUser,
) -> AgentOut:
    """Single-agent detail — liveness + capabilities.modules (from heartbeat)."""
    agent = db.get(WindowsAgent, agent_id)
    if agent is None:
        raise NotFoundError("Agent not found")
    return AgentOut.from_row(agent)


@admin_router.get("/{agent_id}/installs", response_model=list[InstallReportOut])
def admin_agent_installs(
    agent_id: uuid.UUID,
    db: DbSession,
    _admin: AdminUser,
    status_filter: str | None = Query(
        default=None, alias="status", description="success|failed|rolled_back|partial"
    ),
    app_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[InstallReportOut]:
    """Install-attempt history for an agent, newest first."""
    if db.get(WindowsAgent, agent_id) is None:
        raise NotFoundError("Agent not found")
    stmt = select(InstallReport).where(InstallReport.agent_id == agent_id)
    if status_filter:
        stmt = stmt.where(InstallReport.status == status_filter)
    if app_id:
        stmt = stmt.where(InstallReport.app_id == app_id)
    stmt = stmt.order_by(InstallReport.created_at.desc()).limit(limit).offset(offset)
    return [InstallReportOut.from_row(r) for r in db.execute(stmt).scalars().all()]


@admin_router.get("/{agent_id}/audit", response_model=list[AgentAuditOut])
def admin_agent_audit(
    agent_id: uuid.UUID,
    db: DbSession,
    _admin: AdminUser,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[AgentAuditOut]:
    """Audit-event history for an agent, newest first (kind/severity in meta)."""
    if db.get(WindowsAgent, agent_id) is None:
        raise NotFoundError("Agent not found")
    stmt = (
        select(AuditLog)
        .where(
            AuditLog.target_type == "windows_agent",
            AuditLog.target_id == str(agent_id),
        )
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return [AgentAuditOut.from_row(r) for r in db.execute(stmt).scalars().all()]


@admin_router.delete("/{agent_id}")
def admin_disable_agent(
    agent_id: uuid.UUID,
    db: DbSession,
    _admin: AdminUser,
) -> dict[str, str]:
    row = agent_registry.disable(db, agent_id)
    if row is None:
        raise NotFoundError("Agent not found")
    audit_service.safe_log(
        db,
        actor_user_id=_admin.id,
        action="agent.delete",
        target_type="agent",
        target_id=str(row.id),
        meta={"name": row.name, "pool": row.pool},
    )
    return {"detail": "disabled"}
