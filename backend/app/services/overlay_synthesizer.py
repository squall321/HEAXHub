"""Overlay manifest + run.sh synthesizer.

When a submitted upstream repo lacks ``.portal/manifest.yaml``, the build
pipeline used to silently advance to BUILT and the run step would fail
immediately ("ghost build"). This module fills the gap deterministically
using ``static_analyzer.StaticFacts``, with no LLM dependency.

Decision tree:
    upstream/.portal/manifest.yaml exists      → copy verbatim, return None
    pyproject.toml or setup.py present         → Python CLI manifest + run.sh
    package.json present                       → Node service manifest + run.sh
    none of the above                          → placeholder manifest;
                                                 mark Submission.status =
                                                 MANIFEST_REQUIRED so the
                                                 operator knows manual fix
                                                 is needed.

For Python:
    - When pyproject declares ``[project.scripts]``, run.sh calls that script.
    - Else when ``main.py`` is found at root or under common subpaths, exec it
      via ``python -m <pkg>``.
    - Else write a helpful error+exit-1 stub.

For Node:
    - When package.json declares ``scripts.start``, run.sh calls ``npm start``.
    - When scripts.build is also present, the install_command runs ``npm
      install && npm run build`` first.

All written files use Unix newlines and ``run.sh`` is chmod 0755.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.core.logger import get_logger
from app.db.models.submission import Submission, SubmissionStatus
from app.services.static_analyzer import StaticFacts

logger = get_logger(__name__)


@dataclass
class SynthesisResult:
    """Outcome of synthesize_overlay().

    Attributes:
        manifest: The manifest dict written to overlay/.portal/manifest.yaml.
                  None when the upstream manifest was copied verbatim.
        synthesized: True if HEAXHub generated the manifest, False if upstream's
                     own was used.
        flavor: One of {"upstream", "python_cli", "node_service", "placeholder"}.
        warnings: Notes that callers should log / surface to the operator.
    """
    manifest: dict[str, Any] | None
    synthesized: bool
    flavor: str
    warnings: list[str]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def synthesize_overlay(
    workspace: Path,
    sub: Submission,
    facts: StaticFacts,
) -> SynthesisResult:
    """Populate ``workspace/overlay/.portal/`` based on upstream + StaticFacts.

    Mutates ``sub.status`` to ``MANIFEST_REQUIRED`` when no signal was usable.
    The caller is responsible for committing the Submission row.
    """
    upstream_dir = workspace / "upstream"
    overlay_dir = workspace / "overlay" / ".portal"
    overlay_dir.mkdir(parents=True, exist_ok=True)

    upstream_manifest = upstream_dir / ".portal" / "manifest.yaml"
    if upstream_manifest.exists():
        target = overlay_dir / "manifest.yaml"
        shutil.copyfile(upstream_manifest, target)
        # Also copy run.sh if upstream provides one.
        upstream_run = upstream_dir / ".portal" / "run.sh"
        if upstream_run.exists():
            shutil.copyfile(upstream_run, overlay_dir / "run.sh")
            (overlay_dir / "run.sh").chmod(0o755)
        return SynthesisResult(
            manifest=None, synthesized=False, flavor="upstream", warnings=[]
        )

    warnings: list[str] = []

    is_python = (
        (upstream_dir / "pyproject.toml").exists()
        or (upstream_dir / "setup.py").exists()
    )
    has_package_json = (upstream_dir / "package.json").exists()

    if is_python:
        manifest, run_sh = _python_cli(sub, facts, upstream_dir, warnings)
        flavor = "python_cli"
    elif has_package_json:
        manifest, run_sh = _node_service(sub, facts, upstream_dir, warnings)
        flavor = "node_service"
    else:
        manifest = _placeholder(sub)
        run_sh = None
        flavor = "placeholder"
        sub.status = SubmissionStatus.MANIFEST_REQUIRED
        warnings.append(
            "Upstream has no .portal/manifest.yaml, pyproject.toml, setup.py, "
            "or package.json. Submission flagged MANIFEST_REQUIRED so the "
            "operator can review."
        )

    (overlay_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    if run_sh is not None:
        (overlay_dir / "run.sh").write_text(run_sh, encoding="utf-8")
        (overlay_dir / "run.sh").chmod(0o755)

    if warnings:
        logger.warning(
            "overlay synthesized for %s (%s): %s",
            sub.proposed_app_id, flavor, "; ".join(warnings),
        )

    return SynthesisResult(
        manifest=manifest, synthesized=True, flavor=flavor, warnings=warnings
    )


# ---------------------------------------------------------------------------
# Python CLI template
# ---------------------------------------------------------------------------


def _python_cli(
    sub: Submission, facts: StaticFacts, upstream: Path, warnings: list[str]
) -> tuple[dict[str, Any], str]:
    python_version = facts.python_version or "3.11"
    entry = _python_entrypoint(upstream, warnings)
    manifest = {
        "schema_version": 2,
        "id": sub.proposed_app_id,
        "name": sub.name,
        "version": "0.1.0",
        "owner": str(sub.submitter_user_id),
        "status": "draft",
        "app_type": sub.proposed_app_type or "cli_tool",
        "execution_target": sub.proposed_execution_target or "linux_runner",
        "description": (sub.description or "Auto-synthesized by HEAXHub from "
                        "pyproject.toml. Review before publishing."),
        "tags": ["auto-synthesized", "python"],
        "build": {
            "stack": "python_cli",
            "type": "python_venv",
            "python_version": python_version,
        },
        "launch": {
            "mode": "job_runner",
            "command": "./.portal/run.sh",
            "runtime": "python_venv",
        },
        "inputs": [],
        "outputs": [],
        "permissions": {"visibility": "team"},
        "resources": {"cpu": 1, "memory_gb": 1, "gpu": False},
        "requirements": {"os": "linux"},
    }
    run_sh = (
        '#!/usr/bin/env bash\n'
        '# Auto-synthesized by HEAXHub overlay_synthesizer.\n'
        'set -euo pipefail\n'
        'INPUT_DIR="${1:-./input}"\n'
        'OUTPUT_DIR="${2:-./output}"\n'
        'PARAMS_JSON="${3:-./params.json}"\n'
        'mkdir -p "$OUTPUT_DIR"\n'
        'cd "$(dirname "$0")/../upstream"\n'
        f'{entry}\n'
    )
    return manifest, run_sh


def _python_entrypoint(upstream: Path, warnings: list[str]) -> str:
    """Decide how to invoke the upstream Python project."""
    pyproject = upstream / "pyproject.toml"
    if pyproject.exists():
        try:
            try:
                import tomllib  # py3.11+
            except ImportError:  # pragma: no cover
                import tomli as tomllib  # type: ignore[no-redef]
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            scripts = (
                data.get("project", {}).get("scripts")
                or data.get("tool", {}).get("poetry", {}).get("scripts")
                or {}
            )
            if scripts:
                name = next(iter(scripts.keys()))
                return f'exec {name} "$@"'
        except Exception as exc:  # pragma: no cover - best-effort
            warnings.append(f"failed to parse pyproject.toml: {exc}")

    # Fallback: look for module-style entry.
    for cand in ("main.py", "app/main.py", "src/main.py"):
        if (upstream / cand).exists():
            module = cand.replace("/", ".").removesuffix(".py")
            return f'exec python -m {module}'

    # Last resort.
    warnings.append("no entrypoint detected; run.sh will exit 1 with a message")
    return (
        'echo "[heaxhub] No entrypoint detected. Add [project.scripts] to '
        'pyproject.toml or commit a main.py."\n'
        'exit 1'
    )


# ---------------------------------------------------------------------------
# Node service template
# ---------------------------------------------------------------------------


def _node_service(
    sub: Submission, facts: StaticFacts, upstream: Path, warnings: list[str]
) -> tuple[dict[str, Any], str]:
    pkg_path = upstream / "package.json"
    scripts: dict[str, str] = {}
    try:
        scripts = json.loads(pkg_path.read_text(encoding="utf-8")).get("scripts", {})
    except Exception as exc:  # pragma: no cover
        warnings.append(f"failed to parse package.json: {exc}")

    has_build = "build" in scripts
    has_start = "start" in scripts

    manifest = {
        "schema_version": 2,
        "id": sub.proposed_app_id,
        "name": sub.name,
        "version": "0.1.0",
        "owner": str(sub.submitter_user_id),
        "status": "draft",
        "app_type": sub.proposed_app_type or "web_app",
        "execution_target": sub.proposed_execution_target or "linux_runner",
        "description": (sub.description or "Auto-synthesized by HEAXHub from "
                        "package.json. Review before publishing."),
        "tags": ["auto-synthesized", "nodejs"],
        "build": {
            "stack": "nextjs" if has_build else "node_service",
            "type": "nodejs",
            "node_version": facts.node_version or "20",
            "install": "pnpm install --frozen-lockfile" if (upstream / "pnpm-lock.yaml").exists() else "npm ci",
            **({"build": "pnpm build" if (upstream / "pnpm-lock.yaml").exists() else "npm run build"} if has_build else {}),
        },
        "launch": {
            "mode": "service",
            "command": "./.portal/run.sh",
            "health_check": {"path": "/", "interval_seconds": 5, "timeout_seconds": 3},
            "restart_policy": {"policy": "on_failure", "max_attempts": 3},
        },
        "permissions": {"visibility": "team"},
        "resources": {"cpu": 1, "memory_gb": 1, "gpu": False},
        "requirements": {"os": "linux"},
    }

    if not has_start:
        warnings.append(
            'package.json has no "scripts.start" — run.sh will exit 1. '
            "Add a start script or supply .portal/run.sh manually."
        )

    start_cmd = (
        'exec npm start --silent --'
        if has_start
        else 'echo "[heaxhub] package.json missing scripts.start"; exit 1'
    )

    run_sh = (
        '#!/usr/bin/env bash\n'
        '# Auto-synthesized by HEAXHub overlay_synthesizer.\n'
        'set -euo pipefail\n'
        'cd "$(dirname "$0")/../upstream"\n'
        f'{start_cmd}\n'
    )
    return manifest, run_sh


# ---------------------------------------------------------------------------
# Placeholder
# ---------------------------------------------------------------------------


def _placeholder(sub: Submission) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "id": sub.proposed_app_id,
        "name": sub.name,
        "version": "0.1.0",
        "owner": str(sub.submitter_user_id),
        "status": "draft",
        "app_type": sub.proposed_app_type or "cli_tool",
        "execution_target": sub.proposed_execution_target or "linux_runner",
        "description": (
            "PLACEHOLDER. HEAXHub could not detect a known stack in the "
            "upstream repo. Commit a .portal/manifest.yaml and run.sh, then "
            "re-trigger the build."
        ),
        "tags": ["placeholder"],
        "build": {"stack": "unknown"},
        "launch": {"mode": "job_runner", "command": "./.portal/run.sh"},
        "permissions": {"visibility": "team"},
        "resources": {"cpu": 1, "memory_gb": 1, "gpu": False},
        "requirements": {"os": "linux"},
    }
