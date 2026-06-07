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
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    Header,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.errors import ConflictError, NotFoundError, UnauthorizedError, ValidationError
from app.db.models.job import Job, JobStatus
from app.db.models.windows_agent import WindowsAgent
from app.deps import AdminUser, DbSession
from app.runners import windows_agent_client
from app.services import agent_registry, audit_service

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
    # 'launcher' = HWAXAgent (JWT launcher); 'service' = polling worker. Omitted
    # ⇒ NULL, preserving the existing service-agent registration flow.
    device_kind: Literal["launcher", "service"] | None = None


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
) -> list[AgentOut]:
    rows = agent_registry.list_agents(db, pool=pool)
    return [AgentOut.from_row(r) for r in rows]


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
