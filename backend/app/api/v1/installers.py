"""Installer package endpoints — upload / list / download Windows installers.

Mounted under the existing ``/apps`` prefix so URLs match installer_url:
    POST   /api/v1/apps/{app_id}/installers          (operator)
    GET    /api/v1/apps/{app_id}/installers          (any authed user)
    GET    /api/v1/apps/{app_id}/installers/{os}/{version}    (download)
    GET    /api/v1/apps/{app_id}/installers/latest?os=windows-x64    (redirect)
    GET    /api/v1/apps/{app_id}/protocol.reg        (download .reg)
"""
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse

from app.api.v1.launcher_agents import LauncherAuth
from app.config import get_settings
from app.core.errors import GoneError, NotFoundError
from app.db.models.app import App
from app.db.models.installer_package import InstallerPackage
from app.deps import AdminUser, CurrentUser, DbSession, get_app_or_404
from app.services import (
    agent_manifest_builder,
    audit_service,
    custom_protocol,
    installer_packages,
)

# HEAXHub OS slug for the agent's own build; mapped to the Tauri target-triple
# key "windows-x86_64" in the updater feed.
WINDOWS_OS = "windows-x64"

router = APIRouter(prefix="/apps", tags=["installers"])

# Launcher-facing download router, mounted on its own /installers prefix and
# secured by the launcher JWT (aud=hwax-agent) — NOT user auth. This is the
# endpoint the manifest's package.url points at (§2.3 / §2.5).
download_router = APIRouter(prefix="/installers", tags=["hwax-agent"])


def _public_base_url(request: Request) -> str:
    """Public origin the client reached us at, for absolute URLs in responses.

    Honours ``agent_public_base_url`` first; otherwise rebuilds from the standard
    reverse-proxy headers so URLs are correct when the HWAX portal proxies us under
    ``/heax-hub`` (it strips the prefix before forwarding, so the backend only
    learns it from ``X-Forwarded-Prefix``). Falls back to the request's own origin.

    (Mirrors ``launcher_agents._public_base_url`` — kept local so this download
    router stays a self-contained unit.)
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


# ───────────────────────────── upload ──────────────────────────────────────────


@router.post("/{app_id}/installers")
async def upload_installer(
    app_id: str,
    db: DbSession,
    admin: AdminUser,
    version: str = Form(...),
    os: str = Form(...),
    signed: bool = Form(default=False),
    installer: UploadFile = File(...),
    signature: UploadFile | None = File(default=None),
) -> dict[str, Any]:
    """Operator-only multipart upload of an installer (+ optional .sig)."""
    _ = get_app_or_404(app_id, db)  # 404 guard

    # Stream the upload to a temp file, then move into the storage tree.
    with tempfile.NamedTemporaryFile(delete=False, suffix=".upload") as tmp:
        tmp_path = Path(tmp.name)
        while True:
            chunk = await installer.read(1024 * 1024)
            if not chunk:
                break
            tmp.write(chunk)

    sig_tmp: Path | None = None
    if signature is not None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sig.upload") as stmp:
            sig_tmp = Path(stmp.name)
            while True:
                chunk = await signature.read(1024 * 1024)
                if not chunk:
                    break
                stmp.write(chunk)

    dest, sha = installer_packages.save_upload(
        app_id=app_id,
        os=os,
        version=version,
        src_path=tmp_path,
        signature_src=sig_tmp,
    )

    row = installer_packages.register_installer(
        db,
        app_id=app_id,
        version=version,
        os=os,
        file_path=dest,
        sha256=sha,
        signed=signed,
        uploaded_by=admin.id,
        # Capture the real format from the original upload filename (the on-disk
        # copy is always named installer.exe, so this is the only honest source).
        package_format=installer_packages.infer_format(installer.filename),
    )
    return {
        "id": str(row.id),
        "app_id": row.app_id,
        "version": row.version,
        "os": row.os,
        "installer_url": row.installer_url,
        "sha256": row.sha256,
        "size_bytes": row.size_bytes,
        "signed": row.signed,
        "package_format": row.package_format,
    }


# ───────────────────────────── list / get ──────────────────────────────────────


@router.get("/{app_id}/installers")
def list_installers(
    app_id: str,
    db: DbSession,
    _user: CurrentUser,
) -> list[dict[str, Any]]:
    _ = get_app_or_404(app_id, db)
    rows = installer_packages.list_for_app(db, app_id=app_id)
    return [
        {
            "id": str(r.id),
            "app_id": r.app_id,
            "version": r.version,
            "os": r.os,
            "installer_url": r.installer_url,
            "sha256": r.sha256,
            "size_bytes": r.size_bytes,
            "signed": r.signed,
            "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
        }
        for r in rows
    ]


@router.get("/{app_id}/installers/latest")
def latest_installer(
    app_id: str,
    db: DbSession,
    user: CurrentUser,
    os: str = "windows-x64",
) -> RedirectResponse:
    """Convenience redirect to the latest version for the given OS."""
    _ = get_app_or_404(app_id, db)
    row = installer_packages.get_latest(db, app_id=app_id, os=os)
    if row is None:
        raise NotFoundError("No installer published for this app/os")
    return RedirectResponse(
        url=f"/api/v1/apps/{app_id}/installers/{os}/{row.version}",
        status_code=307,
    )


@router.get("/{app_id}/installers/{os}/{version}")
def download_installer(
    app_id: str,
    os: str,
    version: str,
    db: DbSession,
    _user: CurrentUser,
) -> FileResponse:
    row = installer_packages.get_by_av_os(db, app_id=app_id, os=os, version=version)
    if row is None:
        raise NotFoundError("Installer not found")
    file_path = installer_packages.installer_path(app_id, os, version)
    if not file_path.exists():
        raise GoneError("Installer file missing on disk")
    return FileResponse(
        path=str(file_path),
        media_type="application/octet-stream",
        filename=f"{app_id}-{version}-{os}.exe",
        headers={"X-Installer-SHA256": row.sha256, "X-Installer-Signed": "1" if row.signed else "0"},
    )


# ───────────────────────────── custom protocol asset ───────────────────────────


@router.get("/{app_id}/protocol.reg")
def download_protocol_reg(
    app_id: str,
    db: DbSession,
    _user: CurrentUser,
) -> PlainTextResponse:
    """Return a Windows .reg file that registers `heaxhub-<app_id>://` on the user's PC."""
    app: App = get_app_or_404(app_id, db)

    # Pull the protocol scheme + exe path from app.extra.launch.* with fallbacks.
    launch_cfg: dict[str, Any] = {}
    if isinstance(app.extra, dict):
        launch_cfg = app.extra.get("launch") or {}

    protocol = launch_cfg.get("protocol") or f"heaxhub-{app_id}"
    exe_path = launch_cfg.get("local_exe_path") or r"C:\\Program Files\\HEAXHub\\launcher.exe"

    body = custom_protocol.generate_reg_file(app_id, protocol, exe_path)
    return PlainTextResponse(
        content=body,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{app_id}.reg"',
        },
    )


@router.delete("/{app_id}/installers/{installer_id}")
def delete_installer(
    app_id: str,
    installer_id: uuid.UUID,
    db: DbSession,
    admin: AdminUser,
) -> dict[str, bool]:
    """Operator-only: remove an installer package (row + on-disk artifact).

    The admin UI's installer list (InstallerUploader → installersApi.remove) calls
    this; it expects ``{ ok: true }``. 404 if the id is unknown or belongs to a
    different app (so an id from another app can't be deleted via this path).
    """
    row = installer_packages.delete_installer(db, installer_id, app_id=app_id)
    if row is None:
        raise NotFoundError("Installer not found")
    audit_service.safe_log(
        db,
        actor_user_id=admin.id,
        action="installer.delete",
        target_type="app",
        target_id=app_id,
        meta={"installer_id": str(installer_id), "version": row.version, "os": row.os},
    )
    return {"ok": True}


# ───────────────────────────── launcher download-by-id ──────────────────────────


@download_router.get("/{installer_id}/download")
def download_installer_by_id(
    installer_id: uuid.UUID,
    db: DbSession,
    _agent: LauncherAuth,
):
    """Serve the installer payload for an ``InstallerPackage`` id to a launcher.

    This is the endpoint the manifest's ``package.url`` points at (§2.3). Two
    cases, decided by the stored ``installer_url``:

    * **Absolute URL** (``http(s)://…``) — object storage / presigned. We 302
      to it (the agent follows the redirect, then verifies SHA-256). This is the
      shape the contract documents.
    * **Internal relative URL** (the current deployment — files live on local
      disk under ``installer_storage_root``) — we stream the bytes directly
      (200, ``application/octet-stream``) since there is nothing to redirect to.
      The SHA-256 is echoed in ``X-Installer-SHA256`` for convenience; the agent
      verifies against ``manifest.programs[].package.sha256`` regardless.

    Either way the agent treats this as "GET → installer bytes" and must verify
    the hash. (Auth: launcher JWT only — a user token is rejected.)
    """
    row = db.get(InstallerPackage, installer_id)
    if row is None:
        raise NotFoundError("Installer not found")

    # Least privilege: don't serve installers for DRAFT (unreleased) or ARCHIVED
    # (retired) apps, even to a valid launcher JWT that guesses/learns the id —
    # mirror the manifest's status gate. 404 (not 403) so we don't confirm the id.
    app = db.get(App, row.app_id)
    if app is None or not agent_manifest_builder.is_servable_installer_app(app):
        raise NotFoundError("Installer not found")

    url = (row.installer_url or "").strip()
    if url.startswith("http://") or url.startswith("https://"):
        return RedirectResponse(url=url, status_code=302)

    # Internal disk storage: stream the file located by (app_id, os, version).
    file_path = installer_packages.installer_path(row.app_id, row.os, row.version)
    if not file_path.exists():
        raise GoneError("Installer file missing on disk")
    return FileResponse(
        path=str(file_path),
        media_type="application/octet-stream",
        filename=f"{row.app_id}-{row.version}-{row.os}.exe",
        headers={
            "X-Installer-SHA256": row.sha256,
            "X-Installer-Signed": "1" if row.signed else "0",
        },
    )


# ───────────────────────────── agent self-update feed ───────────────────────────


@download_router.get("/{app_id}/latest")
def updater_feed(app_id: str, db: DbSession, request: Request):
    """Tauri-updater feed for the agent's own self-update (contract
    ``GET /api/v1/installers/{app_id}/latest``, used with app_id="hwax-agent").

    NOT bearer-auth'd: the feed is public metadata and integrity comes from the
    Ed25519/minisign **signature** (verified by the agent's tauri-plugin-updater
    against the pubkey pinned in tauri.conf.json), not the transport. The
    ``platforms[*].url`` points at the bearer-protected
    ``/api/v1/installers/{id}/download`` — the agent's updater carries its access
    token on that request (so we don't expose a public byte stream).

    Returns the Tauri v2 static-JSON shape, or **204** when there is nothing
    signature-verifiable to offer (no build, or no minisign ``.sig`` on file).
    Note the minisign ``.sig`` is the one ``tauri build`` emits — distinct from
    the Authenticode signature embedded in the .exe by scripts/sign.ps1.
    """
    row = installer_packages.get_latest(db, app_id=app_id, os=WINDOWS_OS)
    if row is None:
        return Response(status_code=204)

    sig_path = installer_packages.signature_path(app_id, WINDOWS_OS, row.version)
    if not sig_path.exists():
        return Response(status_code=204)
    signature = sig_path.read_text(encoding="utf-8").strip()
    if not signature:
        return Response(status_code=204)

    base = _public_base_url(request)
    payload: dict[str, Any] = {
        "version": row.version,
        "platforms": {
            # HEAXHub os slug "windows-x64" → Tauri target-triple key.
            "windows-x86_64": {
                "signature": signature,
                "url": f"{base}/api/v1/installers/{row.id}/download",
            }
        },
    }
    if row.uploaded_at is not None:
        payload["pub_date"] = row.uploaded_at.isoformat()
    return payload


# ───────────────────────────── portal public download ─────────────────────────


@download_router.get("/{app_id}/public-latest")
def public_latest(app_id: str, db: DbSession, request: Request) -> Response:
    """Public latest-installer metadata for the portal download page.

    Unlike the bearer-gated ``/installers/{id}/download``, this endpoint is
    public — the corp portal already gates page access and the installer
    payload is sha256-checked by the SPA before the user runs it. The portal
    SPA at ``/heax-hub/download`` hits this to render version/size/sha256.

    Response (200):
        {app_id, version, sha256, size_bytes, signed, uploaded_at, download_url}
    Response (404): no Windows installer registered for this app yet.
    """
    row = installer_packages.get_latest(db, app_id=app_id, os=WINDOWS_OS)
    if row is None:
        return Response(status_code=404)
    payload: dict[str, Any] = {
        "app_id": app_id,
        "version": row.version,
        "sha256": row.sha256,
        "size_bytes": int(row.size_bytes) if row.size_bytes else None,
        "signed": bool(row.signed),
        "uploaded_at": row.uploaded_at.isoformat() if row.uploaded_at else None,
        # Absolute, portal-prefix-aware URL (same as updater_feed / the agent manifest) so a client
        # using this field directly hits /heax-hub/api/v1/... and not the prefix-less portal root.
        "download_url": f"{_public_base_url(request)}/api/v1/installers/{app_id}/public-download",
    }
    import json as _json
    return Response(
        content=_json.dumps(payload),
        media_type="application/json",
        status_code=200,
    )


@download_router.get("/{app_id}/public-download")
def public_download(app_id: str, db: DbSession):
    """Stream the latest Windows installer file. Public (no auth).

    Same integrity guarantee as :func:`public_latest`. Sends
    ``X-Installer-SHA256`` / ``X-Installer-Version`` / ``X-Installer-Signed``
    so the SPA can verify before launching.
    """
    row = installer_packages.get_latest(db, app_id=app_id, os=WINDOWS_OS)
    if row is None:
        return Response(status_code=404)
    file_path = installer_packages.installer_path(app_id, row.os, row.version)
    if not file_path.exists():
        return Response(status_code=410)
    return FileResponse(
        path=str(file_path),
        media_type="application/octet-stream",
        filename=f"{app_id}-{row.version}-{row.os}.exe",
        headers={
            "X-Installer-SHA256": row.sha256,
            "X-Installer-Version": row.version,
            "X-Installer-Signed": "1" if row.signed else "0",
        },
    )
