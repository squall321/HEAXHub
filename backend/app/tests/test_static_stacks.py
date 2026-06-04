"""Tests for the non-build static stacks (static_html, mkdocs_static).

These stacks have no install/compile step: the builder just validates that
the configured ``static_root`` exists and contains the configured index
file, and the launcher asks Caddy to file_server it instead of spawning a
process.

We do NOT hit a live Caddy here — :mod:`proxy_manager` is monkeypatched so
the tests remain hermetic and fast.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import integration_builder, integration_launcher, stack_resolver


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _seed_site(workspace: Path, root: str = "public") -> Path:
    """Create ``workspace/<root>/index.html`` with a tiny body."""
    workspace.mkdir(parents=True, exist_ok=True)
    static_root = workspace / root if root not in (".", "") else workspace
    static_root.mkdir(parents=True, exist_ok=True)
    (static_root / "index.html").write_text(
        "<!doctype html><title>t</title><p>hi</p>", encoding="utf-8"
    )
    return static_root


def test_builder_static_html_verifies_root_and_writes_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A well-formed static_html workspace builds without invoking subprocess
    and leaves a sentinel hash so the second build is a no-op."""
    ws = tmp_path / "demo-static"
    _seed_site(ws, root="public")

    monkeypatch.setattr(integration_builder, "LOG_DIR", tmp_path / "logs")

    r1 = integration_builder.build(
        ws, manifest={"build": {"stack": "static_html"}}
    )
    assert r1.action == "built", r1.error
    assert (ws / ".heaxhub_build_ok").exists()

    # Second run with the same content → "skipped" (sentinel hash matches).
    r2 = integration_builder.build(
        ws, manifest={"build": {"stack": "static_html"}}
    )
    assert r2.action == "skipped", r2.error


def test_builder_static_html_missing_root_fails_with_clear_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the configured static root is absent, the builder must fail with a
    clear, operator-readable error — not silently succeed and leave Caddy
    serving 404s."""
    ws = tmp_path / "demo-empty"
    ws.mkdir()
    # No public/ directory at all.

    monkeypatch.setattr(integration_builder, "LOG_DIR", tmp_path / "logs")
    r = integration_builder.build(
        ws, manifest={"build": {"stack": "static_html"}}
    )
    assert r.action == "failed"
    assert "static_root" in (r.error or "")


def test_builder_static_html_missing_index_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directory exists but no index.html → fail (Caddy would otherwise serve
    a directory listing)."""
    ws = tmp_path / "demo-noindex"
    (ws / "public").mkdir(parents=True)
    (ws / "public" / "about.html").write_text("hi", encoding="utf-8")

    monkeypatch.setattr(integration_builder, "LOG_DIR", tmp_path / "logs")
    r = integration_builder.build(
        ws, manifest={"build": {"stack": "static_html"}}
    )
    assert r.action == "failed"
    assert "index" in (r.error or "").lower()


def test_builder_static_html_accepts_root_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manifest ``build.root: .`` (the shorter alias used in demos) must work
    just like ``build.static_root: .``."""
    ws = tmp_path / "demo-root-alias"
    _seed_site(ws, root=".")
    monkeypatch.setattr(integration_builder, "LOG_DIR", tmp_path / "logs")

    r = integration_builder.build(
        ws,
        manifest={"build": {"stack": "static_html", "root": "."}},
    )
    assert r.action == "built", r.error


def test_builder_mkdocs_static_uses_site_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``mkdocs_static`` defaults ``static_root`` to ``site/`` — confirm the
    builder picks that up from the stack spec without manifest override."""
    ws = tmp_path / "demo-mkdocs"
    _seed_site(ws, root="site")

    monkeypatch.setattr(integration_builder, "LOG_DIR", tmp_path / "logs")
    r = integration_builder.build(
        ws, manifest={"build": {"stack": "mkdocs_static"}}
    )
    assert r.action == "built", r.error


# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------


def test_launcher_static_registers_file_server_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``launch.mode: static`` must skip port allocation and call
    ``proxy_manager.register_static_route`` with the absolute on-disk root."""
    ws = tmp_path / "demo-static"
    _seed_site(ws, root="public")

    calls: list[dict] = []

    class _Stub:
        @staticmethod
        def register_static_route(*, app_id, root_path, base_path, index_file):
            calls.append({
                "app_id": app_id, "root_path": root_path,
                "base_path": base_path, "index_file": index_file,
            })
            return SimpleNamespace(ok=True, reason=None)

    monkeypatch.setattr(integration_launcher, "proxy_manager", _Stub)

    manifest = {
        "id": "demo_static",
        "launch": {"mode": "static"},
        "build": {"stack": "static_html"},
    }
    r = integration_launcher.launch(ws, manifest=manifest, db=None)
    assert r.action == "started"
    assert r.port is None, "static stacks must not allocate a port"
    assert r.base_path == "/apps/demo_static"
    assert len(calls) == 1
    call = calls[0]
    assert call["app_id"] == "demo_static"
    assert call["base_path"] == "/apps/demo_static"
    assert call["index_file"] == "index.html"
    # root must be an absolute path Caddy can read.
    assert Path(call["root_path"]).is_absolute()
    assert Path(call["root_path"]) == (ws / "public").resolve()


def test_launcher_static_missing_root_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the static_root vanished between build and launch, the launcher
    must report a clear failure instead of registering a 404-serving route."""
    ws = tmp_path / "demo-gone"
    ws.mkdir()  # no public/

    register_calls: list = []

    class _Stub:
        @staticmethod
        def register_static_route(**kwargs):
            register_calls.append(kwargs)
            return SimpleNamespace(ok=True)

    monkeypatch.setattr(integration_launcher, "proxy_manager", _Stub)

    manifest = {
        "id": "demo_gone",
        "launch": {"mode": "static"},
        "build": {"stack": "static_html"},
    }
    r = integration_launcher.launch(ws, manifest=manifest, db=None)
    assert r.action == "failed"
    assert "static_root" in (r.error or "")
    assert register_calls == [], "must not touch Caddy on local validation fail"


def test_launcher_static_caddy_unreachable_surfaces_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``proxy_manager.register_static_route`` with ``ok=False`` must turn
    into a LaunchResult.action == 'failed' with the underlying reason."""
    ws = tmp_path / "demo-caddy-down"
    _seed_site(ws, root="public")

    class _Stub:
        @staticmethod
        def register_static_route(**kwargs):
            return SimpleNamespace(ok=False, reason="caddy unreachable: ECONNREFUSED")

    monkeypatch.setattr(integration_launcher, "proxy_manager", _Stub)

    r = integration_launcher.launch(
        ws,
        manifest={
            "id": "demo_caddy_down",
            "launch": {"mode": "static"},
            "build": {"stack": "static_html"},
        },
        db=None,
    )
    assert r.action == "failed"
    assert "caddy" in (r.error or "").lower()


# ---------------------------------------------------------------------------
# Stack resolver sanity (catches accidental YAML removal)
# ---------------------------------------------------------------------------


def test_stack_resolver_knows_static_html_and_mkdocs() -> None:
    """``config/stacks.yaml`` must list the two static stacks. If a future
    refactor drops them, this test trips before the launcher does."""
    stack_resolver.reload_stacks()
    stacks = stack_resolver.load_stacks()
    assert "static_html" in stacks
    assert "mkdocs_static" in stacks
    assert stacks["static_html"].runtime == "caddy_static"
    assert stacks["static_html"].launch_mode == "static"
    assert stacks["mkdocs_static"].extra.get("static_root") == "site"


# ---------------------------------------------------------------------------
# Caddy route builder (proxy_manager)
# ---------------------------------------------------------------------------


def test_proxy_manager_static_route_shape() -> None:
    """Lock in the Caddy route JSON shape so a Caddy admin upgrade doesn't
    silently break us — the handler chain MUST be rewrite + file_server."""
    from app.services.proxy_manager import _build_static_route

    route = _build_static_route(
        "demo_static", "/var/heax/apps/demo_static/public", "/apps/demo_static",
        index_file="index.html",
    )
    assert route["@id"] == "app-demo_static"
    assert route["match"][0]["path"] == ["/apps/demo_static", "/apps/demo_static/*"]
    handle_chain = route["handle"][0]["routes"][0]["handle"]
    assert handle_chain[0]["handler"] == "rewrite"
    assert handle_chain[0]["strip_path_prefix"] == "/apps/demo_static"
    assert handle_chain[1]["handler"] == "file_server"
    assert handle_chain[1]["root"] == "/var/heax/apps/demo_static/public"
    assert handle_chain[1]["index_names"] == ["index.html"]
