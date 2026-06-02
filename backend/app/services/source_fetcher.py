"""Source abstraction — fetch upstream source into a workspace from various source types.

source_config schema (mirrors manifest schema v2 `source` block)::

    {
      "type": "git" | "archive_url" | "local_path" | "system_command" | "docker_image",
      "url": "https://...",
      "path": "/mnt/nas/...",                # local_path
      "sha256": "<hex>",
      "image": "registry/name:tag",          # docker_image
      "verify_command": "lmstat -a",         # system_command
      "sync": "rsync" | "symlink" | "copy",  # local_path; default rsync
      "auth": {"type": "basic", "secret_key": "..."}
    }
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any

import httpx
from git import GitCommandError, Repo

from app.config import get_settings
from app.core.errors import ValidationError
from app.core.logger import get_logger
from app.services import secret_manager

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def fetch_source(source_config: dict[str, Any], dest: Path) -> dict[str, Any]:
    """Fetch source described by ``source_config`` into ``dest`` directory.

    Returns a dict with whichever of these are known:
        - commit_sha     (git)
        - sha256         (archive_url, docker_image-stub)
        - fetched_from   (echoes the input source)
        - sync_mode      (local_path)
        - image          (docker_image)
    """
    if not isinstance(source_config, dict):
        raise ValidationError("source_config must be an object")
    stype = source_config.get("type")
    if stype not in {"git", "archive_url", "local_path", "system_command", "docker_image"}:
        raise ValidationError(f"Unsupported source type: {stype!r}")

    dest.mkdir(parents=True, exist_ok=True)
    # Clean dest first (consistent with sync_tasks.clone_upstream behavior).
    _wipe_dir(dest)

    if stype == "git":
        return _fetch_git(source_config, dest)
    if stype == "archive_url":
        return _fetch_archive(source_config, dest)
    if stype == "local_path":
        return _fetch_local_path(source_config, dest)
    if stype == "system_command":
        return _fetch_system_command(source_config, dest)
    if stype == "docker_image":
        return _fetch_docker_image_stub(source_config, dest)
    raise ValidationError(f"Unsupported source type: {stype!r}")  # pragma: no cover


# ---------------------------------------------------------------------------
# Type-specific fetchers
# ---------------------------------------------------------------------------


def _fetch_git(cfg: dict[str, Any], dest: Path) -> dict[str, Any]:
    url = cfg.get("url")
    if not isinstance(url, str) or not url:
        raise ValidationError("git source requires 'url'")
    _enforce_git_host(url)
    try:
        repo = Repo.clone_from(url, str(dest), depth=1)
        commit = repo.head.commit.hexsha
    except GitCommandError:
        logger.exception("git clone failed: url=%s", url)
        raise  # propagate to caller
    return {"commit_sha": commit, "fetched_from": {"type": "git", "url": url}}


def _fetch_archive(cfg: dict[str, Any], dest: Path) -> dict[str, Any]:
    url = cfg.get("url")
    if not isinstance(url, str) or not url:
        raise ValidationError("archive_url source requires 'url'")
    expected_sha = cfg.get("sha256")
    auth = _resolve_auth(cfg.get("auth"))

    # Preserve the URL extension so _extract_archive can dispatch by name.
    from urllib.parse import urlparse
    url_path = urlparse(url).path
    suffix = ""
    for ext in (".tar.gz", ".tgz", ".tar.bz2", ".tbz", ".tar.xz", ".txz", ".tar", ".zip"):
        if url_path.lower().endswith(ext):
            suffix = ext
            break
    download_path = dest.parent / f".__archive_{dest.name}{suffix}"
    download_path.parent.mkdir(parents=True, exist_ok=True)

    # Stream download.
    with httpx.stream("GET", url, follow_redirects=True, auth=auth, timeout=120.0) as resp:
        resp.raise_for_status()
        hasher = hashlib.sha256()
        with download_path.open("wb") as f:
            for chunk in resp.iter_bytes(chunk_size=1 << 16):
                f.write(chunk)
                hasher.update(chunk)
    actual_sha = hasher.hexdigest()

    if expected_sha and actual_sha.lower() != str(expected_sha).lower():
        download_path.unlink(missing_ok=True)
        raise ValidationError(
            f"sha256 mismatch for archive {url}: expected {expected_sha}, got {actual_sha}"
        )

    try:
        _extract_archive(download_path, dest)
    finally:
        download_path.unlink(missing_ok=True)

    return {
        "sha256": actual_sha,
        "fetched_from": {"type": "archive_url", "url": url},
    }


def _fetch_local_path(cfg: dict[str, Any], dest: Path) -> dict[str, Any]:
    raw = cfg.get("path") or cfg.get("url")
    if not isinstance(raw, str) or not raw:
        raise ValidationError("local_path source requires 'path'")
    src = Path(raw).expanduser()
    if not src.exists():
        raise ValidationError(f"local_path source does not exist: {src}")
    if not src.is_dir():
        raise ValidationError(f"local_path source must be a directory: {src}")

    sync = (cfg.get("sync") or "rsync").lower()
    if sync not in {"rsync", "symlink", "copy"}:
        raise ValidationError(f"Unsupported sync mode: {sync}")

    # Empty `dest` was already wiped above.
    if sync == "symlink":
        # Remove the empty dest dir so symlink can take its place.
        try:
            dest.rmdir()
        except OSError:
            pass
        os.symlink(str(src.resolve()), str(dest))
    elif sync == "rsync" and shutil.which("rsync"):
        subprocess.run(
            ["rsync", "-a", "--delete", f"{src}/", f"{dest}/"],
            check=True,
        )
    else:
        # Fallback to copy (also handles sync == "copy").
        for child in src.iterdir():
            target = dest / child.name
            if child.is_dir():
                shutil.copytree(child, target, symlinks=True)
            else:
                shutil.copy2(child, target)
        sync = "copy" if sync != "rsync" else sync

    return {
        "fetched_from": {"type": "local_path", "path": str(src)},
        "sync_mode": sync,
    }


def _fetch_system_command(cfg: dict[str, Any], dest: Path) -> dict[str, Any]:
    """The source 'is' a system command. We verify it's present and leave a marker."""
    verify = cfg.get("verify_command")
    if not isinstance(verify, str) or not verify.strip():
        raise ValidationError("system_command source requires 'verify_command'")
    proc = subprocess.run(
        verify, shell=True, capture_output=True, text=True, timeout=30
    )
    if proc.returncode != 0:
        raise ValidationError(
            f"verify_command failed (exit={proc.returncode}): {verify}\nstderr: {proc.stderr.strip()[:200]}"
        )

    marker = dest / ".system-managed"
    marker.write_text(
        "This workspace is managed by system_command source.\n"
        f"verify_command: {verify}\n"
        f"stdout: {proc.stdout.strip()[:400]}\n",
        encoding="utf-8",
    )
    return {"fetched_from": {"type": "system_command", "verify_command": verify}}


def _fetch_docker_image_stub(cfg: dict[str, Any], dest: Path) -> dict[str, Any]:
    """Stub: full implementation arrives with SA4 (DockerRunner / Apptainer)."""
    image = cfg.get("image") or cfg.get("url")
    if not isinstance(image, str) or not image:
        raise ValidationError("docker_image source requires 'image'")
    note = dest / ".docker-image-placeholder"
    note.write_text(
        "Docker image source — full implementation pending (SA4).\n"
        f"image: {image}\n",
        encoding="utf-8",
    )
    return {
        "fetched_from": {"type": "docker_image", "image": image},
        "image": image,
        "sha256": cfg.get("sha256"),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wipe_dir(d: Path) -> None:
    if not d.exists():
        return
    for child in d.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink(missing_ok=True)
        else:
            shutil.rmtree(child, ignore_errors=True)


def _enforce_git_host(url: str) -> None:
    allowed = set(get_settings().allowed_git_host_list)
    if not allowed:
        return
    # Crude host extraction. Git URLs accept https://, ssh://, git@host:owner/repo.
    host = ""
    if "://" in url:
        host = urllib.parse.urlparse(url).hostname or ""
    elif "@" in url and ":" in url:
        host = url.split("@", 1)[1].split(":", 1)[0]
    host = host.lower()
    if host and host not in allowed:
        raise ValidationError(
            f"git host {host!r} is not in ALLOWED_GIT_HOSTS ({sorted(allowed)})"
        )


def _resolve_auth(auth_cfg: Any) -> httpx.Auth | None:
    if not isinstance(auth_cfg, dict):
        return None
    atype = (auth_cfg.get("type") or "none").lower()
    if atype == "none":
        return None
    secret_key = auth_cfg.get("secret_key")
    if not secret_key:
        return None
    secret = _read_secret(secret_key)
    if secret is None:
        logger.warning("auth secret_key=%s not found; proceeding without auth", secret_key)
        return None
    if atype == "basic":
        # secret can be "user:pass" or {"username":..,"password":..}
        if isinstance(secret, dict):
            return httpx.BasicAuth(secret.get("username", ""), secret.get("password", ""))
        if ":" in str(secret):
            user, password = str(secret).split(":", 1)
            return httpx.BasicAuth(user, password)
        return None
    if atype == "token":
        # Token-style: send as Authorization header via custom Auth.
        token = secret if isinstance(secret, str) else secret.get("token", "")
        return _BearerTokenAuth(str(token))
    return None


class _BearerTokenAuth(httpx.Auth):
    def __init__(self, token: str) -> None:
        self._token = token

    def auth_flow(self, request):  # type: ignore[override]
        request.headers["Authorization"] = f"Bearer {self._token}"
        yield request


def _read_secret(key: str) -> Any:
    """Look up a secret value from the secret_manager service if available.

    Falls back to ``None`` when the secret_manager module is not yet present
    (it lives in SA1's territory). This keeps source_fetcher self-contained.
    """
    fn = getattr(secret_manager, "get_secret", None)
    if fn is None:
        return None
    try:
        return fn(key)
    except Exception:
        logger.exception("secret_manager.get_secret(%s) failed", key)
        return None


def _extract_archive(archive: Path, dest: Path) -> None:
    """Extract zip/tar.gz/tgz/tar archives into ``dest``.

    If the archive contains a single top-level directory, its contents are
    promoted to ``dest`` root (so callers get a stable layout, similar to git clone).
    """
    name = archive.name.lower()
    extract_into = dest / "__extracted"
    extract_into.mkdir(parents=True, exist_ok=True)

    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            _safe_extract_zip(zf, extract_into)
    elif name.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tbz", ".tar.xz", ".txz", ".tar")):
        with tarfile.open(archive) as tf:
            _safe_extract_tar(tf, extract_into)
    else:
        raise ValidationError(f"Unsupported archive format: {archive.name}")

    # Promote single top-level dir.
    entries = list(extract_into.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        inner = entries[0]
        for child in inner.iterdir():
            shutil.move(str(child), str(dest / child.name))
        shutil.rmtree(extract_into, ignore_errors=True)
    else:
        for child in entries:
            shutil.move(str(child), str(dest / child.name))
        shutil.rmtree(extract_into, ignore_errors=True)


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    dest_resolved = dest.resolve()
    for member in zf.namelist():
        target = (dest / member).resolve()
        try:
            target.relative_to(dest_resolved)
        except ValueError as exc:
            raise ValidationError(f"Unsafe zip entry: {member}") from exc
    zf.extractall(dest)


def _safe_extract_tar(tf: tarfile.TarFile, dest: Path) -> None:
    dest_resolved = dest.resolve()
    for member in tf.getmembers():
        target = (dest / member.name).resolve()
        try:
            target.relative_to(dest_resolved)
        except ValueError as exc:
            raise ValidationError(f"Unsafe tar entry: {member.name}") from exc
    tf.extractall(dest)
