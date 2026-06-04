"""Smoke tests for integration_builder + integration_launcher.

We don't actually spawn pnpm/streamlit here — the builder runs subprocess
which we monkeypatch to a no-op so the test stays fast and offline. The
intent is to lock in:
  - decision tree (stack → runtime → build/launch dispatch)
  - idempotency (sentinel mtime probe)
  - error pass-through (BuildResult.action == "failed" instead of raising)
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.services import integration_builder, integration_launcher


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _write_manifest(workspace: Path, **overrides) -> dict:
    workspace.mkdir(parents=True, exist_ok=True)
    portal = workspace / ".portal"
    portal.mkdir(exist_ok=True)
    base = {
        "schema_version": 2,
        "id": workspace.name.replace("-", "_"),
        "name": "Test",
        "version": "0.1.0",
        "owner": "test",
        "status": "stable",
        "app_type": "cli_tool",
        "execution_target": "linux_runner",
        "build": {"stack": "python_cli"},
        "launch": {"mode": "job_runner", "command": "./.portal/run.sh"},
    }
    base.update(overrides)
    (portal / "manifest.yaml").write_text(yaml.safe_dump(base))
    return base


def test_builder_unknown_stack_returns_failed(tmp_path: Path) -> None:
    ws = tmp_path / "demo"
    ws.mkdir()
    manifest = {"build": {"stack": "no_such_stack"}}
    r = integration_builder.build(ws, manifest=manifest)
    assert r.action == "failed"
    assert "unknown stack" in (r.error or "")


def test_builder_python_no_pyproject_skips(tmp_path: Path) -> None:
    ws = tmp_path / "demo-cli"
    _write_manifest(ws)
    # No pyproject.toml → builder should return skipped, not failed.
    r = integration_builder.build(ws, manifest={"build": {"stack": "python_cli"}})
    assert r.action == "skipped"


def test_builder_python_runs_pip_install_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "demo-py"
    ws.mkdir()
    (ws / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.10"\n'
    )

    calls: list[list[str]] = []

    def fake_run(cmd, *, cwd, check, timeout, capture_output):
        calls.append(cmd)
        # simulate venv directory + python binary after 'python -m venv'
        if "venv" in cmd:
            (ws / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
            (ws / ".venv" / "bin" / "python").write_text("")
            (ws / ".venv" / "bin" / "pip").write_text("")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    r = integration_builder.build(
        ws, manifest={"build": {"stack": "python_cli", "python_version": "3.11"}}
    )
    assert r.action == "built"
    # At least one of the calls should be a pip install -e .
    assert any(("pip" in c[0] and "install" in c and "-e" in c) for c in calls), calls

    # Second run with sentinel present and pyproject unchanged → skipped.
    r2 = integration_builder.build(
        ws, manifest={"build": {"stack": "python_cli", "python_version": "3.11"}}
    )
    assert r2.action == "skipped"


def test_builder_nodejs_calls_pnpm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "demo-node"
    ws.mkdir()
    (ws / "package.json").write_text(json.dumps({
        "name": "demo", "version": "0.1.0",
        "scripts": {"build": "next build", "start": "next start"},
    }))

    calls: list[list[str]] = []
    def fake_run(cmd, *, cwd, check, timeout, capture_output):
        calls.append(cmd)
        (ws / "node_modules").mkdir(exist_ok=True)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(integration_builder.shutil, "which", lambda x: "/usr/bin/pnpm" if x == "pnpm" else None)

    r = integration_builder.build(ws, manifest={"build": {"stack": "nextjs"}})
    assert r.action == "built"
    cmds = [" ".join(c) for c in calls]
    assert any("install" in c for c in cmds), cmds
    assert any(c.endswith(" build") for c in cmds), cmds


# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------


def test_launcher_skips_job_runner_mode(tmp_path: Path) -> None:
    ws = tmp_path / "demo-cli"
    _write_manifest(ws, launch={"mode": "job_runner"})
    r = integration_launcher.launch(ws, manifest={"launch": {"mode": "job_runner"}}, db=None)
    assert r.action == "skipped"


def test_launcher_unknown_stack_fails(tmp_path: Path) -> None:
    ws = tmp_path / "demo-bad"
    ws.mkdir()
    r = integration_launcher.launch(
        ws,
        manifest={"launch": {"mode": "service"}, "build": {"stack": "no_such"}},
        db=None,
    )
    assert r.action == "failed"
    assert "unknown stack" in (r.error or "")


def test_launcher_already_running_reuses_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "demo-svc"
    ws.mkdir()
    canonical = "demo_svc"

    # Pre-populate state file to look like a previous run.
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(integration_launcher, "STATE_DIR", state_dir)
    monkeypatch.setattr(integration_launcher, "_state_path",
                        lambda c: state_dir / f"{c}.json")
    (state_dir / f"{canonical}.json").write_text(json.dumps({
        "slug": canonical, "pid": 1, "port": 9999, "base_path": f"/apps/{canonical}",
        "health_path": "/health",
    }))

    monkeypatch.setattr(integration_launcher, "_is_alive", lambda pid: True)
    monkeypatch.setattr(integration_launcher, "_is_healthy",
                        lambda port, p, *, root: True)

    register_calls = []
    class _Stub:
        @staticmethod
        def register_app_route(*, app_id, port, base_path):
            register_calls.append((app_id, port, base_path))
            return SimpleNamespace(ok=True)
    monkeypatch.setattr(integration_launcher, "proxy_manager", _Stub)

    manifest = {
        "id": canonical,
        "launch": {"mode": "service"},
        "build": {"stack": "fastapi"},
    }
    r = integration_launcher.launch(ws, manifest=manifest, db=None)
    assert r.action == "already_running"
    assert r.port == 9999
    assert register_calls and register_calls[0][0] == canonical


def test_launcher_state_io_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(integration_launcher, "STATE_DIR", tmp_path)
    monkeypatch.setattr(integration_launcher, "_state_path",
                        lambda c: tmp_path / f"{c}.json")
    integration_launcher._write_state("foo", {"slug": "foo", "pid": 42, "port": 1234})
    state = integration_launcher._read_state("foo")
    assert state and state["pid"] == 42
    integration_launcher._delete_state("foo")
    assert integration_launcher._read_state("foo") is None
