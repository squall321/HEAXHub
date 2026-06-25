"""Unit tests for ``integration_sif_builder.build_sif``.

The tests don't actually invoke apptainer — ``apt_runner.run_build`` is
monkeypatched so we can assert on its call arguments and simulate
success / failure paths. ``var/sifs`` and ``var/logs`` are pointed at
``tmp_path`` so the test suite never writes outside the sandbox.
"""
from __future__ import annotations

import json
import shlex
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
    # Build targets a temp path; os.replace swaps it onto the final SIF.
    assert captured["sif_out"] == sif_builder.SIF_DIR / "demo-fa.sif.building"
    assert result.sif == sif_builder.SIF_DIR / "demo-fa.sif"
    assert (sif_builder.SIF_DIR / "demo-fa.sif").exists()
    assert not (sif_builder.SIF_DIR / "demo-fa.sif.building").exists()
    assert captured["def_in"] == sif_builder.SIF_DIR / "demo-fa.def"
    assert captured["fakeroot"] is True
    # force is False: we always build into a fresh temp, never overwrite live.
    assert captured["force"] is False
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
    assert result.log_path is not None
    # And no sentinel was written.
    assert not (sif_builder.SIF_DIR / "demo-fail.sif.hash").exists()


def test_failure_preserves_existing_sif(
    isolated_dirs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A build failure must NOT clobber the last-good SIF (atomic swap)."""
    # Pre-existing good SIF from a previous build. No sentinel → forces rebuild.
    good_sif = sif_builder.SIF_DIR / "demo-keep.sif"
    good_sif.write_bytes(b"LAST-GOOD-IMAGE")

    def fake_run_build(*, sif_out, def_in, fakeroot, force, **kwargs):
        # Write a partial temp image, then fail — mimics apptainer aborting.
        Path(sif_out).write_bytes(b"PARTIAL-GARBAGE")
        raise subprocess.CalledProcessError(
            returncode=1, cmd=["apptainer", "build", str(sif_out)]
        )

    monkeypatch.setattr(sif_builder.apt_runner, "run_build", fake_run_build)

    result = sif_builder.build_sif(
        slug="demo-keep",
        manifest=_manifest(stack="flask"),
        fetch_result=_fetch_result(),
    )

    assert result.action == "failed"
    # Old image is intact and the temp file was discarded.
    assert good_sif.read_bytes() == b"LAST-GOOD-IMAGE"
    assert not (sif_builder.SIF_DIR / "demo-keep.sif.building").exists()


# ---------------------------------------------------------------------------
# _inject_build_env (빌드 시점 사내 미러 주입) — RCE 회귀 방지 포함
# ---------------------------------------------------------------------------


def test_inject_build_env_noop_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """BUILD_* 가 하나도 없으면 원문 그대로(완전 no-op)."""
    for k in sif_builder._BUILD_ENV_MAP:
        monkeypatch.delenv(k, raising=False)
    src = "Bootstrap: x\n%post\n    pip install -e .\n"
    assert sif_builder._inject_build_env(src) == src


def test_inject_build_env_injects_every_post_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """멀티스테이지(.def 에 %post 가 여러 개)면 모든 블록에 주입된다."""
    for k in sif_builder._BUILD_ENV_MAP:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BUILD_GOPROXY", "http://gp,direct")
    src = "Bootstrap: a\n%post\n    go build\n\nBootstrap: b\n%post\n    echo hi\n"
    out = sif_builder._inject_build_env(src)
    assert out.count("export GOPROXY=") == 2


def test_inject_build_env_is_shell_injection_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """값에 $()/백틱이 있어도 빌드 시 셸로 실행되지 않아야 한다(shlex.quote)."""
    for k in sif_builder._BUILD_ENV_MAP:
        monkeypatch.delenv(k, raising=False)
    payload = "$(touch PWNED)`touch PWNED2`"
    monkeypatch.setenv("BUILD_GOPROXY", payload)
    out = sif_builder._inject_build_env("Bootstrap: x\n%post\n    echo hi\n")
    line = next(l for l in out.splitlines() if "export GOPROXY=" in l).strip()
    # 작은따옴표 리터럴이라야 한다.
    assert line == "export GOPROXY=" + shlex.quote(payload)
    # 실제 셸로 평가해도 PWNED 파일이 생기지 않아야 한다.
    import os
    import tempfile

    d = tempfile.mkdtemp()
    subprocess.run(["/bin/sh", "-c", line], cwd=d)
    assert not (Path(d) / "PWNED").exists()
    assert not (Path(d) / "PWNED2").exists()


def test_parse_pip_missing_records_and_dedups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """빌드 로그의 pip 누락 패키지를 jsonl 에 기록하고 같은 로그 내 중복은 제거한다."""
    monkeypatch.setattr(sif_builder, "_PKG_REQUESTS_FILE", tmp_path / "pkg-requests.jsonl")
    log = tmp_path / "build.log"
    log.write_text(
        "Could not find a version that satisfies the requirement foopkg==1.2 (from x)\n"
        "No matching distribution found for barpkg\n"
        "Could not find a version that satisfies the requirement foopkg==1.2\n",
        encoding="utf-8",
    )
    sif_builder._parse_and_log_pip_missing(log, "demo")
    rows = [
        json.loads(line)
        for line in (tmp_path / "pkg-requests.jsonl").read_text().splitlines()
    ]
    assert sorted(r["package"] for r in rows) == ["barpkg", "foopkg==1.2"]
    assert all(r["slug"] == "demo" for r in rows)
