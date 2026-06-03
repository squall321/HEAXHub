"""Stack resolver — loads ``config/stacks.yaml`` and exposes one StackSpec per stack.

The fixed catalogue of stacks HEAXHub knows how to build & run lives in
``config/stacks.yaml`` at the repo root. Every integration manifest declares
``build.stack: <name>``; this module resolves that name to a
:class:`StackSpec` so callers (scanner, app_lifecycle, service_manager) can
make routing decisions without re-parsing YAML.

Intentionally tiny: load YAML, cache it, return typed records.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.errors import NotFoundError, ValidationError
from app.core.logger import get_logger

logger = get_logger(__name__)


# Project root is two levels up from backend/app/services/. The default
# stacks.yaml sits at <repo>/config/stacks.yaml. We resolve against the repo
# root rather than CWD so the file is found regardless of where uvicorn/pytest
# was invoked from.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_STACKS_PATH = _REPO_ROOT / "config" / "stacks.yaml"


@dataclass(slots=True)
class StackSpec:
    """Typed record for one entry under ``stacks:`` in ``config/stacks.yaml``.

    Only the fields the dispatcher actually needs are first-class — anything
    else stays in :attr:`extra` so we don't churn this dataclass each time
    ``stacks.yaml`` grows a new key.
    """

    name: str
    label: str
    app_type: str
    execution_target: str
    launch_mode: str  # job_runner | service | installer
    builder: str
    runtime: str
    install: str | None = None
    build: str | None = None
    entrypoint: str | None = None
    health_path: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _stacks_path() -> Path:
    """Resolve the stacks YAML location.

    We do not put this in Settings yet — there is exactly one file and its
    location has never changed. If/when operators need to override it, add a
    ``stacks_config`` Setting and read it here.
    """
    return _DEFAULT_STACKS_PATH


@lru_cache(maxsize=1)
def _load(path_str: str) -> dict[str, StackSpec]:
    """Parse ``stacks.yaml`` once and return ``{name: StackSpec}``."""
    path = Path(path_str)
    if not path.exists():
        raise NotFoundError(f"stacks config not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValidationError(f"stacks config malformed YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValidationError(
            f"stacks config must be a mapping; got {type(raw).__name__}"
        )

    stacks_section = raw.get("stacks") or {}
    if not isinstance(stacks_section, dict):
        raise ValidationError(
            "stacks config: top-level 'stacks' must be a mapping"
        )

    out: dict[str, StackSpec] = {}
    for name, entry in stacks_section.items():
        if not isinstance(entry, dict):
            logger.warning("skipping non-dict stack entry %r", name)
            continue
        try:
            out[str(name)] = StackSpec(
                name=str(name),
                label=str(entry.get("label", name)),
                app_type=str(entry.get("app_type", "cli_tool")),
                execution_target=str(entry.get("execution_target", "linux_runner")),
                launch_mode=str(entry.get("launch_mode", "job_runner")),
                builder=str(entry.get("builder", "python_venv")),
                runtime=str(entry.get("runtime", "python_venv")),
                install=entry.get("install"),
                build=entry.get("build"),
                entrypoint=entry.get("entrypoint"),
                health_path=entry.get("health_path"),
                extra={
                    k: v
                    for k, v in entry.items()
                    if k
                    not in {
                        "label",
                        "app_type",
                        "execution_target",
                        "launch_mode",
                        "builder",
                        "runtime",
                        "install",
                        "build",
                        "entrypoint",
                        "health_path",
                    }
                },
            )
        except Exception:  # noqa: BLE001 — defensive, never break boot on a bad entry
            logger.exception("failed to parse stack entry %r", name)
    return out


def reload_stacks() -> None:
    """Invalidate the cache. Used by tests; operators normally just restart."""
    _load.cache_clear()


def load_stacks() -> dict[str, StackSpec]:
    """Return all configured stacks as ``{name: StackSpec}``."""
    return _load(str(_stacks_path()))


def resolve(stack_name: str) -> StackSpec:
    """Return the StackSpec for ``stack_name`` or raise NotFoundError."""
    stacks = load_stacks()
    spec = stacks.get(stack_name)
    if spec is None:
        known = ", ".join(sorted(stacks.keys())) or "<empty>"
        raise NotFoundError(
            f"unknown stack '{stack_name}'. Known: {known}"
        )
    return spec
