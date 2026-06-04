"""Unit tests for ``integration_sif_builder.build_sif``.

The tests don't actually invoke apptainer — ``apt_runner.run_build`` is
monkeypatched so we can assert on its call arguments and simulate
success / failure paths. ``var/sifs`` and ``var/logs`` are pointed at
``tmp_path`` so the test suite never writes outside the sandbox.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.services import integration_sif_builder as sif_builder


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect SIF_DIR + LOG_DIR into ``tmp_path``."""
    sif_dir = tmp_path / "sifs"
    log_dir = tmp_path / "logs"
    sif_dir.mkdir()
    log_dir.mkdir()
    monkeypatch.setattr(sif_builder, "SIF_DIR", sif_dir)
    monkeypatch.setattr(sif_builder, "LOG_DIR", log_dir)
    return tmp_path


def _manifest(stack: str = "flask", entrypoint: str | None = None) -> dict[str, Any]:
    m: dict[str, Any] = {
        "schema_version": 2,
        "id": "demo",
        "name": "Demo",
        "build": {"stack": stack},
        "launch": {"mode": "service"},
    }
    if entrypoint is not None:
        m["launch"]["command"] = entrypoint
    return m


def _fetch_result(commit: str = "deadbeef", workspace: str = "/tmp/ws") -> dict[str, Any]:
    return {"commit_sha": commit, "workspace": workspace}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_skipped_when_no_template(isolated_dirs: Path) -> None:
    """A stack with no .def file → action='skipped' with a clear error."""
    result = sif_builder.build_sif(
        slug="demo",
        manifest=_manifest(stack="external_link"),
        fetch_result=_fetch_result(),
    )
    assert result.action == "skipped"
    assert result.sif is None
    assert result.hash is None
    assert "no SIF template" in (result.error or "")
    assert "external_link" in (result.error or "")


def test_skipped_when_hash_matches(
    isolated_dirs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing SIF + matching sentinel → cache hit, apt_runner NOT called."""
    sif_path = sif_builder.SIF_DIR / "demo.sif"
    sentinel = sif_builder.SIF_DIR / "demo.sif.hash"
    sif_path.write_bytes(b"fake-sif")

    # Compute the hash that build_sif will compute, and pre-write it.
    manifest = _manifest(stack="flask")
    fetch = _fetch_result()
    template_bytes = (sif_builder.TEMPLATES_DIR / "flask.def").read_bytes()
    expected_hash = sif_builder._hash_inputs(
        fetch["commit_sha"], manifest, template_bytes
    )
    sentinel.write_text(expected_hash + "\n", encoding="utf-8")

    called: list[Any] = []

    def fail_if_called(*args, **kwargs):  # pragma: no cover - assertion only
        called.append(args)
        raise AssertionError("apt_runner.run_build must not be called on cache hit")

    monkeypatch.setattr(sif_builder.apt_runner, "run_build", fail_if_called)

    result = sif_builder.build_sif(slug="demo", manifest=manifest, fetch_result=fetch)
    assert result.action == "skipped"
    assert result.sif == sif_path
    assert result.hash == expected_hash
    assert called == []


def test_renders_template_with_placeholders(
    isolated_dirs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rendered .def has every placeholder substituted with real values."""

    def fake_run_build(*, sif_out, def_in, fakeroot, force, **kwargs):
        # Touch the SIF so build_sif sees success.
        Path(sif_out).write_bytes(b"fake")
        return subprocess.CompletedProcess(args=["build"], returncode=0)

    monkeypatch.setattr(sif_builder.apt_runner, "run_build", fake_run_build)

    manifest = _manifest(stack="streamlit", entrypoint="streamlit run app.py")
    manifest["source"] = {"subpath": "app/"}
    fetch = _fetch_result(commit="cafef00d", workspace="/srv/upstream/demo")

    result = sif_builder.build_sif(slug="demo-st", manifest=manifest, fetch_result=fetch)

    assert result.action == "built"
    rendered = (sif_builder.SIF_DIR / "demo-st.def").read_text(encoding="utf-8")

    # No unresolved placeholders remain.
    assert "{{" not in rendered
    assert "}}" not in rendered

    # Values came through.
    assert "/srv/upstream/demo" in rendered
    assert "streamlit run app.py" in rendered
    assert "cafef00d" in rendered
    assert "demo-st" in rendered
    assert "app/" in rendered


def test_calls_apt_runner_with_correct_args(
    isolated_dirs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """apt_runner.run_build is called with the resolved sif/def paths and fakeroot+force."""
    captured: dict[str, Any] = {}

    def fake_run_build(*, sif_out, def_in, fakeroot, force, **kwargs):
        captured["sif_out"] = Path(sif_out)
        captured["def_in"] = Path(def_in)
        captured["fakeroot"] = fakeroot
        captured["force"] = force
        captured["kwargs"] = kwargs
        Path(sif_out).write_bytes(b"fake")
        return subprocess.CompletedProcess(args=["build"], returncode=0)

    monkeypatch.setattr(sif_builder.apt_runner, "run_build", fake_run_build)

    result = sif_builder.build_sif(
        slug="demo-fa",
        manifest=_manifest(stack="fastapi", entrypoint="uvicorn app:app"),
        fetch_result=_fetch_result(commit="abc123"),
    )

    assert result.action == "built"
    assert captured["sif_out"] == sif_builder.SIF_DIR / "demo-fa.sif"
    assert captured["def_in"] == sif_builder.SIF_DIR / "demo-fa.def"
    assert captured["fakeroot"] is True
    assert captured["force"] is True
    # stdout must be a writable file handle so logs land on disk.
    assert "stdout" in captured["kwargs"]
    assert captured["kwargs"].get("stderr") == subprocess.STDOUT


def test_failure_returns_log_tail(
    isolated_dirs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CalledProcessError from apt_runner → action='failed' with log tail in error."""
    failing_marker = "FATAL: missing-dep XYZ123"

    def fake_run_build(*, sif_out, def_in, fakeroot, force, **kwargs):
        # Simulate apptainer writing some output then failing.
        log_fh = kwargs.get("stdout")
        if log_fh is not None:
            log_fh.write((failing_marker + "\n").encode("utf-8"))
            log_fh.flush()
        raise subprocess.CalledProcessError(
            returncode=255, cmd=["apptainer", "build", str(sif_out), str(def_in)]
        )

    monkeypatch.setattr(sif_builder.apt_runner, "run_build", fake_run_build)

    result = sif_builder.build_sif(
        slug="demo-fail",
        manifest=_manifest(stack="flask"),
        fetch_result=_fetch_result(),
    )

    assert result.action == "failed"
    assert result.sif is None
    assert result.hash is not None  # we know the cache key we tried
    assert "exit=255" in (result.error or "")
    assert failing_marker in (result.error or "")
    # And no sentinel was written.
    assert not (sif_builder.SIF_DIR / "demo-fail.sif.hash").exists()
