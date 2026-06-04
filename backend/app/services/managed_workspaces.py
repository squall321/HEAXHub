"""Path helpers for HEAXHub-managed per-integration workspaces and SIFs.

This module owns the directory layout under ``var/`` for the per-demo SIF
pipeline. Nothing here touches the filesystem destructively — callers ask
for a :class:`Path` and may create / mkdir / write files as needed. All
paths are absolute and rooted at the repo root (three levels up from
``backend/app/services/``).

Layout::

    var/
      integration_workspaces/
        <slug>/
          upstream/             # source_fetcher.fetch_source destination
                                # (or upstream/<subpath> when manifest.source.subpath is set)
      sifs/
        <slug>.sif              # per-demo built image
      logs/
        build_<slug>.log        # apptainer build stdout/stderr

The whole tree is gitignored — only ``integrations/<slug>/.portal/`` is
committed to the repo.
"""
from __future__ import annotations

from pathlib import Path

# Project root: three levels up from backend/app/services/. Same convention
# as integrations_scanner / integration_launcher.
_REPO_ROOT: Path = Path(__file__).resolve().parents[3]

# Public roots ---------------------------------------------------------------
MANAGED_ROOT: Path = _REPO_ROOT / "var" / "integration_workspaces"
SIF_OUT_DIR: Path = _REPO_ROOT / "var" / "sifs"
LOG_DIR: Path = _REPO_ROOT / "var" / "logs"


def workspace_for(slug: str) -> Path:
    """Return ``var/integration_workspaces/<slug>/`` (created if missing).

    The slug is taken as-is from the integrations directory name; it's
    already constrained to a filesystem-safe identifier upstream.
    """
    if not slug or "/" in slug or slug in {".", ".."}:
        raise ValueError(f"invalid slug: {slug!r}")
    out = MANAGED_ROOT / slug
    out.mkdir(parents=True, exist_ok=True)
    return out


def sif_path_for(slug: str) -> Path:
    """Return the absolute path of the per-demo SIF (``var/sifs/<slug>.sif``).

    The file may or may not exist; callers check ``.exists()``. The parent
    directory IS created so a subsequent ``apptainer build`` doesn't fail
    on a missing destination.
    """
    if not slug or "/" in slug or slug in {".", ".."}:
        raise ValueError(f"invalid slug: {slug!r}")
    SIF_OUT_DIR.mkdir(parents=True, exist_ok=True)
    return SIF_OUT_DIR / f"{slug}.sif"


def upstream_dir(slug: str, subpath: str = "") -> Path:
    """Return ``<workspace>/upstream[/<subpath>]`` (workspace created).

    ``subpath`` lets a manifest pin a sub-directory of the fetched source
    as the effective workspace root (e.g. when the repo contains
    ``apps/streamlit-demo/`` and only that subtree is relevant).
    Leading slashes and ``..`` segments are stripped to keep the result
    inside the workspace tree.
    """
    base = workspace_for(slug) / "upstream"
    base.mkdir(parents=True, exist_ok=True)
    sp = (subpath or "").strip().lstrip("/")
    if not sp:
        return base
    # Reject path traversal — keep the result inside `base`.
    parts: list[str] = []
    for seg in sp.split("/"):
        if seg in {"", "."}:
            continue
        if seg == "..":
            raise ValueError(f"subpath escapes workspace: {subpath!r}")
        parts.append(seg)
    return base.joinpath(*parts) if parts else base


def build_log_path(slug: str) -> Path:
    """Return ``var/logs/build_<slug>.log`` (parent dir created)."""
    if not slug or "/" in slug or slug in {".", ".."}:
        raise ValueError(f"invalid slug: {slug!r}")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR / f"build_{slug}.log"
