# launch.forward_authorization: false 앱은 게이트를 지난 뒤 업스트림 요청에서 Authorization 을 지운다 — 기본값은 지금 모양 그대로다
"""Authorization 은 허브 게이트가 보는 것이지 앱이 볼 것이 아니다(사용자 결정 2026-11-01 — WDA 에는 넘기지 않게).

그 헤더의 토큰(포털 PAT · 허브 PAT · 게이트웨이 서비스 PAT)은 사람이 볼 수 있는 앱 전부에 통한다. 앱이 그것을 남기거나
털리면 다른 앱의 열쇠가 된다. 끈 앱은 업스트림 reverse_proxy 의 요청 헤더에서만 지운다 — forward_auth 서브요청(원 요청의
사본)은 그 전에 판정을 마치고, 신원은 검증된 X-Heax-User-* 로 그대로 간다.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.services import apt_runner, integration_launcher
from app.services.proxy_manager import (
    _IDENTITY_HEADERS,
    _build_external_proxy_route,
    _build_route,
    _build_static_route,
)

REPO = Path(__file__).resolve().parents[3]


def _chain(route: dict) -> list[dict]:
    return route["handle"][0]["routes"][0]["handle"]


# ─── 라우트 모양 ────────────────────────────────────────────────────────────


def test_내부_라우트는_기본값이면_지금처럼_넘긴다():
    upstream = _chain(_build_route("app_x", 9301, None))[-1]
    assert upstream["handler"] == "reverse_proxy"
    assert "headers" not in upstream, "기본값에서 모양이 바뀌면 다른 앱의 라우트가 모두 바뀐다"


@pytest.mark.parametrize("strip_prefix, handlers", [
    (True, ["reverse_proxy", "rewrite", "reverse_proxy"]),
    (False, ["reverse_proxy", "reverse_proxy"]),
])
def test_내부_라우트는_끄면_게이트_뒤_업스트림_요청에서만_지운다(strip_prefix, handlers):
    chain = _chain(_build_route("app_x", 9301, None, strip_prefix=strip_prefix, forward_authorization=False))
    assert [h["handler"] for h in chain] == handlers
    gate, upstream = chain[0], chain[-1]
    # 게이트는 그대로 그 헤더를 받아 판정한다 — 게이트 서브요청에서 지우면 토큰 사용자가 모두 401 이다.
    assert gate["rewrite"]["uri"] == "/api/v1/authz"
    assert "Authorization" not in gate["headers"]["request"].get("delete", [])
    assert gate["handle_response"][0]["match"]["status_code"] == [2]
    assert upstream["upstreams"] == [{"dial": "127.0.0.1:9301"}]
    assert upstream["headers"] == {"request": {"delete": ["Authorization"]}}
    # 신원 복사는 그대로다 — 앱은 검증된 X-Heax-User-* 로 사람을 안다.
    copied = {h for r in gate["handle_response"][0]["routes"] for h in r["handle"][0]["request"].get("set", {})}
    assert copied == set(_IDENTITY_HEADERS)


def test_외부_업스트림도_같은_손잡이다():
    on = _chain(_build_external_proxy_route("ext", "https://h.example:8443/", None, portal_auth=True))
    off = _chain(_build_external_proxy_route("ext", "https://h.example:8443/", None, portal_auth=True,
                                             forward_authorization=False))
    assert "delete" not in on[-1]["headers"]["request"]
    assert off[-1]["headers"]["request"]["delete"] == ["Authorization"]
    # 지우는 것 말고는 같다 — Host · X-Forwarded-Prefix · 게이트웨이 시크릿 · TLS · 게이트.
    assert off[-1]["headers"]["request"]["set"] == on[-1]["headers"]["request"]["set"]
    assert off[-1]["transport"] == on[-1]["transport"]
    assert off[:-1] == on[:-1]


def test_정적_라우트에는_넘길_업스트림이_없다():
    assert _chain(_build_static_route("st", "/srv/st", None))[-1]["handler"] == "file_server"


# ─── 매니페스트 읽기 ────────────────────────────────────────────────────────


@pytest.mark.parametrize("text, expected", [
    ("launch: {mode: service}", True),
    ("launch: {mode: service, forward_authorization: true}", True),
    ("launch: {mode: service, forward_authorization: false}", False),
    ("launch:\n  mode: service\n  forward_authorization: false\n", False),
    ("launch: null", True),
    ("{}", True),
])
def test_매니페스트의_forward_authorization_을_읽는다(text, expected):
    assert integration_launcher._forward_authorization(yaml.safe_load(text)) is expected


def test_기본값이면_새_인자를_싣지_않는다():
    """워커 부모는 proxy_manager 를 기동 때 읽고 런처는 작업 안에서 늦게 읽는다 — 코드를 받은 뒤 워커를 다시 띄우기 전의
    자식은 새 런처 + 옛 proxy_manager 다. 기본값에 인자를 실으면 그 사이 모든 앱의 라우트 등록이 TypeError 로 실패한다."""
    assert integration_launcher._route_auth_kwargs(True) == {}
    assert integration_launcher._route_auth_kwargs(False) == {"forward_authorization": False}


def test_WDA_는_끈다():
    """사용자 결정 2026-11-01 — WDA 에는 Authorization 을 넘기지 않는다(포털 '발표자료로' 가 그 사람의 포털 PAT 로 부른다)."""
    manifest = yaml.safe_load((REPO / "integrations/web-design-agents/.portal/manifest.yaml").read_text(encoding="utf-8"))
    assert integration_launcher._forward_authorization(manifest) is False


# ─── 런처가 라우트를 걸 때 ───────────────────────────────────────────────────


class _CaptureRoutes:
    """옛 proxy_manager 와 같은 꼴의 대역 — 받은 인자를 그대로 남긴다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def register_app_route(self, **kw):
        self.calls.append(("app", kw))
        return SimpleNamespace(ok=True, payload=None)

    def register_external_proxy_route(self, **kw):
        self.calls.append(("ext", kw))
        return SimpleNamespace(ok=True, payload=None)


@pytest.mark.parametrize("launch, expected", [({}, "absent"), ({"forward_authorization": False}, False)])
def test_떠_있는_서비스_앱의_라우트를_다시_걸_때_따른다(tmp_path, monkeypatch, launch, expected):
    cap = _CaptureRoutes()
    monkeypatch.setattr(integration_launcher, "proxy_manager", cap)
    monkeypatch.setattr(integration_launcher, "_is_alive", lambda pid: True)
    monkeypatch.setattr(integration_launcher, "_is_healthy", lambda *a, **k: True)
    integration_launcher._write_state("svc_x", {"pid": os.getpid(), "port": 9399, "base_path": "/apps/svc_x",
                                                "caddy_registered": True})
    ws = tmp_path / "svc-x"
    ws.mkdir()
    manifest = {"id": "svc_x", "build": {"stack": "fastapi"}, "launch": {"mode": "service", **launch}}
    r = integration_launcher.launch(ws, manifest=manifest, db=None)
    assert r.action == "already_running"
    [(kind, kw)] = cap.calls
    assert kind == "app" and kw["port"] == 9399 and kw["strip_prefix"] is True
    assert kw.get("forward_authorization", "absent") == expected


def test_SIF_인스턴스_재사용_길도_따른다(tmp_path, monkeypatch):
    cap = _CaptureRoutes()
    monkeypatch.setattr(integration_launcher, "proxy_manager", cap)
    monkeypatch.setattr(integration_launcher, "_is_alive", lambda pid: True)
    monkeypatch.setattr(integration_launcher, "_is_healthy", lambda *a, **k: True)
    monkeypatch.setattr(apt_runner, "instance_list", lambda: ["heax_app_svc_y"])
    integration_launcher._write_state("svc_y", {"pid": os.getpid(), "port": 9398, "base_path": "/apps/svc_y",
                                                "instance_name": "heax_app_svc_y", "caddy_registered": True})
    manifest = {"id": "svc_y", "build": {"stack": "fastapi"},
                "launch": {"mode": "service", "forward_authorization": False}}
    r = integration_launcher._launch_via_sif(
        workspace=tmp_path, slug="svc-y", canonical="svc_y", spec=None, manifest=manifest,
        stack_name="fastapi", base_path="/apps/svc_y", health_path="/health",
        sif_path=tmp_path / "svc-y.sif", db=None)
    assert r.action == "already_running"
    [(kind, kw)] = cap.calls
    assert kind == "app" and kw.get("forward_authorization") is False


@pytest.mark.parametrize("launch, expected", [({}, "absent"), ({"forward_authorization": False}, False)])
def test_proxy_앱의_라우트도_따른다(tmp_path, monkeypatch, launch, expected):
    cap = _CaptureRoutes()
    monkeypatch.setattr(integration_launcher, "proxy_manager", cap)
    ws = tmp_path / "ext-x"
    ws.mkdir()
    manifest = {"id": "ext_x", "build": {"stack": "external_proxy"},
                "launch": {"mode": "proxy", "upstream": "http://127.0.0.1:9", "portal_auth": True, **launch}}
    r = integration_launcher.launch(ws, manifest=manifest, db=None)
    assert r.action == "started"
    [(kind, kw)] = cap.calls
    assert kind == "ext" and kw["portal_auth"] is True
    assert kw.get("forward_authorization", "absent") == expected


def test_런처의_라우트_등록은_모두_이_값을_따른다():
    """새 등록 자리가 생기면 이 값을 빠뜨리기 쉽다 — 끈 앱이 그 길에서만 토큰을 받게 된다."""
    tree = ast.parse((REPO / "backend/app/services/integration_launcher.py").read_text(encoding="utf-8"))
    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if name not in ("_safe_register_caddy", "register_app_route", "register_external_proxy_route"):
            continue
        passes = any(k.arg == "forward_authorization" or k.arg is None for k in node.keywords)
        if not passes:
            missing.append(f"{name} (줄 {node.lineno})")
    assert not missing, f"forward_authorization 을 넘기지 않는 라우트 등록: {missing}"
