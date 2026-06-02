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
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse

from app.core.errors import GoneError, NotFoundError
from app.db.models.app import App
from app.deps import AdminUser, CurrentUser, DbSession, get_app_or_404
from app.services import custom_protocol, installer_packages

router = APIRouter(prefix="/apps", tags=["installers"])


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
