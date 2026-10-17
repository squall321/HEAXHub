# 서비스 앱 기동이 되풀이 실패하면 다음 시도를 늦추고, 앱 로그는 새로 띄울 때 커졌으면 한 벌만 넘기는지(10-17 cae00 리뷰 X4)
#
# cae00 heax_demo_nextjs 가 09-22 부터 45초마다 다시 띄워져 실패했다(48,600여 회 · 통합 로그 392MB). 감싸는 launch() 만 보도록
# 실제 기동(_launch_once)은 대역으로 바꾼다 — 대역이 불린 횟수가 '다시 띄웠다' 의 횟수다.
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import integration_launcher as L
from app.services import integrations_scanner as S

_MANIFEST = {"id": "demo_next", "launch": {"mode": "service"}, "build": {"stack": "nextjs"}}


class _Inner:
    def __init__(self, *actions: str):
        self.actions = list(actions)
        self.calls = 0

    def __call__(self, workspace, *, manifest, db, slug=None, source=None, sif_path=None):
        self.calls += 1
        action = self.actions.pop(0) if self.actions else "failed"
        return L.LaunchResult(slug=slug or workspace.name, action=action, port=None, base_path="/apps/demo_next",
                              error="corepack: unable to get local issuer certificate" if action == "failed" else None)


@pytest.fixture()
def clock(monkeypatch):
    now = {"t": 1_000_000.0}
    monkeypatch.setattr(L.time, "time", lambda: now["t"])
    return now


def _launch(tmp_path: Path, manifest=_MANIFEST, sif: Path | None = None):
    return L.launch(tmp_path / "demo-next", manifest=manifest, db=None, sif_path=sif)


def test_실패하면_다음_시도를_늦추고_로그에_말한다(tmp_path, monkeypatch, clock):
    inner = _Inner("failed", "failed")
    monkeypatch.setattr(L, "_launch_once", inner)
    assert _launch(tmp_path).action == "failed"
    r = _launch(tmp_path)                                      # 45초 뒤 reconcile — 아직 기다리는 중
    assert r.action == "backoff" and inner.calls == 1, "실패한 앱을 곧바로 다시 띄웠다"
    assert "다음 시도" in (r.error or "") and "corepack" in (r.error or "")
    log = (L.LOG_DIR / "integration_demo_next.log").read_text(encoding="utf-8")
    assert "기동 실패 1번째 — 다음 시도 60초 뒤" in log
    clock["t"] += 61
    assert _launch(tmp_path).action == "failed" and inner.calls == 2
    assert "기동 실패 2번째 — 다음 시도 120초 뒤" in (L.LOG_DIR / "integration_demo_next.log").read_text(encoding="utf-8")


def test_기다림은_상한에서_멈춘다(tmp_path, monkeypatch, clock):
    monkeypatch.setattr(L, "_launch_once", _Inner())
    for _ in range(12):
        _launch(tmp_path)
        clock["t"] += 10_000
    rec = L._read_backoff("demo_next")
    assert rec["failures"] == 12 and rec["next_at"] - clock["t"] + 10_000 == L._BACKOFF_MAX_S


def test_매니페스트나_SIF_가_바뀌면_곧바로_다시_한다(tmp_path, monkeypatch, clock):
    inner = _Inner("failed", "failed", "failed")
    monkeypatch.setattr(L, "_launch_once", inner)
    sif = tmp_path / "demo_next.sif"
    sif.write_bytes(b"v1")
    _launch(tmp_path, sif=sif)
    sif.write_bytes(b"v2-rebuilt")                             # 새로 구운 SIF
    assert _launch(tmp_path, sif=sif).action == "failed" and inner.calls == 2
    changed = {**_MANIFEST, "version": "0.2.0"}
    assert _launch(tmp_path, manifest=changed, sif=sif).action == "failed" and inner.calls == 3


def test_성공하면_기록을_지우고_다음_실패는_처음부터_센다(tmp_path, monkeypatch, clock):
    monkeypatch.setattr(L, "_launch_once", _Inner("failed", "started", "failed"))
    _launch(tmp_path)
    clock["t"] += 61
    assert _launch(tmp_path).action == "started" and not L._backoff_path("demo_next").exists()
    _launch(tmp_path)
    assert L._read_backoff("demo_next")["failures"] == 1


def test_운영자가_stop_하면_곧바로_다시_한다(tmp_path, monkeypatch, clock):
    """redeploy-app.sh 는 stop() 뒤 launch() 를 부른다 — 손으로 내렸다 올린 것까지 억제하면 고친 것을 확인할 수 없다."""
    inner = _Inner("failed", "failed")
    monkeypatch.setattr(L, "_launch_once", inner)
    _launch(tmp_path)
    L.stop("demo_next", db=None)
    assert _launch(tmp_path).action == "failed" and inner.calls == 2


def test_상태의_프로세스가_살아_있으면_막지_않는다(tmp_path, monkeypatch, clock):
    inner = _Inner("failed", "already_running")
    monkeypatch.setattr(L, "_launch_once", inner)
    _launch(tmp_path)
    monkeypatch.setattr(L, "_read_state", lambda c: {"pid": 4242, "port": 9100})
    monkeypatch.setattr(L, "_is_alive", lambda pid: True)
    assert _launch(tmp_path).action == "already_running" and inner.calls == 2
    assert not L._backoff_path("demo_next").exists()


def test_서비스가_아닌_모드는_감싸지_않는다(tmp_path, monkeypatch, clock):
    inner = _Inner("failed", "failed")
    monkeypatch.setattr(L, "_launch_once", inner)
    proxy = {"id": "ext", "launch": {"mode": "proxy"}}
    L.launch(tmp_path / "ext", manifest=proxy, db=None)
    L.launch(tmp_path / "ext", manifest=proxy, db=None)
    assert inner.calls == 2 and not L._backoff_path("ext").exists()


def test_앱_로그는_커졌으면_한_벌만_넘기고_새로_연다(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "_APP_LOG_ROTATE_BYTES", 10)
    log = tmp_path / "integration_x.log"
    log.write_bytes(b"x" * 50)
    (tmp_path / "integration_x.log.1").write_bytes(b"older")
    with L._open_app_log(log) as fh:
        fh.write(b"new run\n")
    assert log.read_bytes() == b"new run\n" and (tmp_path / "integration_x.log.1").read_bytes() == b"x" * 50
    with L._open_app_log(log) as fh:
        fh.write(b"again\n")
    assert log.read_bytes() == b"new run\nagain\n", "작으면 넘기지 않고 덧붙인다"


def test_스캔_요약은_억제_중인_앱을_실패로_세고_감사는_새로_남기지_않는다(tmp_path, monkeypatch):
    """'unchanged' 로 보이면 죽어 있는 앱이 다시 조용해진다(스캐너 주석의 실측 — 며칠째 죽어 있어도 아무도 몰랐다)."""
    audits = []
    monkeypatch.setattr(S, "_audit_launch_failed", lambda db, *, app, error: audits.append(error))
    monkeypatch.setattr(L, "launch", lambda *a, **k: L.LaunchResult(slug="demo-next", action="backoff", port=None,
                                                                    base_path="/apps/demo_next", error="다음 시도 60초 뒤"))
    got = S._maybe_launch(None, app=SimpleNamespace(id="demo_next"), stack=SimpleNamespace(launch_mode="service"),
                          integration_dir=tmp_path / "demo-next", manifest=_MANIFEST, source=None, sif_path=None)
    assert got == "failed" and audits == []
