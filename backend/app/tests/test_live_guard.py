# 시험 가드(conftest)가 기본으로 앱 lifespan 의 clone · scan · reconcile 과 apptainer · Caddy admin 을 막고, HEAX_TEST_LIVE=1 이면 오늘의 길로 두는지 본다
"""Proof that the default test run cannot reach this box's live services through the app lifespan.

Every stand-in here only records — even if the guard were broken, these tests reach nothing live: the
in-process tests replace the very functions the lifespan would call, and the child runs add a probe
plugin plus closed-port / empty settings as a backstop.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.services import apt_runner, integration_workspaces, integrations_scanner, proxy_manager
from app.workers import integration_tasks

LIVE_ENV = "HEAX_TEST_LIVE"
LIVE = os.environ.get(LIVE_ENV) == "1"
BACKEND = Path(__file__).resolve().parents[2]
REPO_INTEGRATIONS = (BACKEND.parent / "integrations").resolve()

_default_only = pytest.mark.skipif(LIVE, reason=f"{LIVE_ENV}=1 — 가드를 끈 실행이다")


def _install_stand_ins(monkeypatch, calls: list[str]) -> dict:
    """가드가 없으면 lifespan 이 부를 자리를 미리 대역으로 바꾼다 — 가드가 깨져도 이 시험들은 아무 데도 닿지 않는다.

    대역은 불리면 ``calls`` 에 이름을 남긴다. 길목 대역은 없는 실행 파일 · 닫힌 포트를 돌려준다.
    """

    def stand_in(call: str, result):
        def record(*args, **kwargs):
            calls.append(call)
            return result

        return record

    stubs = {
        (integration_workspaces, "ensure_all_cloned"): stand_in("clone", []),
        (integrations_scanner, "scan_integrations"): stand_in("scan", []),
        (integration_tasks, "reconcile_integrations"): stand_in("reconcile", {"by_action": {}}),
        (apt_runner, "local_apptainer_path"): stand_in("apptainer", "/nonexistent/heax-test-apptainer"),
        (proxy_manager, "_admin_url"): lambda path: calls.append("caddy") or f"http://127.0.0.1:9{path}",
    }
    for (module, name), stub in stubs.items():
        monkeypatch.setattr(module, name, stub)
    return stubs


@_default_only
def test_default_lifespan_skips_startup_steps_and_restores_test_stubs(monkeypatch):
    calls: list[str] = []
    stubs = _install_stand_ins(monkeypatch, calls)

    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200
        # 기동이 끝나면 시험이 바꿔 둔 대역이 제자리에 돌아와 있다(lifespan 밖에서 대역을 쓰는 시험이 그대로 돈다).
        assert all(getattr(module, name) is stub for (module, name), stub in stubs.items())
    assert calls == []
    for root in (integrations_scanner.INTEGRATIONS_ROOT, integration_tasks._INTEGRATIONS_ROOT):
        assert root.resolve() != REPO_INTEGRATIONS
        assert root.is_dir() and not any(root.iterdir())


@_default_only
def test_default_lifespan_fails_when_a_new_step_reaches_apptainer_or_caddy(monkeypatch):
    calls: list[str] = []
    # 덫이 없으면 길목 대역이 불린다 — 없는 실행 파일 · 닫힌 포트라 그래도 아무 데도 닿지 않는다.
    _install_stand_ins(monkeypatch, calls)
    real_setup_logging = main.setup_logging

    def setup_logging_then_new_step():
        # 가드가 모르는 새 기동 단계의 꼴 — 지금의 기동 단계처럼 실패를 삼킨다.
        real_setup_logging()
        for probe in (apt_runner.instance_list, proxy_manager.list_routes):
            with contextlib.suppress(Exception):
                probe()

    monkeypatch.setattr(main, "setup_logging", setup_logging_then_new_step)
    with pytest.raises(RuntimeError, match="apptainer · Caddy admin"), TestClient(main.app):
        pass
    assert calls == []


# 자식 pytest 에 얹는 대역 플러그인 — 가드보다 먼저 올라가 진짜 기동 단계 자리에 서서 불리면 기록만 한다.
_PROBE = '''\
# 시험 가드 증명용 대역 — lifespan 이 부르는 기동 단계를 기록만 하는 대역으로 바꾸고 apptainer · Caddy · 기동 길목은 막는다
import json
import os

from app.services import apt_runner, integration_launcher, integration_workspaces, integrations_scanner, proxy_manager
from app.workers import integration_tasks


def _note(call):
    roots = [str(integrations_scanner.INTEGRATIONS_ROOT), str(integration_tasks._INTEGRATIONS_ROOT)]
    with open(os.environ["HEAX_GUARD_PROBE_LOG"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"call": call, "roots": roots}) + "\\n")


def _stand_in(call, result):
    def stand_in(*args, **kwargs):
        _note(call)
        return result
    return stand_in


def _refuse(call):
    def refuse(*args, **kwargs):
        _note(call)
        raise RuntimeError("probe refused " + call)
    return refuse


integration_workspaces.ensure_all_cloned = _stand_in("clone", [])
integrations_scanner.scan_integrations = _stand_in("scan", [])
integration_tasks.reconcile_integrations = _stand_in("reconcile", {"count": 0, "by_action": {}, "items": []})
# 뒷받침 — 위 대역이 빗나가도 아무 데도 닿지 않는다(불리면 기록에 남아 시험이 실패한다).
integration_launcher.launch = _refuse("launch")
integration_launcher.retire_unrouted = _refuse("retire")
apt_runner.local_apptainer_path = _refuse("apptainer")
proxy_manager._admin_url = _refuse("caddy")


def pytest_sessionfinish(session):
    _note("session")
'''


@pytest.mark.parametrize("live", [False, True], ids=["default", "live"])
def test_api_smoke_lifespan_reaches_startup_steps_only_with_opt_in(tmp_path, live):
    (tmp_path / "heax_guard_probe.py").write_text(_PROBE, encoding="utf-8")
    log = tmp_path / "probe.jsonl"
    env = {k: v for k, v in os.environ.items() if k not in (LIVE_ENV, "PYTEST_ADDOPTS")}
    env.update(
        PYTHONPATH=os.pathsep.join(filter(None, [str(tmp_path), os.environ.get("PYTHONPATH")])),
        HEAX_GUARD_PROBE_LOG=str(log),
        # 뒷받침 — 대역이 빗나가도 닿을 곳이 없다(닫힌 포트 · 빈 clone 목록 · 임시 디렉터리).
        DATABASE_URL="postgresql+psycopg://probe:probe@127.0.0.1:9/probe",
        CADDY_ADMIN_URL="http://127.0.0.1:9",
        INTEGRATION_REPO_URL="",
        INTEGRATION_REPO_URLS="",
        WORKSPACE_ROOT=str(tmp_path / "workspaces"),
        JOB_STORAGE_ROOT=str(tmp_path / "jobs"),
    )
    if live:
        env[LIVE_ENV] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "heax_guard_probe",
         "app/tests/test_api_smoke.py::test_public_get_returns_200"],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    notes = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    roots = {Path(r).resolve() for r in notes[-1]["roots"]}
    if live:
        # 오늘의 길 — lifespan 이 기동 단계 셋을 차례로 부르고 뿌리는 이 리포의 integrations/ 다.
        assert [n["call"] for n in notes] == ["clone", "scan", "reconcile", "session"], out
        assert roots == {REPO_INTEGRATIONS}
        assert "[HEAX 시험 가드] 꺼짐" in out
    else:
        assert [n["call"] for n in notes] == ["session"], out
        assert REPO_INTEGRATIONS not in roots
        assert "[HEAX 시험 가드] 켜짐" in out
