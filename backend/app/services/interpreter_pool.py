"""Interpreter pool — resolves Python/Node version strings to absolute binary paths.

Loads `config/interpreters.yaml` once on first access. Fallback chain for
Python (and Node when applicable):
  1. exact match (e.g. "3.11.4" -> "3.11.4")
  2. major.minor match (e.g. "3.11.4" -> "3.11")
  3. newest available within the same major series (e.g. "3.9" -> "3.12")
  4. raise RuntimeError listing what's actually available
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import yaml

from app.config import get_settings

logger = logging.getLogger(__name__)

_cache_lock = threading.Lock()
_cached_config: dict[str, dict[str, str]] | None = None
_cached_source: Path | None = None


def _resolve_config_path() -> Path:
    """Resolve the interpreters config path. Relative paths are searched at
    cwd first, then at the project root (backend/.. for `cd backend` setups)."""
    raw = get_settings().interpreters_config
    candidates = [Path(raw).expanduser()]
    if not candidates[0].is_absolute():
        # backend/app/services/interpreter_pool.py -> project root is parents[3].
        project_root = Path(__file__).resolve().parents[3]
        candidates.append((project_root / raw).resolve())
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]  # return the first (likely missing) so the loader raises


def _load_config() -> dict[str, dict[str, str]]:
    """Load the YAML once and cache. Re-load only if the configured path changes."""
    global _cached_config, _cached_source
    with _cache_lock:
        path = _resolve_config_path()
        if _cached_config is not None and _cached_source == path:
            return _cached_config

        if not path.exists():
            raise RuntimeError(f"interpreters config not found: {path}")

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise RuntimeError(f"interpreters config malformed (expect dict): {path}")

        # Normalize: ensure all version keys are strings, paths are strings.
        normalized: dict[str, dict[str, str]] = {}
        for lang, versions in raw.items():
            if not isinstance(versions, dict):
                continue
            normalized[str(lang)] = {str(v): str(p) for v, p in versions.items()}

        _cached_config = normalized
        _cached_source = path
        return normalized


def _split_version(v: str) -> tuple[int, ...]:
    """Best-effort numeric split of a version string. Non-numeric parts -> 0."""
    parts: list[int] = []
    for part in v.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _resolve(lang: str, requested: str | None) -> str:
    versions = _load_config().get(lang, {})
    if not versions:
        raise RuntimeError(f"no {lang} interpreters configured")

    available = sorted(versions.keys(), key=_split_version)

    # No version requested -> newest available.
    if requested is None or requested == "":
        chosen = available[-1]
        logger.warning("%s version not specified, using newest available %s", lang, chosen)
        return versions[chosen]

    # 1) exact match
    if requested in versions:
        return versions[requested]

    req_parts = _split_version(requested)

    # 2) major.minor match
    if len(req_parts) >= 2:
        target = f"{req_parts[0]}.{req_parts[1]}"
        if target in versions:
            logger.warning(
                "%s exact version %s not configured, falling back to %s",
                lang, requested, target,
            )
            return versions[target]

    # 3) newest within same major
    same_major = [v for v in available if _split_version(v)[:1] == req_parts[:1]]
    if same_major:
        chosen = same_major[-1]
        logger.warning(
            "%s version %s not configured, falling back to %s (same major series)",
            lang, requested, chosen,
        )
        return versions[chosen]

    raise RuntimeError(
        f"no {lang} interpreter available for '{requested}'. "
        f"configured: {', '.join(available)}"
    )


def python_for(version: str | None) -> str:
    """Return the binary path for the requested Python version (with fallback)."""
    return _resolve("python", version)


def node_for(version: str | None) -> str:
    """Return the binary path for the requested Node version (with fallback)."""
    return _resolve("node", version)


def available_pythons() -> list[str]:
    """Sorted list of configured Python version strings."""
    versions = _load_config().get("python", {})
    return sorted(versions.keys(), key=_split_version)


def available_nodes() -> list[str]:
    """Sorted list of configured Node version strings."""
    versions = _load_config().get("node", {})
    return sorted(versions.keys(), key=_split_version)


def reload_config() -> None:
    """Drop the cache so the next call reloads from disk (useful in tests)."""
    global _cached_config, _cached_source
    with _cache_lock:
        _cached_config = None
        _cached_source = None
