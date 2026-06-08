"""HWAXAgent launcher-side installer endpoints.

Two routes, both under ``/api/v1/installers/`` to match the URLs the launcher
client (Tauri 2) was built against:

    GET /api/v1/installers/{id}/download      — bearer aud='hwax-agent';
                                                302 redirect to installer_url
                                                (with X-Sha256 hint header).
    GET /api/v1/installers/{app_id}/latest    — Tauri updater feed (public,
                                                Ed25519-signed payload).
                                                204 when no installer yet.

These are kept separate from the operator-facing ``apps/{app_id}/installers/*``
routes in :mod:`app.api.v1.installers` because:
  - the auth model differs (agent JWT vs user JWT, or none for updater feed),
  - the URL shape is what the contract (``contracts/hwax-agent/openapi.yaml``)
    pins, so collisions would break the launcher.
"""
from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.core.errors import UnauthorizedError
from app.db.models.installer_package import InstallerPackage
from app.deps import DbSession
from app.services import agent_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/installers", tags=["hwax-agent"])


def _require_agent(
    db: DbSession,
    authorization: str | None,
):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("Missing Bearer token")
    token = authorization.split(" ", 1)[1].strip()
    return agent_service.verify_agent_jwt(db, token)


# ── /installers/{id}/download ──────────────────────────────────────────────────


@router.get("/{installer_id}/download")
def download(
    installer_id: str,
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    """Redirect to the actual installer URL after agent JWT verification.

    The launcher follows the 302 and verifies the downloaded bytes against
    ``X-Sha256`` (and the manifest's ``programs[].package.sha256``).
    """
    _require_agent(db, authorization)

    try:
        pkg_uuid = uuid.UUID(installer_id)
    except ValueError:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    pkg = db.get(InstallerPackage, pkg_uuid)
    if pkg is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    resp = RedirectResponse(pkg.installer_url, status_code=302)
    resp.headers["X-Sha256"] = pkg.sha256
    if pkg.size_bytes:
        resp.headers["X-Size-Bytes"] = str(pkg.size_bytes)
    return resp


# ── /installers/{app_id}/latest ────────────────────────────────────────────────


@router.get("/{app_id}/latest")
def latest(
    app_id: str,
    db: DbSession,
) -> Response:
    """Tauri updater feed: static JSON conforming to TauriUpdaterManifest.

    Public endpoint — integrity is guaranteed by the per-platform Ed25519
    ``signature`` field, not by transport auth. Returns 204 when no Windows
    installer is registered yet.

    Phase 1: ``signature`` is emitted as ``""`` because the signing pipeline
    is not yet wired. Tauri will log a warning but continue — flip the
    feature flag on the agent side to enforce when the key is in place.
    """
    pkg = db.execute(
        select(InstallerPackage)
        .where(InstallerPackage.app_id == app_id)
        .where(InstallerPackage.os.like("windows%"))
        .order_by(InstallerPackage.uploaded_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if pkg is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    payload: dict[str, Any] = {
        "version": pkg.version,
        "notes": "",  # TODO Phase 2 — pull from latest changelog row
        "pub_date": pkg.uploaded_at.isoformat() if pkg.uploaded_at else None,
        "platforms": {
            # Tauri convention for Windows x86_64.
            "windows-x86_64": {
                "signature": "",  # TODO Phase 2 — read sidecar .sig file
                "url": pkg.installer_url,
            },
        },
    }
    return Response(
        content=__import__("json").dumps(payload),
        media_type="application/json",
        status_code=status.HTTP_200_OK,
    )
