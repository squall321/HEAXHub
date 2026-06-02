"""SIF registry — logical-name -> absolute SIF path lookup.

The production target is offline; we never `apptainer pull`. Instead, an
operator places SIFs on disk and lists them in ``config/sif_registry.yaml``.
A manifest author then references the logical name in their
``launch.image_ref``::

    launch:
      image_ref:
        type: registry
        name: lsdyna_smp_s

This module is intentionally tiny: load YAML, cache it, resolve a name to an
absolute :class:`Path`, validate file existence at resolve time (not load time)
so a partial bundle can still run jobs whose SIFs are present.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.config import get_settings
from app.core.errors import NotFoundError, ValidationError
from app.core.logger import get_logger

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def _load_registry(registry_path: str) -> dict[str, str]:
    """Load the YAML registry, returning ``{logical_name: absolute_path}``.

    Cached by path so repeated lookups are O(1). Use :func:`reload_registry`
    in tests or after operators edit the file at runtime.
    """
    path = Path(registry_path)
    if not path.exists():
        raise NotFoundError(f"SIF registry not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValidationError(f"SIF registry malformed YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValidationError(
            f"SIF registry must be a mapping; got {type(raw).__name__}"
        )
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            logger.warning(
                "skipping non-string entry in SIF registry: %r=%r", k, v
            )
            continue
        out[k] = v
    return out


def reload_registry() -> None:
    """Invalidate the cached registry. Call after editing the YAML."""
    _load_registry.cache_clear()


def _registry_path() -> str:
    settings = get_settings()
    return str(settings.sif_registry_path)


def resolve_sif(image_ref: dict[str, Any]) -> Path:
    """Resolve an ``image_ref`` dict from a manifest to an absolute SIF path.

    Supported ``type`` values:

    - ``local_path``  — ``{"type": "local_path", "path": "/abs/path.sif"}``
    - ``registry``    — ``{"type": "registry", "name": "lsdyna_smp_s"}``

    Raises :class:`NotFoundError` when the registry entry is missing or the
    backing file does not exist. Raises :class:`NotImplementedError` for known
    but unsupported types (e.g. ``minio``).
    """
    if not isinstance(image_ref, dict):
        raise ValidationError(
            f"image_ref must be a dict; got {type(image_ref).__name__}"
        )
    ref_type = str(image_ref.get("type") or "").lower()

    if ref_type == "local_path":
        raw = image_ref.get("path")
        if not raw:
            raise NotFoundError("image_ref.local_path missing 'path' field")
        path = Path(str(raw)).expanduser()
        if not path.exists():
            raise NotFoundError(f"SIF (local_path) not found: {path}")
        return path

    if ref_type == "registry":
        name = image_ref.get("name")
        if not name or not isinstance(name, str):
            raise NotFoundError("image_ref.registry missing 'name' field")
        registry = _load_registry(_registry_path())
        raw = registry.get(name)
        if raw is None:
            known = ", ".join(sorted(registry.keys())) or "<empty>"
            raise NotFoundError(
                f"SIF registry has no entry for '{name}'. Known: {known}"
            )
        path = Path(raw).expanduser()
        if not path.exists():
            raise NotFoundError(
                f"SIF registry entry '{name}' -> {path} does not exist on disk"
            )
        return path

    if ref_type == "minio":
        raise NotImplementedError(
            "image_ref.type='minio' is not yet implemented; ship SIFs offline "
            "and reference them via type=registry instead"
        )

    raise NotFoundError(
        f"image_ref.type='{ref_type}' not supported "
        "(expected local_path | registry | minio)"
    )
