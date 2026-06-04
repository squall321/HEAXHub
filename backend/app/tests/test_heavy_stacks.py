"""Tests for the heavy-runtime stacks: dotnet_aspnet, java_springboot, rust_actix.

Policy under test
-----------------
HEAXHub deliberately does NOT auto-install the .NET SDK, JDK, or Rust toolchain
— each is multi-hundred-MB and operators on shared hosts have curated installs
they don't want HEAXHub overwriting. So when the host lacks the relevant
toolchain, the builder MUST:

1. Detect the missing tool BEFORE running any subprocess.
2. Return a ``BuildResult`` with ``action == 'failed'`` (never raise).
3. Surface a clear, actionable instruction in ``BuildResult.error`` telling
   the operator exactly which package to install on the host.

We also lock in the launcher's argv composition for each stack so a future
refactor doesn't silently break process spawn.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import integration_builder, integration_launcher
from app.services.stack_resolver import load_stacks, reload_stacks


# ---------------------------------------------------------------------------
# Stack registration sanity
# ---------------------------------------------------------------------------


def test_heavy_stacks_registered() -> None:
    """All three heavy stacks must show up in stacks.yaml so manifests resolve."""
    reload_stacks()
    stacks = load_stacks()
    for name in ("dotnet_aspnet", "java_springboot", "rust_actix"):
        assert name in stacks, f"{name} missing from config/stacks.yaml"
        spec = stacks[name]
        assert spec.launch_mode == "service"
        assert spec.app_type == "web_app"


# ---------------------------------------------------------------------------
# Detect-or-fail: dotnet
# ---------------------------------------------------------------------------


def test_dotnet_missing_sdk_fails_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without `dotnet` on PATH the builder must surface an operator-facing
    install instruction instead of attempting to bootstrap the SDK."""
    ws = tmp_path / "demo-dotnet"
    ws.mkdir()
    (ws / "App.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk.Web">'
        '<PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup>'
        '</Project>'
    )

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(integration_builder, "LOG_DIR", log_dir)
    monkeypatch.setattr(integration_builder.shutil, "which", lambda x: None)
    # Guard: subprocess.run must NOT be called if detection fires first.
    def fake_run(*a, **kw):
        raise AssertionError("subprocess.run must not run when dotnet is absent")
    monkeypatch.setattr(subprocess, "run", fake_run)

    r = integration_builder.build(
        ws, manifest={"build": {"stack": "dotnet_aspnet"}}
    )
    assert r.action == "failed"
    assert r.error is not None
    err = r.error.lower()
    assert "dotnet" in err
    assert ".net 8" in err or "dotnet-sdk-8" in err
    # Must explicitly disclaim auto-install so the operator knows it's manual.
    assert "auto-install" in err or "install" in err


# ---------------------------------------------------------------------------
# Detect-or-fail: java_springboot
# ---------------------------------------------------------------------------


def test_java_missing_mvn_fails_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no ./mvnw AND no system mvn, the builder must explain both paths
    to recovery (commit a wrapper OR install Maven) without running anything."""
    ws = tmp_path / "demo-java"
    ws.mkdir()
    (ws / "pom.xml").write_text(
        '<project><modelVersion>4.0.0</modelVersion>'
        '<groupId>x</groupId><artifactId>app</artifactId>'
        '<version>0.0.1</version></project>'
    )

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(integration_builder, "LOG_DIR", log_dir)

    # java is present (JDK installed) but neither mvnw wrapper nor system mvn exists.
    def which_stub(name: str):
        if name == "java":
            return "/usr/bin/java"
        return None
    monkeypatch.setattr(integration_builder.shutil, "which", which_stub)

    def fake_run(*a, **kw):
        raise AssertionError("subprocess.run must not run when mvn is absent")
    monkeypatch.setattr(subprocess, "run", fake_run)

    r = integration_builder.build(
        ws, manifest={"build": {"stack": "java_springboot"}}
    )
    assert r.action == "failed"
    assert r.error is not None
    err = r.error.lower()
    assert "mvn" in err
    # Operator should learn both fixes: commit ./mvnw OR install Maven.
    assert "mvnw" in err
    assert "maven" in err


def test_java_missing_jdk_fails_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even with ./mvnw committed, no JDK on PATH must short-circuit."""
    ws = tmp_path / "demo-java-nojdk"
    ws.mkdir()
    (ws / "pom.xml").write_text(
        '<project><modelVersion>4.0.0</modelVersion>'
        '<groupId>x</groupId><artifactId>app</artifactId>'
        '<version>0.0.1</version></project>'
    )
    (ws / "mvnw").write_text("#!/bin/sh\nexit 0\n")

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(integration_builder, "LOG_DIR", log_dir)
    monkeypatch.setattr(integration_builder.shutil, "which", lambda x: None)

    def fake_run(*a, **kw):
        raise AssertionError("subprocess.run must not run when java is absent")
    monkeypatch.setattr(subprocess, "run", fake_run)

    r = integration_builder.build(
        ws, manifest={"build": {"stack": "java_springboot"}}
    )
    assert r.action == "failed"
    err = (r.error or "").lower()
    assert "java" in err
    assert "jdk 17" in err or "jdk" in err


# ---------------------------------------------------------------------------
# Detect-or-fail: rust_actix
# ---------------------------------------------------------------------------


def test_rust_missing_cargo_fails_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without cargo on PATH the builder must say so + recommend rustup,
    not silently attempt to download a toolchain tarball."""
    ws = tmp_path / "demo-rust"
    ws.mkdir()
    (ws / "Cargo.toml").write_text(
        '[package]\nname = "server"\nversion = "0.1.0"\nedition = "2021"\n'
    )

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(integration_builder, "LOG_DIR", log_dir)
    monkeypatch.setattr(integration_builder.shutil, "which", lambda x: None)

    def fake_run(*a, **kw):
        raise AssertionError("subprocess.run must not run when cargo is absent")
    monkeypatch.setattr(subprocess, "run", fake_run)

    r = integration_builder.build(
        ws, manifest={"build": {"stack": "rust_actix"}}
    )
    assert r.action == "failed"
    err = (r.error or "").lower()
    assert "cargo" in err
    assert "rustup" in err or "rust" in err


# ---------------------------------------------------------------------------
# Build-runs path: confirm the dispatcher invokes the right shell command
# when the toolchain IS present.
# ---------------------------------------------------------------------------


def test_dotnet_invokes_publish_when_sdk_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "demo-dotnet-ok"
    ws.mkdir()
    (ws / "App.csproj").write_text("<Project />")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(integration_builder, "LOG_DIR", log_dir)
    monkeypatch.setattr(
        integration_builder.shutil, "which",
        lambda x: "/usr/bin/dotnet" if x == "dotnet" else None,
    )

    calls: list[list[str]] = []
    def fake_run(cmd, *, cwd, check, timeout, capture_output, env=None):
        calls.append(cmd)
        # Simulate publish/ output so the sentinel check on the next build skips.
        (ws / "publish").mkdir(exist_ok=True)
        (ws / "publish" / "App.dll").write_bytes(b"\x00\x00")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    monkeypatch.setattr(subprocess, "run", fake_run)

    r = integration_builder.build(
        ws, manifest={"build": {"stack": "dotnet_aspnet"}}
    )
    assert r.action == "built", r.error
    joined = " ".join(" ".join(c) for c in calls)
    assert "dotnet publish" in joined


def test_rust_invokes_cargo_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "demo-rust-ok"
    ws.mkdir()
    (ws / "Cargo.toml").write_text(
        '[package]\nname = "server"\nversion = "0.1.0"\nedition = "2021"\n'
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(integration_builder, "LOG_DIR", log_dir)
    monkeypatch.setattr(
        integration_builder.shutil, "which",
        lambda x: "/usr/bin/cargo" if x == "cargo" else None,
    )

    calls: list[list[str]] = []
    def fake_run(cmd, *, cwd, check, timeout, capture_output, env=None):
        calls.append(cmd)
        # Simulate target/release/server exec bit so the sentinel sticks.
        rel = ws / "target" / "release"
        rel.mkdir(parents=True, exist_ok=True)
        bin_ = rel / "server"
        bin_.write_bytes(b"\x7fELF")
        bin_.chmod(0o755)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    monkeypatch.setattr(subprocess, "run", fake_run)

    r = integration_builder.build(
        ws, manifest={"build": {"stack": "rust_actix"}}
    )
    assert r.action == "built", r.error
    joined = " ".join(" ".join(c) for c in calls)
    assert "cargo build --release" in joined


# ---------------------------------------------------------------------------
# Launcher argv composition (no process spawn — purely string-level)
# ---------------------------------------------------------------------------


def _spec_for(stack_name: str):
    return load_stacks()[stack_name]


def test_dotnet_launcher_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path / "demo-dn"
    publish = ws / "publish"
    publish.mkdir(parents=True)
    dll = publish / "App.dll"
    dll.write_bytes(b"\x00")
    monkeypatch.setattr(
        integration_launcher.shutil, "which",
        lambda x: "/usr/bin/dotnet" if x == "dotnet" else None,
    )
    argv = integration_launcher._argv_for(
        ws, _spec_for("dotnet_aspnet"),
        {"build": {"stack": "dotnet_aspnet"}},
        port=12345, base_path="/apps/demo_dn",
    )
    assert argv[0] == "/usr/bin/dotnet"
    assert str(dll) in argv
    assert "--urls" in argv
    assert "http://0.0.0.0:12345" in argv


def test_java_launcher_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path / "demo-jv"
    target = ws / "target"
    target.mkdir(parents=True)
    fat_jar = target / "app-0.1.0.jar"
    fat_jar.write_bytes(b"PK")
    # Add a sources jar that must NOT be picked as the entry artefact.
    (target / "app-0.1.0-sources.jar").write_bytes(b"PK")
    monkeypatch.setattr(
        integration_launcher.shutil, "which",
        lambda x: "/usr/bin/java" if x == "java" else None,
    )
    argv = integration_launcher._argv_for(
        ws, _spec_for("java_springboot"),
        {"build": {"stack": "java_springboot"}},
        port=9876, base_path="/apps/demo_jv",
    )
    assert argv[:2] == ["/usr/bin/java", "-jar"]
    assert str(fat_jar) in argv
    assert "--server.port=9876" in argv
    # Sources jar must not be the picked entry.
    assert "sources" not in argv[2]


def test_rust_launcher_argv(tmp_path: Path) -> None:
    ws = tmp_path / "demo-rs"
    release = ws / "target" / "release"
    release.mkdir(parents=True)
    bin_ = release / "server"
    bin_.write_bytes(b"\x7fELF")
    bin_.chmod(0o755)
    # Build artefact that must NOT be executed.
    (release / "server.d").write_bytes(b"")
    argv = integration_launcher._argv_for(
        ws, _spec_for("rust_actix"),
        {"build": {"stack": "rust_actix"}},
        port=4242, base_path="/apps/demo_rs",
    )
    assert argv == [str(bin_)]


# ---------------------------------------------------------------------------
# Caddy prefix policy: heavy stacks are NOT prefix-aware so Caddy strips
# /apps/<slug> before proxying. Asserted via the module-level constant.
# ---------------------------------------------------------------------------


def test_heavy_stacks_strip_prefix() -> None:
    for name in ("dotnet_aspnet", "java_springboot", "rust_actix"):
        assert name not in integration_launcher._PREFIX_AWARE_STACKS, (
            f"{name} must let Caddy strip /apps/<slug> — the heavy frameworks "
            "don't know their sub-path and would 404 on prefixed URLs."
        )
