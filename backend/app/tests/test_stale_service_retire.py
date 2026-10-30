# 서비스에서 다른 모드로 바뀐 앱이 남긴 heax_app_<id> 를 reconcile 이 내리는지 — 그 앱 것만, 새 라우트 뒤에, 다시 띄우지 않고(HH-16)
#
# 운영 전환(HH-14)은 step_forge 를 service 에서 proxy 로 바꾼다. 예전엔 proxy 갈래가 상태 파일을 proxy 기록으로 덮어
# instance_name 을 잃었고, heax_app_step_forge 는 포트 · 메모리를 쥔 채 남았다(내려가는 길은 stop.sh 가 heax_app_* 를 통째로
# 내릴 때뿐 — dev 처럼 heal.sh · start.sh 로만 허브를 다시 띄우는 박스에서는 영영 남는다). apptainer · Caddy · 포트 배정은 대역이다.
from __future__ import annotations

import json
import logging
import signal
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.services import integration_launcher as L

_DB = object()   # reconcile 이 넘기는 세션 자리 — 포트 돌려주기가 이것을 받는지 본다

_PROXY = {
    "id": "step_forge", "version": "0.1.0", "status": "beta", "app_type": "web_app",
    "execution_target": "external_url", "build": {"stack": "external_proxy"},
    "launch": {"mode": "proxy", "upstream": "http://127.0.0.1:16110", "strip_prefix": True, "portal_auth": True},
    "permissions": {"visibility": "company"}, "mcp": {"expose": True, "path": "/mcp"},
}
_SERVICE = {
    "id": "step_forge", "version": "0.1.0", "build": {"stack": "fastapi"},
    "launch": {"mode": "service", "health_check": {"path": "/health"}},
}


class _Apt:
    """apptainer 대역 — 무엇을 멈췄는지 적고, 띄우려 하면 시험을 깬다."""

    def __init__(self, events: list, rcs: list[tuple[int, str]] | None = None) -> None:
        self.events = events
        self.rcs = list(rcs or [])

    def instance_stop(self, name, **kw):
        self.events.append(("stop", name))
        rc, err = self.rcs.pop(0) if self.rcs else (0, "")
        return SimpleNamespace(returncode=rc, stderr=err, stdout="")

    def instance_list(self, **kw):
        return ["heax_app_step_forge", "heax_app_other_app"]

    def instance_start(self, *a, **kw):
        raise AssertionError("서비스가 아닌 앱을 다시 띄웠다(instance start)")

    def instance_exec(self, *a, **kw):
        raise AssertionError("서비스가 아닌 앱을 다시 띄웠다(instance exec)")


class _Proxy:
    def __init__(self, events: list) -> None:
        self.events = events

    def register_external_proxy_route(self, *, app_id, upstream_url, base_path, strip_prefix=True, portal_auth=False):
        self.events.append(("route", app_id, upstream_url))
        return SimpleNamespace(ok=True)

    def register_app_route(self, *, app_id, port, base_path, strip_prefix=True):
        self.events.append(("route", app_id, port))
        return SimpleNamespace(ok=True)

    def unregister_app_route(self, *, app_id):
        self.events.append(("unroute", app_id))
        return SimpleNamespace(ok=True)


class _Ports:
    def __init__(self, events: list) -> None:
        self.events = events

    def release_port(self, db, port):
        assert db is _DB
        self.events.append(("release", port))

    def allocate_port(self, db, *, app_id, scope):
        raise AssertionError("서비스가 아닌 앱에 포트를 배정했다")


class _Session:
    def __enter__(self):
        return _DB

    def __exit__(self, *a):
        return False


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from app.workers import integration_tasks

    root = tmp_path / "integrations"
    root.mkdir()
    sifs = tmp_path / "sifs"
    sifs.mkdir()
    events: list = []
    apt = _Apt(events)
    monkeypatch.setattr(integration_tasks, "_INTEGRATIONS_ROOT", root)
    monkeypatch.setattr(integration_tasks, "_SIF_DIR", sifs)
    monkeypatch.setattr(integration_tasks, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(L, "apt_runner", apt)
    monkeypatch.setattr(L, "proxy_manager", _Proxy(events))
    monkeypatch.setattr(L, "port_allocator", _Ports(events))

    def write(slug: str, manifest: dict) -> Path:
        d = root / slug / ".portal"
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
        return root / slug

    return SimpleNamespace(root=root, sifs=sifs, events=events, apt=apt, write=write,
                           run=integration_tasks.reconcile_integrations)


def _service_state(canonical: str, *, instance: str | None = None, port: int = 9115, pid: int = 4242) -> dict:
    st = {"schema_version": L._STATE_SCHEMA_VERSION, "slug": canonical, "pid": pid, "port": port,
          "base_path": f"/apps/{canonical}", "health_path": "/health", "stack": "fastapi",
          "argv": ["/x/var/sifs/app.sif", "uvicorn", "app.main:app"], "caddy_registered": True}
    if instance is not None:
        st.update(instance_name=instance, sif_path="/x/var/sifs/app.sif")
    return st


def test_service_에서_proxy_로_바뀌면_새_라우트를_건_뒤_그_앱의_인스턴스를_내린다(env, caplog):
    L._write_state("step_forge", _service_state("step_forge", instance="heax_app_step_forge"))
    env.write("step_forge", _PROXY)
    (env.sifs / "step_forge.sif").write_bytes(b"old-sif")      # 전에 빌드한 SIF 가 남아 있어도 다시 띄우지 않는다
    with caplog.at_level(logging.INFO):
        out = env.run()
    assert env.events == [("route", "step_forge", "http://127.0.0.1:16110"),
                          ("stop", "heax_app_step_forge"), ("release", 9115)], "라우트를 먼저 걸고 그 앱의 인스턴스만 내려야 한다"
    assert out["by_action"] == {"started": 1}
    st = json.loads((L.STATE_DIR / "step_forge.json").read_text(encoding="utf-8"))
    assert st["mode"] == "proxy" and "instance_name" not in st and st["pid"] is None
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("heax_app_step_forge" in m and "HH-16" in m for m in warned), warned
    assert "HH-16" in (L.LOG_DIR / "integration_step_forge.log").read_text(encoding="utf-8")

    env.events.clear()
    for _ in range(3):                                         # 다음 패스들 — 다시 내리지도 다시 띄우지도 않는다
        assert env.run()["by_action"] == {"started": 1}
    assert env.events == [("route", "step_forge", "http://127.0.0.1:16110")] * 3


def test_다시_띄우는_길이_없다(env):
    """스캐너 · redeploy-app.sh 도 같은 launch() 를 효과 매니페스트로 부른다 — SIF 를 줘도 서비스로 가지 않는다."""
    L._write_state("step_forge", _service_state("step_forge", instance="heax_app_step_forge"))
    sif = env.sifs / "step_forge.sif"
    sif.write_bytes(b"old-sif")
    ws = env.write("step_forge", _PROXY)
    for _ in range(2):
        lr = L.launch(ws, manifest=_PROXY, db=_DB, slug="step_forge", sif_path=sif)
        assert lr.action == "started" and lr.port is None
    assert [e for e in env.events if e[0] == "stop"] == [("stop", "heax_app_step_forge")]


def test_다른_앱의_인스턴스는_건드리지_않는다(env, monkeypatch):
    monkeypatch.setattr(L, "_is_alive", lambda pid: True)
    monkeypatch.setattr(L, "_is_healthy", lambda *a, **k: True)
    L._write_state("step_forge", _service_state("step_forge", instance="heax_app_step_forge"))
    L._write_state("other_app", _service_state("other_app", instance="heax_app_other_app", port=9120, pid=5555))
    env.write("step_forge", _PROXY)
    env.write("other-app", {**_SERVICE, "id": "other_app"})     # 이 앱은 여전히 서비스다
    out = env.run()
    assert [e for e in env.events if e[0] == "stop"] == [("stop", "heax_app_step_forge")]
    assert ("release", 9120) not in env.events
    assert out["by_action"] == {"already_running": 1, "started": 1}
    assert json.loads((L.STATE_DIR / "other_app.json").read_text(encoding="utf-8"))["instance_name"] == "heax_app_other_app"


def test_이름이_이_앱의_것이_아니면_건드리지_않는다(env, caplog):
    L._write_state("step_forge", _service_state("step_forge", instance="heax_app_someone_else"))
    env.write("step_forge", _PROXY)
    with caplog.at_level(logging.WARNING):
        env.run()
    assert not [e for e in env.events if e[0] == "stop"]
    assert any("heax_app_someone_else" in r.getMessage() for r in caplog.records)
    assert json.loads((L.STATE_DIR / "step_forge.json").read_text(encoding="utf-8"))["mode"] == "proxy"


def test_못_내리면_기록을_남겨_다음_패스가_다시_한다(env, caplog):
    env.apt.rcs = [(1, "FATAL: permission denied"), (0, "")]
    L._write_state("step_forge", _service_state("step_forge", instance="heax_app_step_forge"))
    env.write("step_forge", _PROXY)
    with caplog.at_level(logging.WARNING):
        env.run()
    st = json.loads((L.STATE_DIR / "step_forge.json").read_text(encoding="utf-8"))
    assert st.get("instance_name") == "heax_app_step_forge", "못 내린 기록을 지워 버렸다 — 다음 패스가 다시 할 수 없다"
    assert ("release", 9115) not in env.events, "아직 쥐고 있는 포트를 돌려줬다"
    assert any("permission denied" in r.getMessage() for r in caplog.records)
    env.run()
    assert [e for e in env.events if e[0] == "stop"] == [("stop", "heax_app_step_forge")] * 2
    assert ("release", 9115) in env.events
    assert json.loads((L.STATE_DIR / "step_forge.json").read_text(encoding="utf-8"))["mode"] == "proxy"


def test_이미_없으면_기록만_정리한다(env, caplog):
    """전환 창에서 먼저 손으로 멈춰 두었거나 stop.sh 가 내린 뒤다 — 실패가 아니다."""
    env.apt.rcs = [(255, "FATAL:   no instance found with name heax_app_step_forge")]
    L._write_state("step_forge", _service_state("step_forge", instance="heax_app_step_forge"))
    env.write("step_forge", _PROXY)
    with caplog.at_level(logging.INFO):
        env.run()
    assert ("release", 9115) in env.events
    assert json.loads((L.STATE_DIR / "step_forge.json").read_text(encoding="utf-8"))["mode"] == "proxy"
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING and "HH-16" in r.getMessage()]


def test_다른_워커가_잡고_있으면_다음_패스로_미룬다(env):
    import fcntl

    L._write_state("step_forge", _service_state("step_forge", instance="heax_app_step_forge"))
    env.write("step_forge", _PROXY)
    with (L.STATE_DIR / "step_forge.launch.lock").open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)        # 다른 워커가 이 앱을 띄우는 중
        env.run()
        assert not [e for e in env.events if e[0] == "stop"]
        assert json.loads((L.STATE_DIR / "step_forge.json").read_text(encoding="utf-8")).get("instance_name")
    env.run()
    assert [e for e in env.events if e[0] == "stop"] == [("stop", "heax_app_step_forge")]


def test_잠금_안에서_다시_읽어_이미_정리된_것은_두_번_내리지_않는다(env):
    """두 워커가 같은 기록을 읽고 차례로 잠금을 잡으면, 뒤의 것이 같은 포트를 또 돌려주면 안 된다(그새 다른 앱이 받았을 수 있다)."""
    L._write_state("step_forge", {"schema_version": L._STATE_SCHEMA_VERSION, "slug": "step_forge", "pid": None,
                                  "port": None, "mode": "proxy", "upstream": "http://127.0.0.1:16110"})
    assert L._retire_stale_service("step_forge", db=_DB) is True
    assert env.events == []
    # 그 사이 다른 서비스 기록으로 바뀌었으면 지금 기록된 것을 내린다.
    L._write_state("step_forge", _service_state("step_forge", instance="heax_app_step_forge", port=9200))
    assert L._retire_stale_service("step_forge", db=_DB) is True
    assert env.events == [("stop", "heax_app_step_forge"), ("release", 9200)]


def test_url_모드로_바뀌면_라우트와_상태_파일도_지운다(env):
    L._write_state("step_forge", _service_state("step_forge", instance="heax_app_step_forge"))
    env.write("step_forge", {**_PROXY, "launch": {"mode": "url", "url": "https://example.com/stepforge"}})
    out = env.run()
    assert env.events == [("stop", "heax_app_step_forge"), ("release", 9115), ("unroute", "step_forge")]
    assert out["by_action"] == {"retired": 1} and not (L.STATE_DIR / "step_forge.json").exists()
    env.events.clear()
    assert env.run()["by_action"] == {} and env.events == [], "남은 것이 없으면 요약 · 동작이 예전과 같아야 한다"


def test_static_모드로_바뀌면_새_라우트_뒤에_내리고_상태_파일을_지운다(env, monkeypatch):
    def _static(workspace, canonical, manifest, *, slug=None, source=None):
        env.events.append(("route", canonical, "static"))
        return L.LaunchResult(slug=slug, action="started", port=None, base_path=f"/apps/{canonical}")

    monkeypatch.setattr(L, "_launch_static", _static)
    L._write_state("step_forge", _service_state("step_forge", instance="heax_app_step_forge"))
    env.write("step_forge", {**_PROXY, "build": {"stack": "static_html"}, "launch": {"mode": "static"}})
    env.run()
    assert env.events == [("route", "step_forge", "static"), ("stop", "heax_app_step_forge"), ("release", 9115)]
    assert not (L.STATE_DIR / "step_forge.json").exists()


def test_호스트_프로세스도_명령줄이_맞을_때만_내린다(env, monkeypatch):
    killed: list = []
    monkeypatch.setattr(L, "_is_alive", lambda pid: True)
    monkeypatch.setattr(L.os, "getpgid", lambda pid: pid + 1)
    monkeypatch.setattr(L.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))
    env.write("step_forge", _PROXY)

    monkeypatch.setattr(L, "_pid_matches_argv", lambda pid, argv: False)      # pid 재사용 — 남의 프로세스다
    L._write_state("step_forge", _service_state("step_forge"))
    env.run()
    assert killed == []

    monkeypatch.setattr(L, "_pid_matches_argv", lambda pid, argv: True)
    L._write_state("step_forge", _service_state("step_forge"))
    env.run()
    assert killed == [(4243, signal.SIGTERM)]
    assert not [e for e in env.events if e[0] == "stop"], "인스턴스 기록이 없는데 instance stop 을 불렀다"


def test_서비스로_도는_앱은_그대로다(env, monkeypatch):
    monkeypatch.setattr(L, "_is_alive", lambda pid: True)
    monkeypatch.setattr(L, "_is_healthy", lambda *a, **k: True)
    L._write_state("step_forge", _service_state("step_forge", instance="heax_app_step_forge"))
    env.write("step_forge", _SERVICE)
    assert env.run()["by_action"] == {"already_running": 1}
    assert not [e for e in env.events if e[0] in ("stop", "release", "unroute")]
