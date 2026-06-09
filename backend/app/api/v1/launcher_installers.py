"""HWAXAgent launcher-side installer endpoints.

Three routes, all under ``/api/v1/installers/`` to match the URLs the
launcher client (Tauri 2) was built against, plus the portal's public
download page:

    GET /api/v1/installers/{id}/download      — bearer aud='hwax-agent';
                                                302 redirect to installer_url
                                                (with X-Sha256 hint header).
    GET /api/v1/installers/{app_id}/latest    — Tauri updater feed (public,
                                                Ed25519-signed payload).
                                                204 when no installer yet.
    GET /api/v1/installers/{app_id}/public-latest — Public download for the
                                                portal SPA. Returns JSON
                                                {version, sha256, size_bytes,
                                                 download_url, uploaded_at}
                                                for the latest Windows
                                                installer. 404 when none.
                                                Auth NOT required (decision
                                                Q2: public — corp-portal
                                                already gates access).

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
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select

from app.core.errors import UnauthorizedError
from app.db.models.app import App
from app.db.models.installer_package import InstallerPackage
from app.deps import DbSession
from app.services import agent_manifest_builder, agent_service, installer_packages

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


def _servable(db: DbSession, app_id: str) -> bool:
    """Least privilege: only serve an installer when its app exists and isn't
    retired (ARCHIVED) — mirrors the manifest's status gate so a draft/archived
    app's binary can't be pulled via these download routes."""
    app = db.get(App, app_id)
    return app is not None and agent_manifest_builder.is_servable_installer_app(app)


# ── /installers/{id}/download ──────────────────────────────────────────────────


@router.get("/{installer_id}/download")
def download(
    installer_id: str,
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    """Serve the installer to a launcher after agent JWT verification.

    Internal/relative ``installer_url`` (the on-disk deployment) → stream the
    bytes directly. Absolute ``installer_url`` (object storage) → 302 redirect.
    Either way the agent verifies bytes against ``X-Sha256`` / the manifest's
    ``programs[].package.sha256``.
    """
    _require_agent(db, authorization)

    try:
        pkg_uuid = uuid.UUID(installer_id)
    except ValueError:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    pkg = db.get(InstallerPackage, pkg_uuid)
    if pkg is None or not _servable(db, pkg.app_id):
        # 404 (not 403) so a draft/archived installer id isn't even confirmed.
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    url = (pkg.installer_url or "").strip()
    if url.startswith("http://") or url.startswith("https://"):
        # Absolute (object storage / presigned) → redirect; the agent follows it.
        resp = RedirectResponse(url, status_code=302)
        resp.headers["X-Sha256"] = pkg.sha256
        if pkg.size_bytes:
            resp.headers["X-Size-Bytes"] = str(pkg.size_bytes)
        return resp

    # Internal/relative installer_url (current on-disk deployment): STREAM the
    # bytes. We must NOT 302 to the relative /apps/{app_id}/installers/{os}/{version}
    # route — that needs a *user* JWT (the agent's launcher JWT would 401) and its
    # root-relative Location drops the portal /heax-hub prefix. Mirror
    # public-download, but behind the launcher JWT.
    file_path = installer_packages.installer_path(pkg.app_id, pkg.os, pkg.version)
    if not file_path.exists():
        return Response(status_code=status.HTTP_410_GONE)
    return FileResponse(
        path=str(file_path),
        media_type="application/octet-stream",
        filename=f"{pkg.app_id}-{pkg.version}-{pkg.os}.exe",
        headers={
            "X-Sha256": pkg.sha256,
            "X-Installer-SHA256": pkg.sha256,
            "X-Installer-Version": pkg.version,
            "X-Installer-Signed": "1" if pkg.signed else "0",
        },
    )


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

    if pkg is None or not _servable(db, app_id):
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


# ── /installers/{app_id}/public-latest ─────────────────────────────────────────


@router.get("/{app_id}/public-latest")
def public_latest(
    app_id: str,
    db: DbSession,
) -> Response:
    """Public latest-installer metadata for the portal download page.

    Unlike the bearer-gated ``/installers/{id}/download``, this endpoint is
    **public** — the design decision (PR #3 Q2 = public) is that the corp
    portal already gates who reaches the page, and the installer payload is
    integrity-checked by sha256 anyway. The endpoint exists so the portal
    SPA can show version/size/sha256 before the user clicks "Download".

    Response (200):
        {
          "app_id": "hwax-agent",
          "version": "1.2.3",
          "sha256": "abc...",
          "size_bytes": 12345678,
          "uploaded_at": "2026-06-08T00:00:00Z",
          "download_url": "/api/v1/apps/hwax-agent/installers/windows-x64/1.2.3"
        }

    Response (404): no Windows installer has been uploaded for this app yet.
    """
    pkg = db.execute(
        select(InstallerPackage)
        .where(InstallerPackage.app_id == app_id)
        .where(InstallerPackage.os.like("windows%"))
        .order_by(InstallerPackage.uploaded_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if pkg is None or not _servable(db, app_id):
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    payload: dict[str, Any] = {
        "app_id": app_id,
        "version": pkg.version,
        "sha256": pkg.sha256,
        "size_bytes": int(pkg.size_bytes) if pkg.size_bytes else None,
        "signed": bool(pkg.signed),
        "uploaded_at": pkg.uploaded_at.isoformat() if pkg.uploaded_at else None,
        # Points at the public streaming route below — kept under this same
        # router so the contract surface is one cohesive block.
        "download_url": f"/api/v1/installers/{app_id}/public-download",
    }
    return Response(
        content=__import__("json").dumps(payload),
        media_type="application/json",
        status_code=status.HTTP_200_OK,
    )


# ── /installers/{app_id}/public-download ───────────────────────────────────────


@router.get("/{app_id}/public-download")
def public_download(
    app_id: str,
    db: DbSession,
) -> FileResponse:
    """Stream the latest Windows installer file. Public (no auth).

    Same integrity guarantee as ``public-latest``: the corp portal already
    gates page access, the sha256 is exposed for client-side verification
    (``X-Installer-SHA256`` response header), and the launcher itself is
    self-update-signed via :func:`latest`.
    """
    pkg = db.execute(
        select(InstallerPackage)
        .where(InstallerPackage.app_id == app_id)
        .where(InstallerPackage.os.like("windows%"))
        .order_by(InstallerPackage.uploaded_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if pkg is None or not _servable(db, app_id):
        return Response(status_code=status.HTTP_404_NOT_FOUND)  # type: ignore[return-value]

    file_path = installer_packages.installer_path(app_id, pkg.os, pkg.version)
    if not file_path.exists():
        # Row exists but the artefact disappeared (operator removed the
        # file out of band, or this is a brand-new App with the row seeded
        # by 0009 before any installer was uploaded).
        return Response(status_code=status.HTTP_410_GONE)  # type: ignore[return-value]

    filename = f"{app_id}-{pkg.version}-{pkg.os}.exe"
    return FileResponse(
        path=str(file_path),
        media_type="application/octet-stream",
        filename=filename,
        headers={
            "X-Installer-SHA256": pkg.sha256,
            "X-Installer-Version": pkg.version,
            "X-Installer-Signed": "1" if pkg.signed else "0",
        },
    )
