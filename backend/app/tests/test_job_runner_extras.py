"""Tests for the cpp_executable + apptainer_sif job-runner stacks.

These stacks share two important properties:

  * ``launch_mode: job_runner`` — the long-running ``integration_launcher``
    must NOT spawn anything for them.
  * The builder is the only place where the workspace shape is verified
    before the user submits their first job. We assert each branch surfaces
    a clear operator-visible error (BuildResult.action == "failed" with a
    helpful message) rather than crashing.

Subprocess calls are stubbed so the test stays fast and offline.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import integration_builder, integration_launcher


# ---------------------------------------------------------------------------
# cpp_executable — builder branch
# ---------------------------------------------------------------------------


def test_cpp_builder_skips_when_no_cmake_or_makefile(tmp_path: Path) -> None:
    """No CMakeLists.txt + no Makefile → builder returns skipped, not failed.

    Job-mode integrations are allowed to ship pre-built binaries; the builder
    should be no-op in that case so the user can run their job immediately.
    """
    ws = tmp_path / "cpp-bare"
    ws.mkdir()
    r = integration_builder.build(
        ws, manifest={"build": {"stack": "cpp_executable"}}
    )
    assert r.action == "skipped", r


def test_cpp_builder_runs_cmake_when_cmakelists_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CMakeLists.txt → builder runs cmake configure + cmake build."""
    ws = tmp_path / "cpp-cmake"
    ws.mkdir()
    (ws / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.10)\nproject(demo)\n"
    )

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(integration_builder, "LOG_DIR", log_dir)
    monkeypatch.setattr(
        integration_builder.shutil, "which",
        lambda x: "/usr/bin/cmake" if x == "cmake" else None,
    )

    calls: list[str] = []

    def fake_run(cmd, *, cwd, check, timeout, capture_output, env=None):
        # _run_shell wraps the cmd in /bin/sh -c, so cmd is ["/bin/sh","-c",str]
        if isinstance(cmd, list) and len(cmd) >= 3 and cmd[0] == "/bin/sh":
            calls.append(cmd[2])
        else:
            calls.append(" ".join(cmd))
        # Materialize the binary so the sentinel-skip path doesn't loop on
        # subsequent builds.
        (ws / "build" / "bin").mkdir(parents=True, exist_ok=True)
        (ws / "build" / "bin" / "solver").write_text("")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    r = integration_builder.build(
        ws, manifest={"build": {"stack": "cpp_executable"}}
    )
    assert r.action == "built", r
    # Must invoke both cmake configure and cmake --build.
    assert any("cmake -S . -B build" in c for c in calls), calls
    assert any("cmake --build build" in c for c in calls), calls

    # Second run with sentinel + binary present → skipped.
    r2 = integration_builder.build(
        ws, manifest={"build": {"stack": "cpp_executable"}}
    )
    assert r2.action == "skipped", r2


def test_cpp_builder_runs_make_when_makefile_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plain Makefile (no CMakeLists.txt) → builder falls back to ``make -j``."""
    ws = tmp_path / "cpp-make"
    ws.mkdir()
    (ws / "Makefile").write_text("all:\n\techo build\n")

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(integration_builder, "LOG_DIR", log_dir)
    monkeypatch.setattr(
        integration_builder.shutil, "which",
        lambda x: "/usr/bin/make" if x == "make" else None,
    )

    calls: list[str] = []

    def fake_run(cmd, *, cwd, check, timeout, capture_output, env=None):
        if isinstance(cmd, list) and len(cmd) >= 3 and cmd[0] == "/bin/sh":
            calls.append(cmd[2])
        else:
            calls.append(" ".join(cmd))
        (ws / "build" / "bin").mkdir(parents=True, exist_ok=True)
        (ws / "build" / "bin" / "solver").write_text("")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    r = integration_builder.build(
        ws, manifest={"build": {"stack": "cpp_executable"}}
    )
    assert r.action == "built", r
    assert any(c.startswith("make") or c == "make -j" for c in calls), calls
    # Must NOT have called cmake — we only had a Makefile.
    assert not any("cmake" in c for c in calls), calls


def test_cpp_builder_fails_when_cmake_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CMakeLists.txt present but cmake missing → clear operator instruction."""
    ws = tmp_path / "cpp-nocmake"
    ws.mkdir()
    (ws / "CMakeLists.txt").write_text("project(demo)\n")

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(integration_builder, "LOG_DIR", log_dir)
    monkeypatch.setattr(integration_builder.shutil, "which", lambda x: None)

    r = integration_builder.build(
        ws, manifest={"build": {"stack": "cpp_executable"}}
    )
    assert r.action == "failed"
    assert "cmake" in (r.error or "").lower()


# ---------------------------------------------------------------------------
# apptainer_sif — builder branch
# ---------------------------------------------------------------------------


def test_apptainer_sif_builder_validates_file_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace with a matching .sif file passes; missing file fails clean."""
    ws = tmp_path / "sif-demo"
    ws.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(integration_builder, "LOG_DIR", log_dir)

    # Missing SIF → builder must fail with a clear path in the error.
    r_missing = integration_builder.build(
        ws,
        manifest={"build": {"stack": "apptainer_sif", "sif_path": "myapp.sif"}},
    )
    assert r_missing.action == "failed"
    assert "myapp.sif" in (r_missing.error or "")
    assert "not found" in (r_missing.error or "").lower()

    # Materialize the file → builder must succeed (built on first run).
    (ws / "myapp.sif").write_bytes(b"fake sif header")
    r_ok = integration_builder.build(
        ws,
        manifest={"build": {"stack": "apptainer_sif", "sif_path": "myapp.sif"}},
    )
    assert r_ok.action == "built", r_ok

    # Re-running with the file unchanged → skipped via sentinel.
    r_again = integration_builder.build(
        ws,
        manifest={"build": {"stack": "apptainer_sif", "sif_path": "myapp.sif"}},
    )
    assert r_again.action == "skipped", r_again


def test_apptainer_sif_builder_rejects_path_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sif_path that escapes the workspace must fail with a clear message."""
    ws = tmp_path / "sif-traversal"
    ws.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(integration_builder, "LOG_DIR", log_dir)

    r = integration_builder.build(
        ws,
        manifest={
            "build": {
                "stack": "apptainer_sif",
                "sif_path": "../../etc/passwd",
            }
        },
    )
    assert r.action == "failed"
    assert "outside workspace" in (r.error or "")


# ---------------------------------------------------------------------------
# Launcher — both stacks are job_runner mode and must be skipped
# ---------------------------------------------------------------------------


def test_launcher_skips_cpp_executable_job_mode(tmp_path: Path) -> None:
    ws = tmp_path / "cpp-launch"
    ws.mkdir()
    r = integration_launcher.launch(
        ws,
        manifest={
            "launch": {"mode": "job_runner"},
            "build": {"stack": "cpp_executable"},
        },
        db=None,
    )
    assert r.action == "skipped"


def test_launcher_skips_apptainer_sif_job_mode(tmp_path: Path) -> None:
    ws = tmp_path / "sif-launch"
    ws.mkdir()
    r = integration_launcher.launch(
        ws,
        manifest={
            "launch": {"mode": "job_runner"},
            "build": {"stack": "apptainer_sif"},
        },
        db=None,
    )
    assert r.action == "skipped"


# ---------------------------------------------------------------------------
# stack_resolver — both stacks must load with the expected runtime/builder
# ---------------------------------------------------------------------------


def test_stack_resolver_exposes_cpp_executable() -> None:
    from app.services import stack_resolver

    stack_resolver.reload_stacks()
    spec = stack_resolver.resolve("cpp_executable")
    assert spec.runtime == "native_binary"
    assert spec.builder == "cmake_make"
    assert spec.launch_mode == "job_runner"


def test_stack_resolver_exposes_apptainer_sif() -> None:
    from app.services import stack_resolver

    stack_resolver.reload_stacks()
    spec = stack_resolver.resolve("apptainer_sif")
    assert spec.runtime == "apptainer"
    assert spec.builder == "noop"
    assert spec.launch_mode == "job_runner"
    assert spec.execution_target == "apptainer"
