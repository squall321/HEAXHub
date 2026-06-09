"""HWAXAgent (Tauri 2 Windows tray launcher) HTTP surface.

Mounted at ``/api/v1/launcher-agents/`` to avoid colliding with the
pre-existing service-agent routes under ``/api/v1/agents/`` (which expect a
different body shape — e.g. ``POST /api/v1/agents/heartbeat`` already takes
``{ status, agent_version? }``).

Endpoints:
    POST   /enroll      — bootstrap: redeem enrollment_token → JWT pair
    POST   /refresh     — rotate refresh token, return new access (+refresh)
    GET    /manifest    — programs.json (catalog, ETag/If-None-Match aware)
    POST   /installs    — persist one install attempt (install_reports)
    POST   /audit       — write one audit event (audit_log)
    POST   /heartbeat   — { agent_version, hostname?, modules[] } → last_seen
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import ForbiddenError, UnauthorizedError
from app.db.models.install_report import InstallReport
from app.db.models.windows_agent import WindowsAgent
from app.deps import DbSession
from app.services import agent_manifest_builder, agent_service, audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/launcher-agents", tags=["hwax-agent"])

# Contract truncation limits (the hub truncates oversize rather than rejecting).
_ERROR_MAX = 2048
_LOG_EXCERPT_MAX = 16384
_AGENT_MISMATCH = "agent_id does not match the authenticated launcher"


# ── auth dependency ────────────────────────────────────────────────────────────


def _extract_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("Missing Bearer token")
    return authorization.split(" ", 1)[1].strip()


def _current_agent(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> WindowsAgent:
    """FastAPI dependency: decode access_token (aud='hwax-agent'), load agent."""
    return agent_service.verify_agent_jwt(db, _extract_bearer(authorization))


# ── request / response models ──────────────────────────────────────────────────


class EnrollIn(BaseModel):
    enrollment_token: str = Field(..., min_length=10)
    hostname: str | None = None
    agent_version: str | None = None


class EnrollOut(BaseModel):
    agent_id: str
    access_token: str
    refresh_token: str
    expires_in: int


class RefreshIn(BaseModel):
    refresh_token: str = Field(..., min_length=10)


class RefreshOut(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int


class HeartbeatIn(BaseModel):
    agent_version: str
    hostname: str | None = None
    modules: list[dict[str, Any]] | None = None


class InstallReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # install-report.schema.json
    agent_id: str
    app_id: str
    version: str
    status: Literal["success", "failed", "rolled_back", "partial"]
    started_at: datetime
    finished_at: datetime
    exit_code: int | None = None
    sha256_verified: bool | None = None
    error: str | None = None
    log_excerpt: str | None = None
    previous_version: str | None = None


class AuditClientMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    os: str | None = None
    os_version: str | None = None
    agent_version: str | None = None
    hostname: str | None = None


class AuditEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # audit-event.schema.json
    agent_id: str
    kind: Literal[
        "enrollment", "install", "uninstall", "run", "stop", "rollback",
        "av_block", "sha256_mismatch", "download_failed", "policy_denied",
        "heartbeat",
    ]
    occurred_at: datetime
    severity: Literal["info", "warn", "error"]
    app_id: str | None = None
    version: str | None = None
    payload: dict[str, Any] | None = None
    client_meta: AuditClientMeta | None = None


# ── endpoints ──────────────────────────────────────────────────────────────────


@router.post("/enroll", response_model=EnrollOut)
def enroll(
    payload: EnrollIn,
    db: DbSession,
    request: Request,
) -> EnrollOut:
    """Exchange a one-time enrollment_token for a JWT pair."""
    ua = request.headers.get("user-agent")
    ip = request.client.host if request.client else None
    result = agent_service.redeem_enrollment_token(
        db,
        enrollment_token=payload.enrollment_token,
        hostname=payload.hostname,
        agent_version=payload.agent_version,
        user_agent=ua,
        ip_address=ip,
    )
    return EnrollOut(**result)


@router.post("/refresh", response_model=RefreshOut)
def refresh(
    payload: RefreshIn,
    db: DbSession,
    request: Request,
) -> RefreshOut:
    """Rotate the refresh_token. Old refresh is revoked and a new pair is issued."""
    ua = request.headers.get("user-agent")
    ip = request.client.host if request.client else None
    result = agent_service.rotate_refresh(
        db,
        refresh_token=payload.refresh_token,
        user_agent=ua,
        ip_address=ip,
    )
    return RefreshOut(**result)


@router.get("/manifest")
def manifest(
    db: DbSession,
    response: Response,
    authorization: Annotated[str | None, Header()] = None,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> dict[str, Any]:
    """Return the catalog (programs.json). Honours If-None-Match → 304."""
    agent = _current_agent(db, authorization)
    payload = agent_manifest_builder.build_manifest(db, agent=agent)
    etag = agent_manifest_builder.compute_etag(payload)
    response.headers["ETag"] = etag
    if if_none_match and if_none_match.strip() == etag:
        # FastAPI's response_model would replace this; return Response directly.
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})  # type: ignore[return-value]
    return payload


@router.post("/installs", status_code=status.HTTP_202_ACCEPTED)
def installs(
    payload: InstallReportIn,
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """Persist one install-attempt outcome (contract HWAXAgentInstallReport)."""
    agent = _current_agent(db, authorization)
    if payload.agent_id != str(agent.id):
        raise ForbiddenError(_AGENT_MISMATCH)
    report = InstallReport(
        agent_id=agent.id,
        app_id=payload.app_id,
        version=payload.version,
        status=payload.status,
        exit_code=payload.exit_code,
        started_at=payload.started_at,
        finished_at=payload.finished_at,
        sha256_verified=payload.sha256_verified,
        error=payload.error[:_ERROR_MAX] if payload.error else None,
        log_excerpt=payload.log_excerpt[:_LOG_EXCERPT_MAX] if payload.log_excerpt else None,
        previous_version=payload.previous_version,
    )
    db.add(report)
    db.commit()
    return {"status": "accepted", "id": str(report.id)}


@router.post("/audit", status_code=status.HTTP_202_ACCEPTED)
def audit(
    payload: AuditEventIn,
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """Record one launcher audit event in the shared audit_log table.

    Launcher events have ``actor_user_id=NULL`` and carry their identity in
    ``meta`` (actor='system:hwax-agent'), targeting the windows_agent.
    """
    agent = _current_agent(db, authorization)
    if payload.agent_id != str(agent.id):
        raise ForbiddenError(_AGENT_MISMATCH)
    meta: dict[str, Any] = {
        "agent_id": str(agent.id),
        "actor": "system:hwax-agent",
        "kind": payload.kind,
        "severity": payload.severity,
        "occurred_at": payload.occurred_at.isoformat(),
    }
    if payload.app_id:
        meta["app_id"] = payload.app_id
    if payload.version:
        meta["version"] = payload.version
    if payload.payload is not None:
        meta["payload"] = payload.payload
    if payload.client_meta is not None:
        meta["client_meta"] = payload.client_meta.model_dump(exclude_none=True)
    audit_service.safe_log(
        db,
        actor_user_id=None,
        action=f"agent.{payload.kind}",
        target_type="windows_agent",
        target_id=str(agent.id),
        meta=meta,
    )
    return {"status": "accepted"}


@router.post("/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
def heartbeat(
    payload: HeartbeatIn,
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    """30-minute alive ping. Updates last_seen + (best-effort) capabilities."""
    agent = _current_agent(db, authorization)
    agent.last_seen = datetime.now(timezone.utc)
    agent.agent_version = payload.agent_version
    if payload.hostname:
        agent.hostname = payload.hostname
    agent.status = "online"
    if payload.modules:
        caps = dict(agent.capabilities or {})
        caps["modules"] = payload.modules
        agent.capabilities = caps
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
