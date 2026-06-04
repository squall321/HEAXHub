"""Toolchain SIF dispatch tests.

These pin two contracts:

  1. ``toolchain_dispatch.resolve_sif`` reads the disk on every call and only
     returns a Path when ``heaxhub_toolchain_<key>.sif`` actually exists in
     one of the candidate directories. Stacks that don't map to a SIF — and
     stacks whose SIF file is missing — return ``None`` so the builder falls
     back to host PATH.

  2. ``integration_builder`` wraps the install command in ``apptainer exec``
     when the resolver returns a Path, and runs it bare otherwise. This is the
     "auto-dispatch" the operators see at runtime; we intercept
     ``subprocess.run`` so the test stays offline.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.services import integration_builder, toolchain_dispatch


# ---------------------------------------------------------------------------
# resolve_sif (pure)
# ---------------------------------------------------------------------------


def _force_settings_dir(monkeypatch: pytest.MonkeyPatch, dir_: Path) -> None:
    """Make resolve_sif look at ``dir_`` only.

    Settings is lru_cached; monkeypatching the env var doesn't refresh it. We
    instead patch the running Settings instance directly and squash the
    $HOME/serviceApptainers fallback by pointing $HOME at a fresh tmp dir.
    """
    s = get_settings()
    monkeypatch.setattr(s, "toolchain_sif_dir", str(dir_), raising=False)
    # Clear the dev fallback so it can't satisfy the probe.
    monkeypatch.setenv("HOME", str(dir_.parent / "__no_home__"))


def test_resolve_sif_returns_path_when_file_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sif = tmp_path / "heaxhub_toolchain_python312.sif"
    sif.write_bytes(b"")  # touch — content irrelevant, only existence matters
    _force_settings_dir(monkeypatch, tmp_path)

    assert toolchain_dispatch.resolve_sif("streamlit") == sif
    assert toolchain_dispatch.resolve_sif("fastapi") == sif


def test_resolve_sif_returns_none_when_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # tmp_path is empty — no SIF file there.
    _force_settings_dir(monkeypatch, tmp_path)
    assert toolchain_dispatch.resolve_sif("streamlit") is None


def test_resolve_sif_returns_none_for_unmapped_stack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Even if a SIF happened to be on disk, these stacks must never dispatch
    # into one (static / R / external are deliberately host-only).
    (tmp_path / "heaxhub_toolchain_python312.sif").write_bytes(b"")
    _force_settings_dir(monkeypatch, tmp_path)
    assert toolchain_dispatch.resolve_sif("static_html") is None
    assert toolchain_dispatch.resolve_sif("external_link") is None
    assert toolchain_dispatch.resolve_sif("r_script") is None


def test_resolve_sif_picks_up_new_file_without_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No in-process caching: dropping a SIF in is visible on the next call."""
    _force_settings_dir(monkeypatch, tmp_path)
    assert toolchain_dispatch.resolve_sif("streamlit") is None

    sif = tmp_path / "heaxhub_toolchain_python312.sif"
    sif.write_bytes(b"")
    assert toolchain_dispatch.resolve_sif("streamlit") == sif


# ---------------------------------------------------------------------------
# Builder auto-dispatch
# ---------------------------------------------------------------------------


def _bootstrap_python_workspace(ws: Path) -> None:
    ws.mkdir()
    (ws / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.10"\n'
    )


def _fake_subprocess_run(ws: Path, calls: list[list[str]]):
    """Build a fake subprocess.run that records argv and fakes a venv."""

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        # Mimic ``python -m venv`` populating the venv.
        if any("venv" in str(part) for part in cmd):
            (ws / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
            (ws / ".venv" / "bin" / "python").write_text("")
            (ws / ".venv" / "bin" / "pip").write_text("")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    return fake_run


def test_builder_wraps_when_sif_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "demo-py-sif"
    _bootstrap_python_workspace(ws)

    sif_dir = tmp_path / "sifs"
    sif_dir.mkdir()
    sif_path = sif_dir / "heaxhub_toolchain_python312.sif"
    sif_path.write_bytes(b"")
    _force_settings_dir(monkeypatch, sif_dir)

    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(ws, calls))

    r = integration_builder.build(
        ws, manifest={"build": {"stack": "python_cli", "python_version": "3.11"}}
    )
    assert r.action == "built", r.error

    # First call is `python -m venv` on host — never wrapped (would break the
    # venv if SIF disappears later). The pip install calls MUST be wrapped.
    pip_calls = [c for c in calls if any("pip" in part for part in c)]
    assert pip_calls, calls
    for argv in pip_calls:
        assert argv[0] == "apptainer", argv
        assert "exec" in argv
        assert "--cleanenv" in argv
        # workspace bind
        assert any(part == f"{ws}:/workspace" for part in argv), argv
        # SIF path
        assert str(sif_path) in argv, argv
        # bash -lc
        assert argv[-3:-1] == ["bash", "-lc"], argv
        # inner command runs pip install -e .
        inner = argv[-1]
        assert inner.startswith("cd /workspace && "), inner
        assert "pip" in inner and "install" in inner


def test_builder_runs_directly_when_no_sif(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "demo-py-host"
    _bootstrap_python_workspace(ws)

    # Empty SIF dir → resolve_sif returns None → builder uses host venv pip.
    sif_dir = tmp_path / "empty"
    sif_dir.mkdir()
    _force_settings_dir(monkeypatch, sif_dir)

    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run(ws, calls))

    r = integration_builder.build(
        ws, manifest={"build": {"stack": "python_cli", "python_version": "3.11"}}
    )
    assert r.action == "built", r.error

    pip_calls = [c for c in calls if any("pip" in part for part in c)]
    assert pip_calls, calls
    for argv in pip_calls:
        assert "apptainer" not in argv, argv
        # bare pip path from the venv
        assert argv[0].endswith("/pip"), argv
        assert "install" in argv, argv
    # One of the calls must be the editable install of the workspace itself.
    assert any("-e" in c and "." in c for c in pip_calls), pip_calls
