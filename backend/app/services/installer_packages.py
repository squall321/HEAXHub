"""Installer package storage + metadata for windows_gui / local_pc apps.

Files are stored on the local filesystem under:
    {settings.installer_storage_root}/{app_id}/{os}/{version}/installer.exe
    {settings.installer_storage_root}/{app_id}/{os}/{version}/installer.exe.sha256
    {settings.installer_storage_root}/{app_id}/{os}/{version}/installer.exe.sig   (optional)

The `installer_packages` row stores a relative URL the client uses to download.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import ValidationError
from app.db.models.installer_package import InstallerPackage


# ── storage helpers ────────────────────────────────────────────────────────────

# OS / version path segments are user-controllable via URL params, so we lock
# them down to a conservative character set before joining them onto the
# installer storage root. Anything outside this set (slashes, ".." segments,
# null bytes, etc.) hard-fails the request.
_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _safe_segment(label: str, value: str) -> str:
    if not isinstance(value, str) or not _PATH_SEGMENT_RE.match(value):
        raise ValidationError(f"Invalid {label}: {value!r}")
    return value


def _storage_root() -> Path:
    return Path(get_settings().installer_storage_root).expanduser().resolve()


def installer_dir(app_id: str, os_name: str, version: str) -> Path:
    """Resolve the installer storage directory, refusing path-traversal segments."""
    safe_app = _safe_segment("app_id", app_id)
    safe_os = _safe_segment("os", os_name)
    safe_version = _safe_segment("version", version)
    root = _storage_root()
    candidate = (root / safe_app / safe_os / safe_version).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValidationError(
            f"installer path escapes storage root: {candidate}"
        ) from exc
    return candidate


def installer_path(app_id: str, os_name: str, version: str) -> Path:
    return installer_dir(app_id, os_name, version) / "installer.exe"


def sha256_path(app_id: str, os_name: str, version: str) -> Path:
    return installer_dir(app_id, os_name, version) / "installer.exe.sha256"


def signature_path(app_id: str, os_name: str, version: str) -> Path:
    return installer_dir(app_id, os_name, version) / "installer.exe.sig"


_FORMAT_BY_SUFFIX = ((".msix", "msix"), (".msi", "msi"), (".zip", "zip"), (".exe", "exe"))


def infer_format(filename: str | None) -> str | None:
    """Map an uploaded filename to a manifest package type (zip|exe|msi|msix).

    Returns None for an unrecognised/absent extension so the manifest builder
    can fall back to URL inference.
    """
    if not filename:
        return None
    lowered = filename.lower()
    for suffix, fmt in _FORMAT_BY_SUFFIX:
        if lowered.endswith(suffix):
            return fmt
    return None


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ── public api ────────────────────────────────────────────────────────────────


def register_installer(
    db: Session,
    *,
    app_id: str,
    version: str,
    os: str,
    file_path: Path,
    sha256: str,
    signed: bool,
    uploaded_by: uuid.UUID | None,
    package_format: str | None = None,
) -> InstallerPackage:
    """Create / upsert an installer_packages row for an already-stored file.

    `file_path` must already point to the destination on disk; this function
    only writes metadata + the sidecar sha256 file.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"installer file missing: {file_path}")

    # Write/refresh sidecar sha256 file for transparency.
    sha_file = file_path.with_suffix(file_path.suffix + ".sha256")
    sha_file.write_text(f"{sha256}  {file_path.name}\n", encoding="utf-8")

    size_bytes = file_path.stat().st_size

    # Upsert by (app_id, version, os)
    existing = db.execute(
        select(InstallerPackage).where(
            InstallerPackage.app_id == app_id,
            InstallerPackage.version == version,
            InstallerPackage.os == os,
        )
    ).scalar_one_or_none()

    installer_url = f"/api/v1/apps/{app_id}/installers/{os}/{version}"

    if existing is not None:
        existing.installer_url = installer_url
        existing.sha256 = sha256
        existing.size_bytes = size_bytes
        existing.signed = signed
        existing.uploaded_by = uploaded_by
        existing.package_format = package_format
        db.commit()
        db.refresh(existing)
        return existing

    row = InstallerPackage(
        app_id=app_id,
        version=version,
        os=os,
        installer_url=installer_url,
        sha256=sha256,
        size_bytes=size_bytes,
        signed=signed,
        uploaded_by=uploaded_by,
        package_format=package_format,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_latest(db: Session, *, app_id: str, os: str) -> InstallerPackage | None:
    """Return the most recently uploaded installer for (app_id, os)."""
    stmt = (
        select(InstallerPackage)
        .where(InstallerPackage.app_id == app_id, InstallerPackage.os == os)
        .order_by(InstallerPackage.uploaded_at.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def list_for_app(db: Session, *, app_id: str) -> list[InstallerPackage]:
    stmt = (
        select(InstallerPackage)
        .where(InstallerPackage.app_id == app_id)
        .order_by(InstallerPackage.os.asc(), InstallerPackage.uploaded_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def get_by_av_os(
    db: Session, *, app_id: str, os: str, version: str
) -> InstallerPackage | None:
    stmt = select(InstallerPackage).where(
        InstallerPackage.app_id == app_id,
        InstallerPackage.os == os,
        InstallerPackage.version == version,
    )
    return db.execute(stmt).scalar_one_or_none()


def save_upload(
    *,
    app_id: str,
    os: str,
    version: str,
    src_path: Path,
    signature_src: Path | None = None,
) -> tuple[Path, str]:
    """Move an already-saved upload into the installer storage tree.

    Returns (destination_path, sha256_hex).
    """
    dest_dir = installer_dir(app_id, os, version)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = installer_path(app_id, os, version)
    shutil.move(str(src_path), str(dest))
    digest = compute_sha256(dest)
    if signature_src is not None:
        sig_dest = signature_path(app_id, os, version)
        shutil.move(str(signature_src), str(sig_dest))
    return dest, digest
