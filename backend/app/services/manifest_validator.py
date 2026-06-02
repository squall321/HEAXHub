"""Manifest validator backed by manifest.schema.json (v1) and manifest.schema.v2.json (v2)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft7Validator

# Project layout: backend/app/services/... → repo root is parents[3]
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_PATHS: dict[int, Path] = {
    1: _REPO_ROOT / "schemas" / "manifest.schema.json",
    2: _REPO_ROOT / "schemas" / "manifest.schema.v2.json",
}


@lru_cache
def _get_validator(version: int) -> Draft7Validator:
    schema_path = _SCHEMA_PATHS.get(version)
    if schema_path is None:
        raise ValueError(f"Unsupported manifest schema_version: {version}")
    with schema_path.open(encoding="utf-8") as f:
        schema = json.load(f)
    return Draft7Validator(schema)


def _resolve_version(manifest: dict[str, Any]) -> int:
    raw = manifest.get("schema_version", 1)
    try:
        version = int(raw)
    except (TypeError, ValueError):
        return 1
    if version not in _SCHEMA_PATHS:
        return version  # let validator return a clear error
    return version


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    """Return list of human-readable errors. Empty list ⇒ valid.

    Dispatch by `schema_version` (defaults to 1 when missing).
    """
    version = _resolve_version(manifest)
    if version not in _SCHEMA_PATHS:
        return [f"<root>: unsupported schema_version {version} (supported: {sorted(_SCHEMA_PATHS)})"]
    validator = _get_validator(version)
    errors: list[str] = []
    for err in sorted(validator.iter_errors(manifest), key=lambda e: list(e.absolute_path)):
        loc = ".".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{loc}: {err.message}")
    return errors


def load_manifest_file(path: Path) -> dict[str, Any]:
    """Load a manifest from a YAML or JSON file. Does not validate."""
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".yaml", ".yml"}:
        loaded = yaml.safe_load(text)
    else:
        loaded = json.loads(text)
    if not isinstance(loaded, dict):
        raise ValueError(f"Manifest at {path} must be a mapping")
    return loaded
