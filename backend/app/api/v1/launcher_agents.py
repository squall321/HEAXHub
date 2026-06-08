"""HWAXAgent launcher endpoints (NEXT_STEPS §2.4).

Separate from ``agents.py`` (polling service agents, bearer token) on purpose:
launchers authenticate with a JWT pair (``aud='hwax-agent'``) and live under the
``/api/v1/launcher-agents`` prefix so they never collide with the pre-existing
``/api/v1/agents`` polling routes (which take a different request body).

Phase 1 surface (contract ``contracts/hwax-agent/openapi.yaml`` v0.2.0):
  POST /enroll     — real (agent_service.redeem_enrollment_token); no bearer
  POST /refresh    — real (agent_service.rotate_refresh);          no bearer
  GET  /manifest   — real (agent_manifest_builder.cached_manifest); launcher JWT
  POST /installs   — 501 stub (Phase 2 §3.1)                        launcher JWT
  POST /audit      — 501 stub (Phase 2 §3.1)                        launcher JWT
  POST /heartbeat  — 501 stub (Phase 2 §3.1)                        launcher JWT
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.core.errors import ForbiddenError, UnauthorizedError
from app.db.models.install_report import InstallReport
from app.db.models.windows_agent import WindowsAgent
from app.deps import DbSession
from app.services import agent_manifest_builder, agent_service, audit_service

router = APIRouter(prefix="/launcher-agents", tags=["hwax-agent"])


# ─────────────────────────── launcher JWT auth dep ─────────────────────────────


def get_launcher_agent(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> WindowsAgent:
    """Resolve the bearer access token to its launcher agent.

    ``agent_service.verify_agent_jwt`` enforces ``aud='hwax-agent'``, so a plain
    user token (no audience) is rejected here — and, conversely, a launcher token
    is rejected on user routes by the audience-less user decode.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("Missing launcher access token")
    token = authorization.split(" ", 1)[1].strip()
    return agent_service.verify_agent_jwt(db, access_token=token)


LauncherAuth = Annotated[WindowsAgent, Depends(get_launcher_agent)]


# ─────────────────────────── schemas ───────────────────────────────────────────


class EnrollIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # contract: additionalProperties:false
    enrollment_token: str
    hostname: str | None = None
    agent_version: str | None = None


class EnrollOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str
    access_token: str
    refresh_token: str
    expires_in: int = Field(..., description="access_token lifetime in seconds")


class RefreshIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str


class RefreshOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access_token: str
    refresh_token: str
    expires_in: int


# Phase 2 reporting bodies — mirror the contract JSON Schemas (so FastAPI gives a
# native 422 on a malformed body; no jsonschema dependency needed).


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


class HeartbeatModule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    version: str


class HeartbeatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # openapi heartbeat body
    agent_version: str
    hostname: str | None = None
    modules: list[HeartbeatModule] | None = None


# ─────────────────────────── helpers ───────────────────────────────────────────


def _client_meta(request: Request) -> tuple[str | None, str | None]:
    user_agent = request.headers.get("user-agent")
    ip = request.client.host if request.client else None
    return user_agent, ip


def _public_base_url(request: Request) -> str:
    """Public origin the launcher reaches us at, for absolute installer URLs.

    Honours an explicit ``agent_public_base_url`` setting first; otherwise rebuilds
    from the standard reverse-proxy headers so manifest download URLs are correct
    when the HWAX portal proxies us under ``/heax-hub`` (it strips that prefix
    before forwarding, so the backend only learns it from ``X-Forwarded-Prefix``).
    Falls back to the request's own scheme/host for direct access.
    """
    override = get_settings().agent_public_base_url.strip()
    if override:
        return override.rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    )
    raw_prefix = request.headers.get("x-forwarded-prefix", "").strip("/")
    prefix = f"/{raw_prefix}" if raw_prefix else ""
    return f"{proto}://{host}{prefix}"


# ─────────────────────────── real endpoints ────────────────────────────────────


@router.post("/enroll", response_model=EnrollOut)
def enroll(payload: EnrollIn, db: DbSession, request: Request) -> EnrollOut:
    """Exchange a one-time enrollment token for a JWT pair. No bearer required."""
    user_agent, ip = _client_meta(request)
    result = agent_service.redeem_enrollment_token(
        db,
        enrollment_token=payload.enrollment_token,
        hostname=payload.hostname,
        agent_version=payload.agent_version,
        user_agent=user_agent,
        ip_address=ip,
    )
    return EnrollOut(
        agent_id=str(result.agent.id),
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in=result.access_expires_in,
    )


@router.post("/refresh", response_model=RefreshOut)
def refresh(payload: RefreshIn, db: DbSession, request: Request) -> RefreshOut:
    """Rotate the refresh token and mint a fresh access token. No bearer required."""
    user_agent, ip = _client_meta(request)
    result = agent_service.rotate_refresh(
        db,
        refresh_token=payload.refresh_token,
        user_agent=user_agent,
        ip_address=ip,
    )
    return RefreshOut(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in=result.access_expires_in,
    )


@router.get("/manifest")
def manifest(
    db: DbSession,
    request: Request,
    _agent: LauncherAuth,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    """Program catalog for this launcher (contract HWAXAgentManifest).

    Sets a strong ETag over the catalog content; an unchanged catalog returns
    304 (no body) on a matching ``If-None-Match`` so fleet-wide polls stay cheap.
    """
    body = agent_manifest_builder.cached_manifest(db, base_url=_public_base_url(request))
    etag = agent_manifest_builder.manifest_etag(body)
    if if_none_match is not None and if_none_match.strip() == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    return JSONResponse(content=body, headers={"ETag": etag})


# ─────────────────────────── reporting endpoints (§3.1) ────────────────────────

# A report/event/heartbeat may only be filed for the authenticated agent itself.
_AGENT_MISMATCH = "agent_id does not match the authenticated launcher"

# Contract truncation limits (the hub truncates rather than rejecting oversize).
_ERROR_MAX = 2048
_LOG_EXCERPT_MAX = 16384


@router.post("/installs", status_code=status.HTTP_202_ACCEPTED)
def installs(payload: InstallReportIn, db: DbSession, agent: LauncherAuth) -> dict:
    """Persist one install-attempt outcome (contract HWAXAgentInstallReport)."""
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
    return {"detail": "accepted", "id": str(report.id)}


@router.post("/audit", status_code=status.HTTP_202_ACCEPTED)
def audit(payload: AuditEventIn, db: DbSession, agent: LauncherAuth) -> dict:
    """Record one launcher audit event in the shared audit_log table.

    Launcher events have ``actor_user_id=NULL`` and carry their identity in
    ``meta`` (actor="system:hwax-agent"), targeting the windows_agent.
    """
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
    return {"detail": "accepted"}


@router.post("/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
def heartbeat(payload: HeartbeatIn, db: DbSession, agent: LauncherAuth) -> Response:
    """30-minute alive ping: refresh last_seen (+ version/hostname/modules)."""
    agent.last_seen = datetime.now(timezone.utc)
    if payload.agent_version:
        agent.agent_version = payload.agent_version
    if payload.hostname:
        agent.hostname = payload.hostname
    if payload.modules is not None:
        # Merge installed-module versions into capabilities for the fleet view
        # (reassign so SQLAlchemy detects the JSON change). Status is left
        # untouched: a launcher is not a job-dispatch target like a service agent.
        caps = dict(agent.capabilities or {})
        caps["modules"] = {m.id: m.version for m in payload.modules}
        agent.capabilities = caps
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
