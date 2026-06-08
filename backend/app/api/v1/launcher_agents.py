"""HWAXAgent (Tauri 2 Windows tray launcher) HTTP surface.

Mounted at ``/api/v1/launcher-agents/`` to avoid colliding with the
pre-existing service-agent routes under ``/api/v1/agents/`` (which expect a
different body shape — e.g. ``POST /api/v1/agents/heartbeat`` already takes
``{ status, agent_version? }``).

Phase 1 endpoints (this PR):
    POST   /enroll      — bootstrap: redeem enrollment_token → JWT pair
    POST   /refresh     — rotate refresh token, return new access (+refresh)
    GET    /manifest    — programs.json (catalog, ETag/If-None-Match aware)

Phase 1 stubs (202 Accepted, body recorded but not yet persisted to a
dedicated install_reports / audit_log table — that's Phase 2):
    POST   /installs    — install-report.schema.json
    POST   /audit       — audit-event.schema.json
    POST   /heartbeat   — { agent_version, hostname?, modules[] }
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body, Header, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.errors import UnauthorizedError
from app.db.models.windows_agent import WindowsAgent
from app.deps import DbSession
from app.services import agent_manifest_builder, agent_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/launcher-agents", tags=["hwax-agent"])


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


# ── Phase 1 stubs: accept the body but don't persist yet ───────────────────────


@router.post("/installs", status_code=status.HTTP_202_ACCEPTED)
def installs(
    payload: Annotated[dict[str, Any], Body(...)],
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """Record an install attempt. Phase 1: accepted-and-logged stub."""
    agent = _current_agent(db, authorization)
    logger.info(
        "launcher install report agent=%s app_id=%s version=%s status=%s",
        agent.id,
        payload.get("app_id"),
        payload.get("version"),
        payload.get("status"),
    )
    return {"status": "accepted"}


@router.post("/audit", status_code=status.HTTP_202_ACCEPTED)
def audit(
    payload: Annotated[dict[str, Any], Body(...)],
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """Submit a single audit event. Phase 1: accepted-and-logged stub."""
    agent = _current_agent(db, authorization)
    logger.info(
        "launcher audit agent=%s kind=%s severity=%s",
        agent.id,
        payload.get("kind"),
        payload.get("severity"),
    )
    return {"status": "accepted"}


@router.post("/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
def heartbeat(
    payload: HeartbeatIn,
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    """30-minute alive ping. Updates last_seen + (best-effort) capabilities."""
    from datetime import datetime, timezone  # noqa: PLC0415

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
