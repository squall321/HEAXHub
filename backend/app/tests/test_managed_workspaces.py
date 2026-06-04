"""Unit tests for ``managed_workspaces`` path helpers.

The module owns the layout under ``var/`` for per-integration workspaces +
SIFs. The tests redirect the four module-level constants to ``tmp_path`` so
nothing leaks into the real ``var/`` tree.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import managed_workspaces


@pytest.fixture()
def isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect MANAGED_ROOT/SIF_OUT_DIR/LOG_DIR to ``tmp_path``."""
    managed = tmp_path / "var" / "integration_workspaces"
    sifs = tmp_path / "var" / "sifs"
    logs = tmp_path / "var" / "logs"
    monkeypatch.setattr(managed_workspaces, "MANAGED_ROOT", managed)
    monkeypatch.setattr(managed_workspaces, "SIF_OUT_DIR", sifs)
    monkeypatch.setattr(managed_workspaces, "LOG_DIR", logs)
    return tmp_path


def test_workspace_for_creates_dir(isolated_paths: Path) -> None:
    """workspace_for returns an existing directory at .../<slug>/."""
    ws = managed_workspaces.workspace_for("heax-demo-streamlit")
    assert ws.is_dir()
    assert ws.name == "heax-demo-streamlit"
    assert ws.parent == managed_workspaces.MANAGED_ROOT
    # Idempotent — calling twice does not raise.
    ws2 = managed_workspaces.workspace_for("heax-demo-streamlit")
    assert ws2 == ws


def test_workspace_for_rejects_invalid_slugs(isolated_paths: Path) -> None:
    for bad in ["", "..", ".", "a/b"]:
        with pytest.raises(ValueError):
            managed_workspaces.workspace_for(bad)


def test_sif_path_for_returns_var_sifs_slug(isolated_paths: Path) -> None:
    """sif_path_for returns var/sifs/<slug>.sif and ensures the parent exists."""
    sif = managed_workspaces.sif_path_for("heax-demo-fastapi")
    assert sif.name == "heax-demo-fastapi.sif"
    assert sif.parent == managed_workspaces.SIF_OUT_DIR
    assert sif.parent.is_dir()
    # The SIF file itself must NOT be created — callers (apptainer build)
    # write it.
    assert not sif.exists()


def test_upstream_dir_handles_subpath(isolated_paths: Path) -> None:
    """upstream_dir returns base, base/<subpath>, and rejects traversal."""
    base = managed_workspaces.upstream_dir("demo-app")
    assert base.is_dir()
    assert base.name == "upstream"

    # Subpath joins underneath upstream/.
    sub = managed_workspaces.upstream_dir("demo-app", "apps/streamlit-demo")
    assert sub == base / "apps" / "streamlit-demo"

    # Leading slash stripped, empty/"." segments ignored.
    sub2 = managed_workspaces.upstream_dir("demo-app", "/apps/./streamlit-demo/")
    assert sub2 == base / "apps" / "streamlit-demo"

    # `..` rejected.
    with pytest.raises(ValueError):
        managed_workspaces.upstream_dir("demo-app", "../escape")
    with pytest.raises(ValueError):
        managed_workspaces.upstream_dir("demo-app", "apps/../../escape")

    # Empty subpath returns base unchanged.
    assert managed_workspaces.upstream_dir("demo-app", "") == base
    assert managed_workspaces.upstream_dir("demo-app", "   ") == base


def test_build_log_path(isolated_paths: Path) -> None:
    log = managed_workspaces.build_log_path("demo-app")
    assert log.name == "build_demo-app.log"
    assert log.parent == managed_workspaces.LOG_DIR
    assert log.parent.is_dir()
