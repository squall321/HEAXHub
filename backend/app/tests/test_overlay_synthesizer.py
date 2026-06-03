"""Tests for overlay_synthesizer — manifest + run.sh synthesis from static facts."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
import yaml

from app.db.models.submission import Submission, SubmissionStatus
from app.services import overlay_synthesizer
from app.services.static_analyzer import StaticFacts


def _make_submission() -> Submission:
    return Submission(
        id=uuid.uuid4(),
        submitter_user_id=uuid.uuid4(),
        proposed_app_id="demo_app",
        name="Demo App",
        upstream_repo_url="https://example.com/demo.git",
        status=SubmissionStatus.PROVISIONING,
    )


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "upstream").mkdir(parents=True, exist_ok=True)
    (ws / "overlay" / ".portal").mkdir(parents=True, exist_ok=True)
    return ws


def test_synthesize_copies_upstream_manifest_unchanged(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    upstream_portal = ws / "upstream" / ".portal"
    upstream_portal.mkdir(parents=True, exist_ok=True)
    upstream_manifest = {"schema_version": 2, "id": "custom", "name": "Custom"}
    (upstream_portal / "manifest.yaml").write_text(yaml.safe_dump(upstream_manifest))
    (upstream_portal / "run.sh").write_text("#!/bin/sh\necho hi\n")

    sub = _make_submission()
    result = overlay_synthesizer.synthesize_overlay(ws, sub, StaticFacts())

    assert result.synthesized is False
    assert result.flavor == "upstream"
    overlay_manifest = ws / "overlay" / ".portal" / "manifest.yaml"
    data = yaml.safe_load(overlay_manifest.read_text())
    assert data["id"] == "custom"
    overlay_run = ws / "overlay" / ".portal" / "run.sh"
    assert overlay_run.exists()
    # 0o755
    assert overlay_run.stat().st_mode & 0o777 == 0o755


def test_synthesize_python_pyproject_creates_cli_manifest(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    (ws / "upstream" / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
        '[project.scripts]\ndemo-app = "demo.cli:main"\n'
    )

    sub = _make_submission()
    result = overlay_synthesizer.synthesize_overlay(
        ws, sub, StaticFacts(languages=["python"], python_version="3.11")
    )

    assert result.synthesized is True
    assert result.flavor == "python_cli"
    overlay_manifest = ws / "overlay" / ".portal" / "manifest.yaml"
    data = yaml.safe_load(overlay_manifest.read_text())
    assert data["app_type"] == "cli_tool"
    assert data["build"]["stack"] == "python_cli"
    assert data["build"]["python_version"] == "3.11"
    assert data["launch"]["mode"] == "job_runner"

    run_sh = (ws / "overlay" / ".portal" / "run.sh").read_text()
    assert "exec demo-app" in run_sh
    assert sub.status == SubmissionStatus.PROVISIONING  # unchanged


def test_synthesize_python_falls_back_to_main_module(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    (ws / "upstream" / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
    )
    (ws / "upstream" / "main.py").write_text('print("hi")\n')

    sub = _make_submission()
    result = overlay_synthesizer.synthesize_overlay(
        ws, sub, StaticFacts(languages=["python"])
    )

    run_sh = (ws / "overlay" / ".portal" / "run.sh").read_text()
    assert "python -m main" in run_sh
    assert result.flavor == "python_cli"


def test_synthesize_node_package_creates_service_manifest(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    (ws / "upstream" / "package.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "version": "0.1.0",
                "scripts": {"start": "next start", "build": "next build"},
            }
        )
    )

    sub = _make_submission()
    sub.proposed_app_type = "web_app"
    result = overlay_synthesizer.synthesize_overlay(
        ws, sub, StaticFacts(languages=["javascript"], node_version="20")
    )

    assert result.synthesized is True
    assert result.flavor == "node_service"
    overlay_manifest = ws / "overlay" / ".portal" / "manifest.yaml"
    data = yaml.safe_load(overlay_manifest.read_text())
    assert data["launch"]["mode"] == "service"
    assert data["build"]["stack"] == "nextjs"
    assert data["build"]["node_version"] == "20"

    run_sh = (ws / "overlay" / ".portal" / "run.sh").read_text()
    assert "npm start" in run_sh


def test_synthesize_empty_repo_creates_placeholder_and_flags(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    # upstream/ exists but empty — no manifest, no pyproject, no package.json.

    sub = _make_submission()
    result = overlay_synthesizer.synthesize_overlay(ws, sub, StaticFacts())

    assert result.synthesized is True
    assert result.flavor == "placeholder"
    assert sub.status == SubmissionStatus.MANIFEST_REQUIRED
    assert result.warnings
    overlay_manifest = ws / "overlay" / ".portal" / "manifest.yaml"
    data = yaml.safe_load(overlay_manifest.read_text())
    assert "placeholder" in data["tags"]
    assert (ws / "overlay" / ".portal" / "run.sh").exists() is False
