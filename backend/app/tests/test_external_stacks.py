"""Tests for external (non-build) stacks: external_link, external_iframe, external_proxy.

These stacks do NOT spawn processes; the builder is a no-op and the launcher
either short-circuits (url/iframe) or only registers a Caddy reverse-proxy
route pointing at an external upstream URL (proxy).

Locked-in behavior:
  - url / iframe modes: launcher returns ``skipped`` WITHOUT allocating a port
    and WITHOUT calling proxy_manager.
  - proxy mode: launcher calls ``proxy_manager.register_external_proxy_route``
    with the upstream URL parsed from manifest.launch.upstream, and DOES NOT
    allocate a port.
  - The Caddy route built for an external proxy must ``dial`` the upstream
    host:port (not 127.0.0.1) and attach a TLS transport for https upstreams.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import integration_builder, integration_launcher, proxy_manager


# ---------------------------------------------------------------------------
# Builder: external runtime is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stack", ["external_link", "external_iframe", "external_proxy"])
def test_builder_external_stack_is_noop(tmp_path: Path, stack: str) -> None:
    ws = tmp_path / f"ext-{stack}"
    ws.mkdir()
    r = integration_builder.build(ws, manifest={"build": {"stack": stack}})
    # The runtime is 'noop' → we report 'skipped' (no work performed, but not failed).
    assert r.action == "skipped", r
    assert r.stack == stack


# ---------------------------------------------------------------------------
# Launcher: url / iframe do not allocate ports or touch Caddy
# ---------------------------------------------------------------------------


class _NoCallProxy:
    """Drop-in proxy_manager that explodes if anyone calls it.

    Used to assert the url/iframe paths never reach into proxy_manager.
    """

    @staticmethod
    def register_app_route(**kw):
        raise AssertionError(f"register_app_route should not be called: {kw}")

    @staticmethod
    def register_external_proxy_route(**kw):
        raise AssertionError(f"register_external_proxy_route should not be called: {kw}")

    @staticmethod
    def unregister_app_route(**kw):
        raise AssertionError(f"unregister_app_route should not be called: {kw}")


class _NoCallPorts:
    """Drop-in port_allocator that explodes if anyone tries to allocate."""

    @staticmethod
    def allocate_port(db, *, app_id, scope):
        raise AssertionError(
            f"allocate_port should not be called for non-process modes "
            f"(app_id={app_id}, scope={scope})"
        )

    @staticmethod
    def release_port(db, *, port):  # pragma: no cover - defensive
        raise AssertionError(f"release_port should not be called: port={port}")


def test_external_link_launch_skips_port_alloc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ext-link"
    ws.mkdir()

    monkeypatch.setattr(integration_launcher, "proxy_manager", _NoCallProxy)
    monkeypatch.setattr(integration_launcher, "port_allocator", _NoCallPorts)

    manifest = {
        "id": "ext_link",
        "build": {"stack": "external_link"},
        "launch": {"mode": "url", "url": "https://example.com/docs"},
    }
    r = integration_launcher.launch(ws, manifest=manifest, db=None)
    assert r.action == "skipped"
    assert r.port is None
    assert r.pid is None
    assert r.base_path == "/apps/ext_link"


def test_external_iframe_launch_skips_port_alloc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ext-iframe"
    ws.mkdir()

    monkeypatch.setattr(integration_launcher, "proxy_manager", _NoCallProxy)
    monkeypatch.setattr(integration_launcher, "port_allocator", _NoCallPorts)

    manifest = {
        "id": "ext_iframe",
        "build": {"stack": "external_iframe"},
        "launch": {
            "mode": "iframe",
            "url": "https://grafana.internal/d/abc",
            "iframe": {"sandbox": "allow-scripts allow-same-origin"},
        },
    }
    r = integration_launcher.launch(ws, manifest=manifest, db=None)
    assert r.action == "skipped"
    assert r.port is None
    assert r.pid is None
    assert r.base_path == "/apps/ext_iframe"


# ---------------------------------------------------------------------------
# Launcher: proxy mode registers a Caddy route with the upstream dial
# ---------------------------------------------------------------------------


def test_external_proxy_registers_route_with_upstream_dial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ext-proxy"
    ws.mkdir()

    calls: list[dict] = []

    class _CaptureProxy:
        @staticmethod
        def register_external_proxy_route(*, app_id, upstream_url, base_path, strip_prefix=True):
            calls.append({
                "app_id": app_id,
                "upstream_url": upstream_url,
                "base_path": base_path,
                "strip_prefix": strip_prefix,
            })
            return SimpleNamespace(ok=True, payload=None)

    monkeypatch.setattr(integration_launcher, "proxy_manager", _CaptureProxy)
    monkeypatch.setattr(integration_launcher, "port_allocator", _NoCallPorts)

    manifest = {
        "id": "ext_proxy",
        "build": {"stack": "external_proxy"},
        "launch": {
            "mode": "proxy",
            "upstream": "https://internal-tool.corp:8443/",
            "strip_prefix": True,
        },
    }
    r = integration_launcher.launch(ws, manifest=manifest, db=None)
    assert r.action == "started"
    assert r.port is None  # no port allocated for proxy mode
    assert r.base_path == "/apps/ext_proxy"
    assert calls and calls[0]["app_id"] == "ext_proxy"
    assert calls[0]["upstream_url"] == "https://internal-tool.corp:8443/"
    assert calls[0]["base_path"] == "/apps/ext_proxy"
    assert calls[0]["strip_prefix"] is True


def test_external_proxy_missing_upstream_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """proxy mode without a launch.upstream URL must fail cleanly, not Popen."""
    ws = tmp_path / "ext-proxy-bad"
    ws.mkdir()
    monkeypatch.setattr(integration_launcher, "proxy_manager", _NoCallProxy)
    monkeypatch.setattr(integration_launcher, "port_allocator", _NoCallPorts)

    r = integration_launcher.launch(
        ws,
        manifest={
            "id": "ext_proxy_bad",
            "build": {"stack": "external_proxy"},
            "launch": {"mode": "proxy"},  # missing upstream
        },
        db=None,
    )
    assert r.action == "failed"
    assert "upstream" in (r.error or "")


# ---------------------------------------------------------------------------
# proxy_manager: external route shape (host:port dial + TLS for https)
# ---------------------------------------------------------------------------


def test_external_proxy_route_builds_host_dial_with_tls() -> None:
    """The Caddy route built for an external https upstream must:
      - dial host:443 (NOT 127.0.0.1)
      - include a TLS transport with the upstream SNI
      - strip the /apps/{id} prefix before forwarding
    """
    route = proxy_manager._build_external_proxy_route(
        "ext_proxy",
        "https://internal-tool.corp:8443/some/path",
        "/apps/ext_proxy",
        strip_prefix=True,
    )
    handle = route["handle"][0]["routes"][0]["handle"]
    # Expect [rewrite, reverse_proxy]
    assert handle[0]["handler"] == "rewrite"
    assert handle[0]["strip_path_prefix"] == "/apps/ext_proxy"
    rp = handle[1]
    assert rp["handler"] == "reverse_proxy"
    assert rp["upstreams"] == [{"dial": "internal-tool.corp:8443"}]
    # TLS transport for https
    assert rp.get("transport", {}).get("protocol") == "http"
    assert rp["transport"]["tls"]["server_name"] == "internal-tool.corp"
    # Host header override so the upstream sees its own hostname
    assert rp["headers"]["request"]["set"]["Host"] == ["internal-tool.corp"]


def test_external_proxy_route_rejects_non_http_scheme() -> None:
    with pytest.raises(ValueError):
        proxy_manager._build_external_proxy_route(
            "x", "ftp://example.com/", "/apps/x"
        )
