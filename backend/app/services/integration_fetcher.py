"""Per-integration upstream source fetcher (git-only for now).

This module owns the ``upstream/`` directory under each integration's
managed workspace. It is the bridge between a manifest ``source:`` block
(parsed into :class:`SourceSpec`) and the on-disk tree the SIF builder
will consume.

Scope (intentionally narrow):

* ``git`` source type only — ``ref`` is treated as a branch/tag/sha that
  ``git checkout`` understands.
* Operations are idempotent: re-running with an unchanged ref reports
  ``skipped``, a new ref reports ``updated``, a fresh slug reports
  ``cloned``.
* Failures are caught and returned as ``action="failed"`` with the error
  message — the upstream tree is left in whatever state git left it.

The richer ``source_fetcher`` module (archive_url / local_path / system_command
/ docker_image) is deliberately NOT used here yet; once the SIF pipeline
proves out on git we can route those types through this same surface.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.services.integrations_scanner import SourceSpec
from app.services.managed_workspaces import upstream_dir

logger = logging.getLogger(__name__)

_GIT_TIMEOUT_SECONDS = 300


@dataclass(slots=True)
class FetchResult:
    """Outcome of a single ``fetch_for_integration`` call.

    Attributes:
        action: One of ``"cloned" | "updated" | "skipped" | "failed"``.
        commit: Resolved commit sha of the upstream HEAD after the call,
            or ``None`` when the action failed before checkout succeeded.
        error: Human-readable error string when ``action == "failed"``.
    """

    action: str
    commit: str | None = None
    error: str | None = None


def fetch_for_integration(slug: str, source: SourceSpec) -> FetchResult:
    """Clone / update an integration's upstream tree per its ``SourceSpec``.

    The destination is ``var/integration_workspaces/<slug>/upstream/`` as
    returned by :func:`managed_workspaces.upstream_dir` (with ``subpath=""``
    so the full repo lands at the workspace root; the SIF builder narrows
    to ``source.subpath`` later).

    Only ``source.type == "git"`` is supported. Anything else returns
    ``action="failed"`` with a descriptive error.
    """
    if source.type != "git":
        return FetchResult(
            action="failed",
            error=f"unsupported source type: {source.type!r} (git only for now)",
        )
    if not source.url:
        return FetchResult(action="failed", error="source.url is empty")

    dest = upstream_dir(slug)  # creates parents, returns .../upstream/
    git_dir = dest / ".git"
    ref = source.ref or "main"

    try:
        if not git_dir.exists():
            return _clone(dest, source.url, ref)
        return _update(dest, ref)
    except subprocess.CalledProcessError as exc:  # pragma: no cover - exercised via tests
        msg = (exc.stderr or exc.stdout or str(exc)).strip()
        logger.warning("integration_fetcher git failed for %s: %s", slug, msg)
        return FetchResult(action="failed", error=msg)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("integration_fetcher I/O error for %s: %s", slug, exc)
        return FetchResult(action="failed", error=str(exc))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _clone(dest: Path, url: str, ref: str) -> FetchResult:
    """Fresh clone into ``dest`` then check out ``ref``.

    ``upstream/`` is created by ``upstream_dir`` and is empty here (the
    caller guaranteed ``.git`` doesn't exist). We clone into it with
    ``git clone <url> .`` and then resolve ``ref`` to a concrete commit.
    """
    _run(["git", "clone", "--no-tags", url, "."], cwd=dest)
    _run(["git", "checkout", ref], cwd=dest)
    commit = _resolve_head(dest)
    return FetchResult(action="cloned", commit=commit)


def _update(dest: Path, ref: str) -> FetchResult:
    """Fetch + decide between skipped vs updated.

    Strategy: fetch the remote, resolve ``ref`` to a concrete commit
    (preferring ``origin/<ref>`` so a moved branch tip is picked up),
    then compare to current HEAD. When they differ, hard-reset the
    working tree to the resolved sha so a local branch that has fallen
    behind ``origin/<ref>`` advances cleanly.
    """
    _run(["git", "fetch", "--no-tags", "--prune", "origin"], cwd=dest)
    current = _resolve_head(dest)
    target = _resolve_ref(dest, ref)

    if target and current and target == current:
        return FetchResult(action="skipped", commit=current)

    # Resolve-then-reset moves both detached refs and stale local branches
    # to the upstream tip; falling back to a plain checkout when we couldn't
    # resolve the ref lets git produce the canonical error message.
    if target:
        _run(["git", "reset", "--hard", target], cwd=dest)
    else:
        _run(["git", "checkout", ref], cwd=dest)
    return FetchResult(action="updated", commit=_resolve_head(dest))


def _resolve_head(cwd: Path) -> str | None:
    """Return the current ``HEAD`` commit sha, or ``None`` on error."""
    try:
        out = _run(["git", "rev-parse", "HEAD"], cwd=cwd, capture=True)
    except subprocess.CalledProcessError:
        return None
    return (out or "").strip() or None


def _resolve_ref(cwd: Path, ref: str) -> str | None:
    """Resolve ``ref`` (after fetch) to a commit sha, trying remote first.

    Order: ``origin/<ref>`` → ``<ref>``. Returns ``None`` if neither
    resolves so callers can still attempt a plain ``git checkout`` and
    let git produce the canonical error.
    """
    for candidate in (f"origin/{ref}", ref):
        try:
            out = _run(
                ["git", "rev-parse", "--verify", candidate],
                cwd=cwd,
                capture=True,
            )
        except subprocess.CalledProcessError:
            continue
        sha = (out or "").strip()
        if sha:
            return sha
    return None


def _run(
    argv: list[str],
    *,
    cwd: Path,
    capture: bool = False,
) -> str | None:
    """Run ``git`` with a fixed timeout and consistent error handling."""
    proc = subprocess.run(  # noqa: S603 - argv is constructed from validated inputs
        argv,
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    return proc.stdout if capture else None
