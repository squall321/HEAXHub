"""Smoke tests for the deterministic static_analyzer.

Uses ``app/tests/fixtures/sample_python_cli`` as a stand-in for upstream/ —
no DB or network access required.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from app.services.static_analyzer import (
    StaticFacts,
    analyze,
    detect_languages,
    extract_env_references,
    extract_readme_commands,
    read_python_version,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_python_cli"


def _make_workspace(tmpdir: Path) -> Path:
    """Copy the fixture under tmpdir/upstream/ to mimic a real workspace."""
    workspace = tmpdir / "ws"
    upstream = workspace / "upstream"
    upstream.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURE, upstream)
    return workspace


def test_analyze_detects_python() -> None:
    with tempfile.TemporaryDirectory() as td:
        ws = _make_workspace(Path(td))
        facts: StaticFacts = analyze(ws)

    assert "python" in facts.languages
    assert facts.python_version == "3.11"
    assert facts.python_version_source == ".python-version"
    assert facts.has_dockerfile is False
    assert facts.has_apptainer_def is False
    # FOO from os.environ.get, API_KEY from os.environ[..], USER_NAME from os.getenv
    assert "FOO" in facts.detected_env_references
    assert "API_KEY" in facts.detected_env_references
    assert "USER_NAME" in facts.detected_env_references
    # README "How to run" commands
    joined = " ".join(facts.readme_run_commands)
    assert "pip install" in joined or "python -m src.main" in joined
    assert facts.repo_size_bytes > 0


def test_detect_languages_python() -> None:
    langs = detect_languages(FIXTURE)
    assert "python" in langs


def test_read_python_version_from_python_version_file() -> None:
    v, src = read_python_version(FIXTURE)
    assert v == "3.11"
    assert src == ".python-version"


def test_extract_env_references_python() -> None:
    envs = extract_env_references(FIXTURE)
    assert "FOO" in envs
    assert "API_KEY" in envs
    assert "USER_NAME" in envs


def test_extract_readme_commands_finds_how_to_run_block() -> None:
    cmds = extract_readme_commands(FIXTURE)
    assert any("python -m src.main" in c for c in cmds)


def test_analyze_missing_upstream_returns_empty() -> None:
    with tempfile.TemporaryDirectory() as td:
        facts = analyze(Path(td) / "nonexistent")
    assert facts.languages == []
    assert facts.python_version is None
    assert facts.commit_sha is None
