# 앱을 띄울 때 쓰는 호스트 env 가 허브 기동 때의 옛 값이 아니라 지금의 .env 를 따르는지(cae00 HH-18)
"""``host_env.current`` 와 그것을 쓰는 apt_runner 의 하위 프로세스 env.

허브는 기동 때 .env 를 한 번 source 한다. 그 뒤 .env 를 고치면 자동 길로 뜨는 앱이 옛 값을 받았다.
"""
from __future__ import annotations

import subprocess

import pytest

from app.services import apt_runner, host_env

_ENV = (
    '# 주석 줄\n'
    'export APPTAINERENV_DOCFORGE_SP_CORE_URL="https://portal.example/rootdesign/rootdesign/api"\n'
    "HTTPS_PROXY='http://new-proxy:3128'\n"
    'NO_PROXY="localhost,127.0.0.1,portal.example"\n'
    'EMPTY=\n'
    'BARE\n'
    'MAYBE=${NOT_DEFINED_ANYWHERE}\n'   # 실제 .env 에 있는 모양 — bash 처럼 빈 값
)


@pytest.fixture()
def stale_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """허브가 11-12 이전 .env 로 떠 있던 상태."""
    monkeypatch.setenv("APPTAINERENV_DOCFORGE_SP_CORE_URL", "http://old-core:9000/api")
    monkeypatch.setenv("APPTAINERENV_REMOVED_KEY", "stale")      # 운영자가 .env 에서 지웠다
    monkeypatch.setenv("HTTPS_PROXY", "http://old-proxy:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://shell-profile:3128")  # .env 가 아니라 셸 프로필에서 왔다
    monkeypatch.delenv("BARE", raising=False)


def test_env_file_wins_over_the_stale_process_env(stale_process_env) -> None:
    host_env.ENV_FILE.write_text(_ENV, encoding="utf-8")

    env = host_env.current()

    assert env["APPTAINERENV_DOCFORGE_SP_CORE_URL"] == "https://portal.example/rootdesign/rootdesign/api"
    assert env["HTTPS_PROXY"] == "http://new-proxy:3128"            # 홑따옴표가 풀린다
    assert env["NO_PROXY"] == "localhost,127.0.0.1,portal.example"
    assert env["EMPTY"] == "" and env["MAYBE"] == ""
    assert "BARE" not in env                                         # 값 없는 줄은 아무것도 정하지 않는다


def test_apptainerenv_removed_from_env_file_is_dropped(stale_process_env) -> None:
    host_env.ENV_FILE.write_text(_ENV, encoding="utf-8")

    env = host_env.current()

    assert "APPTAINERENV_REMOVED_KEY" not in env, "지운 앱 env 가 옛 값으로 살아남았다"
    assert env["HTTP_PROXY"] == "http://shell-profile:3128", "셸에서 온 프록시는 .env 에 없어도 남아야 한다"


def test_without_env_file_the_process_env_is_used_as_is(stale_process_env) -> None:
    assert not host_env.ENV_FILE.exists()

    env = host_env.current()

    assert env["APPTAINERENV_REMOVED_KEY"] == "stale"
    assert env["HTTPS_PROXY"] == "http://old-proxy:3128"


def test_instance_start_hands_apptainer_the_current_env(
    stale_process_env, monkeypatch: pytest.MonkeyPatch,
) -> None:
    host_env.ENV_FILE.write_text(_ENV, encoding="utf-8")
    captured: dict = {}

    def fake_run(args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(apt_runner, "run", fake_run)
    monkeypatch.setattr(apt_runner, "local_apptainer_path", lambda: "/usr/bin/apptainer")
    apt_runner.instance_start("/tmp/x.sif", "inst", env={"PORT": "9001"})

    sub = captured["env"]
    assert sub["APPTAINERENV_PORT"] == "9001"
    assert sub["APPTAINERENV_DOCFORGE_SP_CORE_URL"] == "https://portal.example/rootdesign/rootdesign/api"
    assert "APPTAINERENV_REMOVED_KEY" not in sub


def test_instance_exec_hands_apptainer_the_current_env(
    stale_process_env, monkeypatch: pytest.MonkeyPatch,
) -> None:
    host_env.ENV_FILE.write_text(_ENV, encoding="utf-8")
    captured: dict = {}

    def fake_popen(args, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(apt_runner, "local_apptainer_path", lambda: "/usr/bin/apptainer")
    monkeypatch.setattr(apt_runner.subprocess, "Popen", fake_popen)
    apt_runner.instance_exec("inst", ["uvicorn"], env={"PORT": "9001"})

    sub = captured["env"]
    assert sub["APPTAINERENV_PORT"] == "9001"
    assert sub["APPTAINERENV_DOCFORGE_SP_CORE_URL"] == "https://portal.example/rootdesign/rootdesign/api"
    assert "APPTAINERENV_REMOVED_KEY" not in sub
