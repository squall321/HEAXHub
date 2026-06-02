"""Integration repo workspaces.

Maintains a local clone of each repo listed in INTEGRATION_REPO_URLS under
``app_workspaces/_integrations/{slug}/upstream/``. Operators occasionally need
to rebuild or repackage the upstream code (e.g. produce a web-app deployable)
without touching the upstream repository itself — so we keep an up-to-date,
read-mostly clone sitting on disk.

Lifecycle:
- On app startup, ``ensure_all_cloned()`` walks the configured URLs and
  performs ``git clone`` for any that aren't present yet. It does NOT pull
  updates unless explicitly asked.
- Operator can trigger ``sync_one(url)`` / ``sync_all()`` from
  ``/admin/integrations/sync`` to git-pull the latest commit.
- This is a best-effort utility — failures are logged but do not block boot.

NOTE: Cloning is intentionally synchronous and serial here. We're targeting
small numbers (typically 1-5 integration repos). For large fleets, move this
into a Celery task.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.core.logger import get_logger

logger = get_logger(__name__)


# Directory bucket under workspace_root that holds integration clones.
_BUCKET = "_integrations"

# Slug regex — allows characters that survive in filesystem.
_SLUG_OK = re.compile(r"[^a-z0-9_.-]")


@dataclass
class IntegrationWorkspace:
    repo_url: str
    slug: str
    path: Path          # app_workspaces/_integrations/{slug}
    upstream: Path      # .../{slug}/upstream
    cloned: bool
    commit_sha: str | None
    last_sync_at: datetime | None
    error: str | None = None


def _slug_for(url: str) -> str:
    """Derive a filesystem-safe slug from a repo URL.

    Strategy: last 2 path segments joined with '__', lowercased,
    non-[a-z0-9_.-] replaced with '-'. Length capped at 80.
    """
    # Strip protocol + trailing .git
    s = url.strip()
    s = re.sub(r"^[a-zA-Z]+://", "", s)
    s = re.sub(r"^git@[^:]+:", "", s)
    if s.endswith(".git"):
        s = s[:-4]
    parts = [p for p in s.split("/") if p]
    tail = parts[-2:] if len(parts) >= 2 else parts
    slug = "__".join(tail).lower()
    slug = _SLUG_OK.sub("-", slug)
    return slug[:80] or "repo"


def _root() -> Path:
    return get_settings().workspace_root / _BUCKET


def workspace_for(repo_url: str) -> Path:
    """Return the directory where the integration clone lives (does not create)."""
    return _root() / _slug_for(repo_url)


def status_one(repo_url: str) -> IntegrationWorkspace:
    """Inspect the local clone without modifying anything."""
    slug = _slug_for(repo_url)
    path = _root() / slug
    upstream = path / "upstream"
    cloned = upstream.exists() and (upstream / ".git").exists()
    commit: str | None = None
    last_sync: datetime | None = None
    if cloned:
        # rev-parse HEAD may fail on empty repos (no commits) — this is fine.
        res = subprocess.run(
            ["git", "-C", str(upstream), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if res.returncode == 0:
            commit = res.stdout.strip() or None
        else:
            logger.debug("rev-parse HEAD empty for %s (likely empty repo)", upstream)
        try:
            mtime = (upstream / ".git" / "FETCH_HEAD").stat().st_mtime
            last_sync = datetime.fromtimestamp(mtime, tz=timezone.utc)
        except OSError:
            try:
                mtime = (upstream / ".git").stat().st_mtime
                last_sync = datetime.fromtimestamp(mtime, tz=timezone.utc)
            except OSError:
                last_sync = None
    return IntegrationWorkspace(
        repo_url=repo_url, slug=slug, path=path, upstream=upstream,
        cloned=cloned, commit_sha=commit, last_sync_at=last_sync,
    )


def ensure_cloned(repo_url: str, *, force_resync: bool = False) -> IntegrationWorkspace:
    """Ensure the repo is cloned. If already present and ``force_resync`` is True,
    runs ``git fetch + reset --hard origin/HEAD``.
    """
    info = status_one(repo_url)
    info.path.mkdir(parents=True, exist_ok=True)
    if not info.cloned:
        # First clone
        info.upstream.parent.mkdir(parents=True, exist_ok=True)
        if info.upstream.exists():
            # half-cloned remains — wipe to start fresh
            shutil.rmtree(info.upstream, ignore_errors=True)
        logger.info("git clone %s → %s", repo_url, info.upstream)
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", repo_url, str(info.upstream)],
                check=True, capture_output=True, text=True, timeout=300,
            )
        except subprocess.CalledProcessError as exc:
            info.error = (exc.stderr or "").strip() or "git clone failed"
            logger.warning("git clone failed for %s: %s", repo_url, info.error)
            return info
        except subprocess.TimeoutExpired:
            info.error = "git clone timed out (5 min)"
            return info
        return status_one(repo_url)
    if force_resync:
        logger.info("git pull %s in %s", repo_url, info.upstream)
        try:
            subprocess.run(
                ["git", "-C", str(info.upstream), "fetch", "--depth", "1", "origin"],
                check=True, capture_output=True, text=True, timeout=120,
            )
            subprocess.run(
                ["git", "-C", str(info.upstream), "reset", "--hard", "origin/HEAD"],
                check=True, capture_output=True, text=True, timeout=60,
            )
        except subprocess.CalledProcessError as exc:
            info.error = (exc.stderr or "").strip() or "git fetch/reset failed"
            logger.warning("git resync failed for %s: %s", repo_url, info.error)
            return info
        except subprocess.TimeoutExpired:
            info.error = "git resync timed out"
            return info
        return status_one(repo_url)
    return info


def ensure_all_cloned() -> list[IntegrationWorkspace]:
    """Best-effort: clone everything in INTEGRATION_REPO_URLS that's missing.

    Skips repos already cloned. Failures are recorded in the returned list,
    not raised.
    """
    settings = get_settings()
    urls = settings.integration_repo_url_list
    out: list[IntegrationWorkspace] = []
    for url in urls:
        try:
            out.append(ensure_cloned(url))
        except Exception as exc:  # noqa: BLE001 — defensive at boot
            logger.exception("ensure_cloned crashed for %s", url)
            out.append(IntegrationWorkspace(
                repo_url=url,
                slug=_slug_for(url),
                path=_root() / _slug_for(url),
                upstream=_root() / _slug_for(url) / "upstream",
                cloned=False, commit_sha=None, last_sync_at=None,
                error=str(exc),
            ))
    return out


def sync_one(repo_url: str) -> IntegrationWorkspace:
    """Operator-triggered fetch + reset."""
    return ensure_cloned(repo_url, force_resync=True)


def sync_all() -> list[IntegrationWorkspace]:
    settings = get_settings()
    return [sync_one(u) for u in settings.integration_repo_url_list]


def list_all() -> list[IntegrationWorkspace]:
    """Inspect-only — current state of every configured integration repo."""
    settings = get_settings()
    return [status_one(u) for u in settings.integration_repo_url_list]
