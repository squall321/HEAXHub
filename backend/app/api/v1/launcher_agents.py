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

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.core.errors import UnauthorizedError
from app.db.models.windows_agent import WindowsAgent
from app.deps import DbSession
from app.services import agent_manifest_builder, agent_service

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
def manifest(db: DbSession, request: Request, _agent: LauncherAuth) -> dict:
    """Program catalog for this launcher (contract HWAXAgentManifest)."""
    return agent_manifest_builder.cached_manifest(
        db, base_url=_public_base_url(request)
    )


# ─────────────────────────── Phase 2 stubs (501) ───────────────────────────────


def _not_implemented() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Not implemented in Phase 1 (NEXT_STEPS §3.1)",
    )


@router.post("/installs", status_code=status.HTTP_202_ACCEPTED)
def installs(_agent: LauncherAuth) -> None:
    raise _not_implemented()


@router.post("/audit", status_code=status.HTTP_202_ACCEPTED)
def audit(_agent: LauncherAuth) -> None:
    raise _not_implemented()


@router.post("/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
def heartbeat(_agent: LauncherAuth) -> None:
    raise _not_implemented()
