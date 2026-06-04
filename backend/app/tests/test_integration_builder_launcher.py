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
        def register_app_route(*, app_id, port, base_path, strip_prefix=True):
            register_calls.append((app_id, port, base_path, strip_prefix))
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


# ---------------------------------------------------------------------------
# Hardening tests (production robustness)
# ---------------------------------------------------------------------------


def test_builder_logs_failure_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing pip install must leave a build_<slug>.log AND surface the
    log tail in BuildResult.error so the operator can diagnose without SSH."""
    ws = tmp_path / "demo-fail"
    ws.mkdir()
    (ws / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.10"\n'
    )

    # Redirect the module-level LOG_DIR so we don't pollute the repo.
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(integration_builder, "LOG_DIR", log_dir)
    # Skip retry backoff so the test stays fast.
    monkeypatch.setattr(integration_builder, "_INSTALL_RETRY_DELAYS", (0, 0, 0))

    err_blob = b"ERROR: could not find a version that satisfies fictional-pkg==9.9"

    def fake_run(cmd, *, cwd, check, timeout, capture_output):
        # Let `python -m venv` succeed so we get to the pip step.
        if "venv" in cmd:
            (ws / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
            (ws / ".venv" / "bin" / "python").write_text("")
            (ws / ".venv" / "bin" / "pip").write_text("")
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        # Pip fails — and the failure shows up only in stderr (the bug we fixed:
        # the old builder swallowed stderr entirely).
        return SimpleNamespace(returncode=1, stdout=b"", stderr=err_blob)

    monkeypatch.setattr(subprocess, "run", fake_run)

    r = integration_builder.build(
        ws, manifest={"build": {"stack": "python_cli", "python_version": "3.11"}}
    )
    assert r.action == "failed"
    # Log file must exist and contain the stderr we captured.
    log_path = log_dir / "build_demo-fail.log"
    assert log_path.exists(), f"build log not created at {log_path}"
    assert b"fictional-pkg" in log_path.read_bytes()
    # And the error string surfaced to the API must include a tail.
    assert "fictional-pkg" in (r.error or ""), r.error


def test_builder_skips_when_pyproject_hash_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare ``touch pyproject.toml`` (mtime bump, content unchanged) must
    NOT trigger a rebuild — only content changes should."""
    ws = tmp_path / "demo-stable"
    ws.mkdir()
    py = ws / "pyproject.toml"
    py.write_text('[project]\nname = "demo"\nversion = "0.1.0"\n')

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(integration_builder, "LOG_DIR", log_dir)

    def fake_run(cmd, *, cwd, check, timeout, capture_output):
        if "venv" in cmd:
            (ws / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
            (ws / ".venv" / "bin" / "python").write_text("")
            (ws / ".venv" / "bin" / "pip").write_text("")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    r1 = integration_builder.build(
        ws, manifest={"build": {"stack": "python_cli"}}
    )
    assert r1.action == "built"

    # Bump mtime but keep content identical (simulates `touch` or git checkout).
    now = time.time() + 60
    import os as _os
    _os.utime(py, (now, now))

    r2 = integration_builder.build(
        ws, manifest={"build": {"stack": "python_cli"}}
    )
    assert r2.action == "skipped", "mtime-only change must not trigger rebuild"

    # Now ACTUALLY change the content — must rebuild.
    py.write_text('[project]\nname = "demo"\nversion = "0.2.0"\n')
    r3 = integration_builder.build(
        ws, manifest={"build": {"stack": "python_cli"}}
    )
    assert r3.action == "built", "content change must trigger rebuild"


def test_builder_refuses_unsatisfied_python_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If pyproject demands >=3.13 but the picked interpreter is 3.10, the
    builder must refuse with a clear error instead of silently using 3.10."""
    ws = tmp_path / "demo-pyreq"
    ws.mkdir()
    (ws / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.13"\n'
    )

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(integration_builder, "LOG_DIR", log_dir)
    # Force _pick_python to return python3.10 (which will fail the >=3.13 check).
    monkeypatch.setattr(
        integration_builder, "_pick_python", lambda spec, bs: "python3.10"
    )

    r = integration_builder.build(
        ws, manifest={"build": {"stack": "python_cli"}}
    )
    assert r.action == "failed"
    assert "requires-python" in (r.error or "")
    assert "3.13" in (r.error or "")


def test_launcher_refuses_to_kill_pid_with_foreign_cmdline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the stored pid has been reused by an unrelated process, stop() must
    NOT send SIGTERM to that stranger."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(integration_launcher, "STATE_DIR", state_dir)
    monkeypatch.setattr(integration_launcher, "_state_path",
                        lambda c: state_dir / f"{c}.json")

    integration_launcher._write_state("svc", {
        "schema_version": 1,
        "slug": "svc",
        "pid": 4242,
        "port": 9001,
        "argv": ["/opt/heaxhub/.venv/bin/uvicorn", "app.main:app"],
    })

    # Pretend the pid IS alive but actually belongs to /bin/bash now (reused).
    monkeypatch.setattr(integration_launcher, "_is_alive", lambda pid: True)
    monkeypatch.setattr(
        integration_launcher, "_pid_matches_argv",
        lambda pid, argv: False,
    )

    killpg_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(integration_launcher.os, "killpg",
                        lambda pgid, sig: killpg_calls.append((pgid, sig)))

    class _StubProxy:
        @staticmethod
        def unregister_app_route(*, app_id):
            return None
    monkeypatch.setattr(integration_launcher, "proxy_manager", _StubProxy)

    class _StubPorts:
        @staticmethod
        def release_port(db, *, port):
            return None
    monkeypatch.setattr(integration_launcher, "port_allocator", _StubPorts)

    killed = integration_launcher.stop("svc", db=None)
    assert killed is False, "must not claim to have killed a foreign pid"
    assert killpg_calls == [], "must not send SIGTERM to a reused pid"


def test_launcher_state_schema_version_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A state file from a future schema version must be ignored (not crash)
    and a missing version must be tolerated."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(integration_launcher, "STATE_DIR", state_dir)
    monkeypatch.setattr(integration_launcher, "_state_path",
                        lambda c: state_dir / f"{c}.json")

    # Future schema → must be ignored.
    (state_dir / "future.json").write_text(json.dumps({
        "schema_version": 999, "pid": 1, "port": 1,
        "weird_new_field": "boom",
    }))
    assert integration_launcher._read_state("future") is None

    # No schema_version at all (legacy file) → must be accepted but tagged.
    (state_dir / "legacy.json").write_text(json.dumps({
        "pid": 7, "port": 1234, "slug": "legacy",
    }))
    legacy = integration_launcher._read_state("legacy")
    assert legacy is not None
    assert legacy["pid"] == 7
    assert legacy["schema_version"] == integration_launcher._STATE_SCHEMA_VERSION

    # Round-trip a current-version write.
    integration_launcher._write_state("cur", {"slug": "cur", "pid": 99, "port": 9})
    cur = integration_launcher._read_state("cur")
    assert cur and cur["schema_version"] == integration_launcher._STATE_SCHEMA_VERSION
