"""Per-demo SIF builder.

Takes a slug, its manifest, and a ``fetch_result`` (the dict returned by
:func:`app.services.source_fetcher.fetch_source`) and produces a SIF at
``var/sifs/<slug>.sif``.

Decision tree
-------------
1. Resolve ``stack`` from ``manifest.build.stack``.
2. Look up ``sif_templates/<stack>.def``. If missing →
   :class:`SifBuildResult` with ``action="skipped"`` and a clear error
   string (e.g. ``external_link`` or ``r_script`` legitimately have no SIF
   template — caller decides whether that's fatal).
3. Render the template by substituting these placeholders:
       {{UPSTREAM_DIR}}  absolute path to the fetched workspace
       {{SUBPATH}}       manifest.source.subpath or ""
       {{ENTRYPOINT}}    manifest.launch.command or the stack's entrypoint
       {{COMMIT}}        fetch_result.get("commit_sha"|"sha256") or "unknown"
       {{SLUG}}          the integration slug
4. Compute ``build_hash = sha256(commit + manifest_json + template_bytes)``.
   If ``var/sifs/<slug>.sif`` exists *and* the sentinel
   ``var/sifs/<slug>.sif.hash`` matches → ``action="skipped"`` (cached).
5. Otherwise call :func:`app.services.apt_runner.run_build` with
   ``stdout``/``stderr`` piped into ``var/logs/sif_build_<slug>.log``. On
   non-zero exit → ``action="failed"`` carrying the last 4 KiB of the log.
6. On success → write the sentinel, return ``action="built"``.

The module never raises; the caller (scanner / Celery task) gets a
structured result it can persist or surface in the operator UI.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.core.logger import get_logger
from app.services import apt_runner

logger = get_logger(__name__)


# Project root = three levels up from backend/app/services/.
_REPO_ROOT: Path = Path(__file__).resolve().parents[3]

# Public so tests + callers can introspect.
TEMPLATES_DIR: Path = Path(__file__).parent / "sif_templates"
SIF_DIR: Path = _REPO_ROOT / "var" / "sifs"
LOG_DIR: Path = _REPO_ROOT / "var" / "logs"

# Base image map: docker ref → local base SIF filename. See config/base_images.yaml.
_BASE_IMAGE_MAP_PATH: Path = _REPO_ROOT / "config" / "base_images.yaml"


def base_image_dir() -> Path:
    """Directory holding local base SIFs (base_<key>.sif).

    $HEAXHUB_BASE_IMAGE_DIR → ~/serviceApptainers (where start.sh keeps the
    service SIFs). Kept identical to deploy/apptainer/pull-base-images.sh.
    """
    val = os.environ.get("HEAXHUB_BASE_IMAGE_DIR")
    if val:
        return Path(val).expanduser()
    return Path.home() / "serviceApptainers"


@functools.lru_cache(maxsize=1)
def _base_image_map() -> dict[str, str]:
    """Load docker-ref → local-SIF-filename map. Empty on any error (feature off)."""
    try:
        data = yaml.safe_load(_BASE_IMAGE_MAP_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if v}


def _localize_base_images(rendered: str) -> str:
    """Rewrite ``Bootstrap: docker`` / ``From: <ref>`` pairs to a local base SIF
    when one exists, so app builds don't depend on Docker Hub for the base layer.

    Untouched (docker:// fallback) when the map is empty or the local SIF is
    absent. Handles multi-stage defs (e.g. go_service) by scanning every pair.
    """
    mapping = _base_image_map()
    if not mapping:
        return rendered
    base_dir = base_image_dir()
    lines = rendered.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if line.strip() == "Bootstrap: docker" and i + 1 < n:
            m = re.match(r"\s*From:\s*(\S+)\s*$", lines[i + 1])
            if m:
                ref = m.group(1)
                sif = mapping.get(ref)
                if sif and (base_dir / sif).is_file():
                    out.append("Bootstrap: localimage")
                    out.append(f"From: {base_dir / sif}")
                    logger.info("base image localized: %s → %s", ref, base_dir / sif)
                    i += 2
                    continue
        out.append(line)
        i += 1
    result = "\n".join(out)
    return result + "\n" if rendered.endswith("\n") else result

# How many trailing bytes of the build log to surface in `error`.
_ERROR_TAIL_BYTES = 4096


@dataclass(slots=True)
class SifBuildResult:
    """Outcome of :func:`build_sif`.

    ``action`` is one of:
      - ``"built"``    SIF freshly produced.
      - ``"skipped"``  No template for this stack, *or* SIF exists and the
                       hash sentinel matches (cache hit).
      - ``"failed"``   apptainer build returned non-zero / template missing
                       placeholders / OS error.

    ``sif`` is the absolute path to the SIF when relevant (``built`` or
    cache-hit ``skipped``); ``None`` otherwise. ``hash`` is the cache key
    that was computed (also ``None`` when we never got far enough to compute
    it, e.g. no template).
    """

    action: str
    sif: Path | None
    hash: str | None
    error: str | None = None
    # Absolute path to the build log on disk. Set whenever a build was
    # attempted (built/failed); None on cache-hit skip or no-template skip.
    log_path: Path | None = None
    # Stable upstream identity (git commit sha / archive digest) that this SIF
    # was built from. Lets the caller persist AppVersion.git_commit_hash.
    commit: str | None = None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_sif(
    slug: str,
    manifest: dict[str, Any],
    fetch_result: dict[str, Any],
) -> SifBuildResult:
    """Build (or cache-hit) the SIF for one integration. Never raises."""
    SIF_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    stack = _stack_name(manifest)
    commit_for_meta = _commit_identity(fetch_result)
    template_path = TEMPLATES_DIR / f"{stack}.def"
    if not template_path.is_file():
        return SifBuildResult(
            action="skipped",
            sif=None,
            hash=None,
            error=f"no SIF template for stack {stack}",
            commit=commit_for_meta,
        )

    # Read template *bytes* — that's what we hash, and that's what we render
    # against. Decode to str only for the placeholder substitution step.
    template_bytes = template_path.read_bytes()

    commit = commit_for_meta
    upstream_dir = _upstream_dir(fetch_result, slug)
    subpath = ((manifest.get("source") or {}).get("subpath")) or ""
    # STK-06: when the manifest pins a monorepo sub-directory, build that
    # subtree (not the whole repo). Resolve UPSTREAM_DIR to <upstream>/<subpath>
    # so the .def's `%files {{UPSTREAM_DIR}} /app` copies only the relevant app.
    # ``..``/leading-slash are already stripped by managed_workspaces.upstream_dir.
    if subpath:
        sub_dir = upstream_dir / subpath.strip().lstrip("/")
        if sub_dir.is_dir():
            upstream_dir = sub_dir
        else:
            logger.warning(
                "STK-06: source.subpath '%s' not found under %s for %s; "
                "building the repo root instead",
                subpath, upstream_dir, slug,
            )
    entrypoint = _entrypoint(manifest)

    placeholders = {
        "{{UPSTREAM_DIR}}": str(upstream_dir),
        "{{SUBPATH}}": str(subpath),
        "{{ENTRYPOINT}}": str(entrypoint),
        "{{COMMIT}}": str(commit),
        "{{SLUG}}": str(slug),
    }

    rendered = template_bytes.decode("utf-8")
    for key, value in placeholders.items():
        rendered = rendered.replace(key, value)
    # Prefer a local base SIF over docker:// when one is staged (resilience to
    # Docker Hub being unavailable). No-op when no local base SIF exists.
    rendered = _localize_base_images(rendered)

    # Cache key.
    build_hash = _hash_inputs(commit, manifest, template_bytes)

    sif_path = SIF_DIR / f"{slug}.sif"
    sentinel = SIF_DIR / f"{slug}.sif.hash"

    if sif_path.exists() and sentinel.exists():
        try:
            if sentinel.read_text(encoding="utf-8").strip() == build_hash:
                logger.debug("sif_builder: cache hit slug=%s hash=%s", slug, build_hash)
                return SifBuildResult(
                    action="skipped",
                    sif=sif_path,
                    hash=build_hash,
                    error=None,
                    commit=commit,
                )
        except OSError:
            # Sentinel unreadable → treat as miss and rebuild.
            pass

    # Persist the rendered .def next to the SIF for operator debugging.
    def_path = SIF_DIR / f"{slug}.def"
    log_path = LOG_DIR / f"sif_build_{slug}.log"
    try:
        def_path.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        return SifBuildResult(
            action="failed",
            sif=None,
            hash=build_hash,
            error=f"failed to write {def_path}: {exc}",
            log_path=log_path,
            commit=commit,
        )

    # Build into a temporary path so a failed/aborted build never clobbers the
    # last-good SIF that is potentially serving live traffic. We os.replace()
    # onto the final path only after apptainer exits 0 — an atomic swap on the
    # same filesystem. force=False because the temp path is always fresh.
    building_path = SIF_DIR / f"{slug}.sif.building"
    try:
        if building_path.exists():
            building_path.unlink()
    except OSError:
        pass

    try:
        with log_path.open("ab") as log_fh:
            log_fh.write(
                f"\n--- sif_build {slug} hash={build_hash} ---\n".encode("utf-8")
            )
            log_fh.flush()
            apt_runner.run_build(
                sif_out=building_path,
                def_in=def_path,
                fakeroot=True,
                force=False,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                check=True,
            )
    except subprocess.CalledProcessError as exc:
        _discard(building_path)
        tail = _tail_text(log_path, _ERROR_TAIL_BYTES)
        return SifBuildResult(
            action="failed",
            sif=None,
            hash=build_hash,
            error=(
                f"apptainer build exit={exc.returncode}\n"
                f"--- tail ({log_path.name}) ---\n{tail}"
            ),
            log_path=log_path,
            commit=commit,
        )
    except FileNotFoundError as exc:
        # apt_runner.local_apptainer_path() raises this when no apptainer
        # is installed anywhere; surface the original message verbatim.
        _discard(building_path)
        return SifBuildResult(
            action="failed",
            sif=None,
            hash=build_hash,
            error=str(exc),
            log_path=log_path,
            commit=commit,
        )
    except OSError as exc:  # pragma: no cover - defensive
        _discard(building_path)
        return SifBuildResult(
            action="failed",
            sif=None,
            hash=build_hash,
            error=f"OS error during build: {exc}",
            log_path=log_path,
            commit=commit,
        )

    # Atomic swap: move the freshly-built temp SIF onto the final path. Only
    # now is the old image replaced, so a crash mid-build leaves it intact.
    try:
        os.replace(building_path, sif_path)
    except OSError as exc:  # pragma: no cover - defensive
        _discard(building_path)
        return SifBuildResult(
            action="failed",
            sif=None,
            hash=build_hash,
            error=f"built {building_path.name} but failed to swap into place: {exc}",
            log_path=log_path,
            commit=commit,
        )

    # Success → write the sentinel atomically.
    try:
        sentinel.write_text(build_hash + "\n", encoding="utf-8")
    except OSError as exc:  # pragma: no cover - defensive
        return SifBuildResult(
            action="failed",
            sif=sif_path,
            hash=build_hash,
            error=f"built {sif_path.name} but failed to write sentinel: {exc}",
            log_path=log_path,
            commit=commit,
        )

    return SifBuildResult(
        action="built",
        sif=sif_path,
        hash=build_hash,
        error=None,
        log_path=log_path,
        commit=commit,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stack_name(manifest: dict[str, Any]) -> str:
    build_section = manifest.get("build") or {}
    stack = build_section.get("stack") or build_section.get("type")
    return str(stack or "unknown")


def _fr_to_dict(fr: Any) -> dict[str, Any]:
    """Accept either a dict-style result (legacy source_fetcher) or our
    integration_fetcher.FetchResult dataclass. Returns a plain dict."""
    if isinstance(fr, dict):
        return fr
    # dataclass: pull standard fields
    return {
        "commit_sha": getattr(fr, "commit", None) or getattr(fr, "commit_sha", None),
        "action": getattr(fr, "action", None),
        "error": getattr(fr, "error", None),
    }


def _commit_identity(fetch_result: Any) -> str:
    """Pick the best stable identity from a fetch_result."""
    d = _fr_to_dict(fetch_result)
    # source_fetcher returns commit_sha for git, sha256 for archive_url, etc.
    for key in ("commit_sha", "sha256", "image", "path"):
        value = d.get(key)
        if isinstance(value, str) and value:
            return value
    return "unknown"


def _upstream_dir(fetch_result: Any, slug: str) -> Path:
    """Where the fetched source lives. Falls back to the managed workspace."""
    d = _fr_to_dict(fetch_result)
    path_str = d.get("workspace") or d.get("dest")
    if isinstance(path_str, str) and path_str:
        return Path(path_str)
    return _REPO_ROOT / "var" / "integration_workspaces" / slug / "upstream"


def _entrypoint(manifest: dict[str, Any]) -> str:
    launch = manifest.get("launch") or {}
    cmd = launch.get("command") or launch.get("entrypoint")
    if isinstance(cmd, str) and cmd:
        return cmd
    build_section = manifest.get("build") or {}
    cmd = build_section.get("entrypoint")
    if isinstance(cmd, str) and cmd:
        return cmd
    return "./.portal/run.sh"


def _hash_inputs(commit: str, manifest: dict[str, Any], template_bytes: bytes) -> str:
    h = hashlib.sha256()
    h.update(commit.encode("utf-8"))
    h.update(b"\0")
    # Stable JSON so reordering keys never invalidates the cache.
    h.update(json.dumps(manifest, sort_keys=True, default=str).encode("utf-8"))
    h.update(b"\0")
    h.update(template_bytes)
    return h.hexdigest()


def _discard(path: Path) -> None:
    """Best-effort delete of a partial/temp SIF. Never raises."""
    try:
        if path.exists():
            path.unlink()
    except OSError:  # pragma: no cover - defensive
        logger.warning("sif_builder: could not remove temp file %s", path)


def _tail_text(path: Path, max_bytes: int) -> str:
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            data = fh.read()
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""
