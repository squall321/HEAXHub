"""Tests for the SIF-backed dispatch path in ``integration_launcher``.

``apt_runner`` is mocked end-to-end so the tests never touch a real apptainer
binary. We exercise the four contracts the launcher needs to keep:

  1. ``sif_path`` present + instance NOT running → ``apt_runner.instance_start``
     is called, then ``apt_runner.instance_exec`` is called, and a state file
     with ``instance_name`` + ``sif_path`` lands on disk.
  2. ``sif_path`` present + instance ALREADY running (state file says so and
     ``apt_runner.instance_list`` returns it) → no start/exec, ``action ==
     "already_running"``.
  3. ``sif_path`` absent → existing host-PATH code path runs (Popen on the
     real ``subprocess`` module) — no apt_runner traffic at all.
  4. ``stop()`` with state containing ``instance_name`` → calls
     ``apt_runner.instance_stop`` instead of ``os.killpg``.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import integration_launcher


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect STATE_DIR + LOG_DIR + _state_path into tmp_path."""
    state_dir = tmp_path / "state"
    log_dir = tmp_path / "logs"
    state_dir.mkdir()
    log_dir.mkdir()
    monkeypatch.setattr(integration_launcher, "STATE_DIR", state_dir)
    monkeypatch.setattr(integration_launcher, "LOG_DIR", log_dir)
    monkeypatch.setattr(
        integration_launcher, "_state_path",
        lambda c: state_dir / f"{c}.json",
    )

    # Caddy + port_allocator stubs so we don't touch real services.
    class _StubProxy:
        @staticmethod
        def register_app_route(*, app_id, port, base_path, strip_prefix=True):
            return SimpleNamespace(ok=True)

        @staticmethod
        def unregister_app_route(*, app_id):
            return None

    class _StubPorts:
        port = 17171

        @classmethod
        def allocate_port(cls, db, *, app_id, scope):
            return cls.port

        @staticmethod
        def release_port(db, *, port):
            return None

    monkeypatch.setattr(integration_launcher, "proxy_manager", _StubProxy)
    monkeypatch.setattr(integration_launcher, "port_allocator", _StubPorts)

    # Always healthy + 0 sleep to keep tests fast.
    monkeypatch.setattr(integration_launcher, "_is_healthy",
                        lambda port, p, *, root: True)
    monkeypatch.setattr(integration_launcher, "_is_alive", lambda pid: True)
    monkeypatch.setattr(integration_launcher.time, "sleep", lambda *_a, **_k: None)
    return tmp_path


def _manifest(stack: str = "fastapi", mode: str = "service") -> dict:
    return {
        "id": "demo_sif",
        "build": {"stack": stack},
        "launch": {"mode": mode, "health_check": {"path": "/health"}},
    }


# ---------------------------------------------------------------------------
# 1. SIF present + instance not running → start + exec
# ---------------------------------------------------------------------------


def test_starts_instance_when_sif_present_and_not_running(
    tmp_path: Path,
    isolated_state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = tmp_path / "demo_sif"
    ws.mkdir()
    sif = tmp_path / "demo_sif.sif"
    sif.write_bytes(b"fake-sif")

    started: list[dict] = []
    execed: list[dict] = []

    def fake_instance_list(**_kwargs):
        return []  # nothing running yet

    def fake_instance_start(*, sif, name, binds=(), cleanenv=True, env=None, **kw):
        started.append({
            "sif": Path(sif),
            "name": name,
            "binds": list(binds),
            "cleanenv": cleanenv,
            "env": dict(env or {}),
        })
        return subprocess.CompletedProcess(args=["instance", "start"], returncode=0)

    def fake_instance_exec(name, argv, env=None, *, cleanenv=True, cwd=None, **kw):
        execed.append({
            "name": name,
            "argv": list(argv),
            "env": dict(env or {}),
            "cleanenv": cleanenv,
            "cwd": cwd,
        })
        # Closes the log file handle (Popen would close it on exit).
        if "stdout" in kw and hasattr(kw["stdout"], "close"):
            pass
        return SimpleNamespace(pid=4242, poll=lambda: None, returncode=None)

    monkeypatch.setattr(integration_launcher.apt_runner, "instance_list", fake_instance_list)
    monkeypatch.setattr(integration_launcher.apt_runner, "instance_start", fake_instance_start)
    monkeypatch.setattr(integration_launcher.apt_runner, "instance_exec", fake_instance_exec)

    result = integration_launcher.launch(
        ws,
        manifest=_manifest(stack="fastapi"),
        db=None,
        slug="demo_sif",
        sif_path=sif,
    )

    assert result.action == "started", result.error
    assert result.port == 17171
    assert result.pid == 4242

    # start was called with the SIF + canonical instance name + workspace bind.
    assert len(started) == 1
    s = started[0]
    assert s["sif"] == sif
    assert s["name"] == "heax_app_demo_sif"
    assert (str(ws), "/workspace") in s["binds"]
    assert s["env"]["PORT"] == "17171"
    assert s["env"]["ROOT_PATH"] == "/apps/demo_sif"

    # exec ran the canonical fastapi argv inside that instance.
    assert len(execed) == 1
    e = execed[0]
    assert e["name"] == "heax_app_demo_sif"
    assert e["argv"][0] == "uvicorn"
    assert "app.main:app" in e["argv"]
    assert e["env"]["PORT"] == "17171"

    # State file recorded the SIF + instance.
    state = integration_launcher._read_state("demo_sif")
    assert state is not None
    assert state["instance_name"] == "heax_app_demo_sif"
    assert state["sif_path"] == str(sif)
    assert state["schema_version"] == integration_launcher._STATE_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# 2. Instance already running → reuse, no start/exec
# ---------------------------------------------------------------------------


def test_reuses_running_instance_when_already_started(
    tmp_path: Path,
    isolated_state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = tmp_path / "demo_sif"
    ws.mkdir()
    sif = tmp_path / "demo_sif.sif"
    sif.write_bytes(b"fake-sif")

    # Pre-populate state for a "previously launched" SIF instance.
    state_path = integration_launcher._state_path("demo_sif")
    state_path.write_text(json.dumps({
        "schema_version": integration_launcher._STATE_SCHEMA_VERSION,
        "slug": "demo_sif",
        "pid": 1234,
        "port": 17171,
        "base_path": "/apps/demo_sif",
        "health_path": "/health",
        "stack": "fastapi",
        "argv": [str(sif), "uvicorn", "app.main:app"],
        "instance_name": "heax_app_demo_sif",
        "sif_path": str(sif),
        "caddy_registered": True,
    }))

    monkeypatch.setattr(
        integration_launcher.apt_runner, "instance_list",
        lambda **kw: ["heax_app_demo_sif"],
    )

    def must_not_start(**_kw):  # pragma: no cover - assertion only
        raise AssertionError("instance_start must not be called when reused")

    def must_not_exec(*_a, **_kw):  # pragma: no cover - assertion only
        raise AssertionError("instance_exec must not be called when reused")

    monkeypatch.setattr(integration_launcher.apt_runner, "instance_start", must_not_start)
    monkeypatch.setattr(integration_launcher.apt_runner, "instance_exec", must_not_exec)

    result = integration_launcher.launch(
        ws,
        manifest=_manifest(stack="fastapi"),
        db=None,
        slug="demo_sif",
        sif_path=sif,
    )

    assert result.action == "already_running"
    assert result.pid == 1234
    assert result.port == 17171


# ---------------------------------------------------------------------------
# 3. No SIF → existing host-PATH code path runs (no apt_runner traffic)
# ---------------------------------------------------------------------------


def test_falls_back_to_host_path_when_no_sif(
    tmp_path: Path,
    isolated_state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = tmp_path / "demo_host"
    ws.mkdir()
    # Make the host-mode argv builder succeed without a real venv: stub it.
    monkeypatch.setattr(
        integration_launcher, "_argv_for",
        lambda workspace, spec, manifest, *, port, base_path: ["/bin/echo", "host-mode"],
    )

    popen_calls: list[list[str]] = []

    class _FakePopen:
        def __init__(self, argv, **_kw):
            popen_calls.append(argv)
            self.pid = 9999
            self.returncode = None

        def poll(self):
            return None

    monkeypatch.setattr(integration_launcher.subprocess, "Popen", _FakePopen)

    # Guard: apt_runner must NOT be called when no SIF is supplied.
    def fail(*_a, **_kw):  # pragma: no cover - assertion only
        raise AssertionError("apt_runner must not be called in host-PATH mode")

    monkeypatch.setattr(integration_launcher.apt_runner, "instance_list", fail)
    monkeypatch.setattr(integration_launcher.apt_runner, "instance_start", fail)
    monkeypatch.setattr(integration_launcher.apt_runner, "instance_exec", fail)

    manifest = _manifest(stack="fastapi")
    manifest["id"] = "demo_host"
    result = integration_launcher.launch(
        ws,
        manifest=manifest,
        db=None,
        slug="demo_host",
        sif_path=None,
    )

    assert result.action == "started"
    assert popen_calls and popen_calls[0] == ["/bin/echo", "host-mode"]

    # State file does NOT carry instance_name/sif_path in host mode.
    state = integration_launcher._read_state("demo_host")
    assert state is not None
    assert "instance_name" not in state
    assert "sif_path" not in state


# ---------------------------------------------------------------------------
# 4. stop() with SIF state → calls apt_runner.instance_stop
# ---------------------------------------------------------------------------


def test_stop_calls_instance_stop_when_state_has_instance_name(
    tmp_path: Path,
    isolated_state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = "demo_sif"
    state_path = integration_launcher._state_path(canonical)
    state_path.write_text(json.dumps({
        "schema_version": integration_launcher._STATE_SCHEMA_VERSION,
        "slug": canonical,
        "pid": 4242,
        "port": 17171,
        "base_path": f"/apps/{canonical}",
        "instance_name": "heax_app_demo_sif",
        "sif_path": "/tmp/fake.sif",
    }))

    stop_calls: list[tuple[str, dict]] = []

    def fake_instance_stop(name, **kwargs):
        stop_calls.append((name, kwargs))
        return subprocess.CompletedProcess(args=["instance", "stop"], returncode=0)

    monkeypatch.setattr(integration_launcher.apt_runner, "instance_stop", fake_instance_stop)

    # Make sure we DON'T fall through to os.killpg — fail loud if we do.
    def must_not_kill(*_a, **_kw):  # pragma: no cover
        raise AssertionError("stop() must use instance_stop, not killpg, for SIF state")

    monkeypatch.setattr(integration_launcher.os, "killpg", must_not_kill)

    killed = integration_launcher.stop(canonical, db=None)
    assert killed is True
    assert len(stop_calls) == 1
    assert stop_calls[0][0] == "heax_app_demo_sif"
    # State file is cleaned up after stop.
    assert integration_launcher._read_state(canonical) is None
