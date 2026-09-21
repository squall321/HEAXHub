"""Tests for the SIF-backed dispatch path in ``integration_launcher``.

``apt_runner`` is mocked end-to-end so the tests never touch a real apptainer
binary. We exercise the four contracts the launcher needs to keep:

  1. ``sif_path`` present + instance NOT running → ``apt_runner.instance_start``
     is called, then ``apt_runner.instance_exec`` is called, and a state file
     with ``instance_name`` + ``sif_path`` lands on disk.
  2. ``sif_path`` present + instance ALREADY running (state file says so and
     ``apt_runner.instance_list`` returns it) → no start/exec, ``action ==
     "already_running"``.
  3. ``sif_path`` absent → existing host-PATH code path runs (Popen on the
     real ``subprocess`` module) — no apt_runner traffic at all.
  4. ``stop()`` with state containing ``instance_name`` → calls
     ``apt_runner.instance_stop`` instead of ``os.killpg``.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import integration_launcher


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect STATE_DIR + LOG_DIR + _state_path into tmp_path."""
    state_dir = tmp_path / "state"
    log_dir = tmp_path / "logs"
    app_data_root = tmp_path / "app_data"
    state_dir.mkdir()
    log_dir.mkdir()
    monkeypatch.setattr(integration_launcher, "STATE_DIR", state_dir)
    monkeypatch.setattr(integration_launcher, "LOG_DIR", log_dir)
    # 영구 데이터 볼륨 루트를 tmp 로 돌린다 — 안 하면 _app_data_dir 이 실제 repo
    # var/app_data/ 에 디렉터리를 만들어 테스트가 소스트리를 오염시킨다.
    monkeypatch.setattr(integration_launcher, "APP_DATA_ROOT", app_data_root)
    monkeypatch.setattr(
        integration_launcher, "_state_path",
        lambda c: state_dir / f"{c}.json",
    )

    # Caddy + port_allocator stubs so we don't touch real services.
    class _StubProxy:
        @staticmethod
        def register_app_route(*, app_id, port, base_path, strip_prefix=True):
            return SimpleNamespace(ok=True)

        @staticmethod
        def unregister_app_route(*, app_id):
            return None

    class _StubPorts:
        port = 17171
        released: list[int] = []  # record release calls for invariant tests

        @classmethod
        def allocate_port(cls, db, *, app_id, scope):
            return cls.port

        @classmethod
        def release_port(cls, db, *, port):
            cls.released.append(port)

    _StubPorts.released = []  # fresh per test
    monkeypatch.setattr(integration_launcher, "proxy_manager", _StubProxy)
    monkeypatch.setattr(integration_launcher, "port_allocator", _StubPorts)

    # Always healthy + 0 sleep to keep tests fast.
    monkeypatch.setattr(integration_launcher, "_is_healthy",
                        lambda port, p, *, root: True)
    monkeypatch.setattr(integration_launcher, "_is_alive", lambda pid: True)
    monkeypatch.setattr(integration_launcher.time, "sleep", lambda *_a, **_k: None)
    return tmp_path


def _manifest(stack: str = "fastapi", mode: str = "service") -> dict:
    return {
        "id": "demo_sif",
        "build": {"stack": stack},
        "launch": {"mode": mode, "health_check": {"path": "/health"}},
    }


# ---------------------------------------------------------------------------
# 1. SIF present + instance not running → start + exec
# ---------------------------------------------------------------------------


def test_starts_instance_when_sif_present_and_not_running(
    tmp_path: Path,
    isolated_state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = tmp_path / "demo_sif"
    ws.mkdir()
    sif = tmp_path / "demo_sif.sif"
    sif.write_bytes(b"fake-sif")

    started: list[dict] = []
    execed: list[dict] = []

    def fake_instance_list(**_kwargs):
        return []  # nothing running yet

    def fake_instance_start(*, sif, name, binds=(), cleanenv=True, env=None, **kw):
        started.append({
            "sif": Path(sif),
            "name": name,
            "binds": list(binds),
            "cleanenv": cleanenv,
            "env": dict(env or {}),
        })
        return subprocess.CompletedProcess(args=["instance", "start"], returncode=0)

    def fake_instance_exec(name, argv, env=None, *, cleanenv=True, cwd=None, **kw):
        execed.append({
            "name": name,
            "argv": list(argv),
            "env": dict(env or {}),
            "cleanenv": cleanenv,
            "cwd": cwd,
        })
        # Closes the log file handle (Popen would close it on exit).
        if "stdout" in kw and hasattr(kw["stdout"], "close"):
            pass
        return SimpleNamespace(pid=4242, poll=lambda: None, returncode=None)

    monkeypatch.setattr(integration_launcher.apt_runner, "instance_list", fake_instance_list)
    monkeypatch.setattr(integration_launcher.apt_runner, "instance_start", fake_instance_start)
    monkeypatch.setattr(integration_launcher.apt_runner, "instance_exec", fake_instance_exec)

    result = integration_launcher.launch(
        ws,
        manifest=_manifest(stack="fastapi"),
        db=None,
        slug="demo_sif",
        sif_path=sif,
    )

    assert result.action == "started", result.error
    assert result.port == 17171
    assert result.pid == 4242

    # start was called with the SIF + canonical instance name + workspace bind.
    assert len(started) == 1
    s = started[0]
    assert s["sif"] == sif
    assert s["name"] == "heax_app_demo_sif"
    assert (str(ws), "/workspace") in s["binds"]
    assert s["env"]["PORT"] == "17171"
    assert s["env"]["ROOT_PATH"] == "/apps/demo_sif"
    # 영구 데이터 볼륨: var/app_data/<canonical> → /data 로 bind 되고 HEAX_DATA_DIR
    # 로 알려진다(SIF rootfs 는 read-only 라 앱은 여기 외엔 못 쓴다).
    data_binds = [h for h, c in s["binds"] if c == "/data"]
    assert len(data_binds) == 1, s["binds"]
    assert data_binds[0].endswith("app_data/demo_sif")
    assert Path(data_binds[0]).is_dir()  # _app_data_dir 이 미리 만들어 둔다
    assert s["env"]["HEAX_DATA_DIR"] == "/data"

    # exec ran the canonical fastapi argv inside that instance.
    assert len(execed) == 1
    e = execed[0]
    assert e["name"] == "heax_app_demo_sif"
    assert e["argv"][0] == "uvicorn"
    assert "app.main:app" in e["argv"]
    assert e["env"]["PORT"] == "17171"

    # State file recorded the SIF + instance.
    state = integration_launcher._read_state("demo_sif")
    assert state is not None
    assert state["instance_name"] == "heax_app_demo_sif"
    assert state["sif_path"] == str(sif)
    assert state["schema_version"] == integration_launcher._STATE_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# 2. Instance already running → reuse, no start/exec
# ---------------------------------------------------------------------------


def test_reuses_running_instance_when_already_started(
    tmp_path: Path,
    isolated_state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = tmp_path / "demo_sif"
    ws.mkdir()
    sif = tmp_path / "demo_sif.sif"
    sif.write_bytes(b"fake-sif")

    # Pre-populate state for a "previously launched" SIF instance.
    state_path = integration_launcher._state_path("demo_sif")
    state_path.write_text(json.dumps({
        "schema_version": integration_launcher._STATE_SCHEMA_VERSION,
        "slug": "demo_sif",
        "pid": 1234,
        "port": 17171,
        "base_path": "/apps/demo_sif",
        "health_path": "/health",
        "stack": "fastapi",
        "argv": [str(sif), "uvicorn", "app.main:app"],
        "instance_name": "heax_app_demo_sif",
        "sif_path": str(sif),
        "caddy_registered": True,
    }))

    monkeypatch.setattr(
        integration_launcher.apt_runner, "instance_list",
        lambda **kw: ["heax_app_demo_sif"],
    )

    def must_not_start(**_kw):  # pragma: no cover - assertion only
        raise AssertionError("instance_start must not be called when reused")

    def must_not_exec(*_a, **_kw):  # pragma: no cover - assertion only
        raise AssertionError("instance_exec must not be called when reused")

    monkeypatch.setattr(integration_launcher.apt_runner, "instance_start", must_not_start)
    monkeypatch.setattr(integration_launcher.apt_runner, "instance_exec", must_not_exec)

    result = integration_launcher.launch(
        ws,
        manifest=_manifest(stack="fastapi"),
        db=None,
        slug="demo_sif",
        sif_path=sif,
    )

    assert result.action == "already_running"
    assert result.pid == 1234
    assert result.port == 17171


# ---------------------------------------------------------------------------
# 3. No SIF → existing host-PATH code path runs (no apt_runner traffic)
# ---------------------------------------------------------------------------


def test_falls_back_to_host_path_when_no_sif(
    tmp_path: Path,
    isolated_state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = tmp_path / "demo_host"
    ws.mkdir()
    # Make the host-mode argv builder succeed without a real venv: stub it.
    monkeypatch.setattr(
        integration_launcher, "_argv_for",
        lambda workspace, spec, manifest, *, port, base_path: ["/bin/echo", "host-mode"],
    )

    popen_calls: list[list[str]] = []

    class _FakePopen:
        def __init__(self, argv, **_kw):
            popen_calls.append(argv)
            self.pid = 9999
            self.returncode = None

        def poll(self):
            return None

    monkeypatch.setattr(integration_launcher.subprocess, "Popen", _FakePopen)

    # Guard: apt_runner must NOT be called when no SIF is supplied.
    def fail(*_a, **_kw):  # pragma: no cover - assertion only
        raise AssertionError("apt_runner must not be called in host-PATH mode")

    monkeypatch.setattr(integration_launcher.apt_runner, "instance_list", fail)
    monkeypatch.setattr(integration_launcher.apt_runner, "instance_start", fail)
    monkeypatch.setattr(integration_launcher.apt_runner, "instance_exec", fail)

    manifest = _manifest(stack="fastapi")
    manifest["id"] = "demo_host"
    result = integration_launcher.launch(
        ws,
        manifest=manifest,
        db=None,
        slug="demo_host",
        sif_path=None,
    )

    assert result.action == "started"
    assert popen_calls and popen_calls[0] == ["/bin/echo", "host-mode"]

    # State file does NOT carry instance_name/sif_path in host mode.
    state = integration_launcher._read_state("demo_host")
    assert state is not None
    assert "instance_name" not in state
    assert "sif_path" not in state


# ---------------------------------------------------------------------------
# 4. stop() with SIF state → calls apt_runner.instance_stop
# ---------------------------------------------------------------------------


def test_stop_calls_instance_stop_when_state_has_instance_name(
    tmp_path: Path,
    isolated_state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = "demo_sif"
    state_path = integration_launcher._state_path(canonical)
    state_path.write_text(json.dumps({
        "schema_version": integration_launcher._STATE_SCHEMA_VERSION,
        "slug": canonical,
        "pid": 4242,
        "port": 17171,
        "base_path": f"/apps/{canonical}",
        "instance_name": "heax_app_demo_sif",
        "sif_path": "/tmp/fake.sif",
    }))

    stop_calls: list[tuple[str, dict]] = []

    def fake_instance_stop(name, **kwargs):
        stop_calls.append((name, kwargs))
        return subprocess.CompletedProcess(args=["instance", "stop"], returncode=0)

    monkeypatch.setattr(integration_launcher.apt_runner, "instance_stop", fake_instance_stop)

    # Make sure we DON'T fall through to os.killpg — fail loud if we do.
    def must_not_kill(*_a, **_kw):  # pragma: no cover
        raise AssertionError("stop() must use instance_stop, not killpg, for SIF state")

    monkeypatch.setattr(integration_launcher.os, "killpg", must_not_kill)

    killed = integration_launcher.stop(canonical, db=None)
    assert killed is True
    assert len(stop_calls) == 1
    assert stop_calls[0][0] == "heax_app_demo_sif"
    # State file is cleaned up after stop.
    assert integration_launcher._read_state(canonical) is None


# ---------------------------------------------------------------------------
# 5. launch failure must NOT release the port (parked-port invariant)
# ---------------------------------------------------------------------------


def test_sif_launch_failure_does_not_release_port(
    tmp_path: Path,
    isolated_state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """idempotent allocate 는 살아있는 인스턴스가 bind 중인 포트를 돌려줄 수 있다
    (헬스 프로브 일시 오판 → 콜드스타트 재진입). 그 상태에서 launch 가 실패할 때
    포트를 release 하면 다른 앱이 그 live 포트를 재할당받아 Caddy 가 교차 라우팅
    된다. 실패 경로는 release 금지 — 포트는 앱에 파킹, 해제는 stop() 만."""
    ws = tmp_path / "demo_sif"
    ws.mkdir()
    sif = tmp_path / "demo_sif.sif"
    sif.write_bytes(b"fake-sif")

    def fake_instance_list(**_kwargs):
        return []

    # ⚠ 실패를 **실제 모양**으로 흉내 낸다. `apt_runner.run` 은 `check=True` 를 쓰지 않으므로
    # 진짜 실패는 예외가 아니라 **종료코드**로 온다(2026-09-20). 예전엔 이 스텁이 예외를 던져,
    # 프로덕션에서 한 번도 실행되지 않는 경로 위에서 불변식을 확인하고 있었다.
    def fake_instance_start(**_kwargs):
        return subprocess.CompletedProcess(args=["instance", "start"], returncode=1,
                                           stdout="", stderr="FATAL: mount /data failed")

    monkeypatch.setattr(integration_launcher.apt_runner, "instance_list", fake_instance_list)
    monkeypatch.setattr(integration_launcher.apt_runner, "instance_start", fake_instance_start)

    result = integration_launcher.launch(
        ws, manifest=_manifest(stack="fastapi"), db=None,
        slug="demo_sif", sif_path=sif,
    )

    assert result.action == "failed"
    assert integration_launcher.port_allocator.released == []


def test_host_launch_failure_does_not_release_port(
    tmp_path: Path,
    isolated_state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """호스트 프로세스 경로도 동일한 파킹 불변식을 지킨다(Popen 실패 시)."""
    ws = tmp_path / "demo_sif"
    ws.mkdir()

    def fake_popen(*_a, **_k):
        raise OSError("spawn failed")

    monkeypatch.setattr(integration_launcher.subprocess, "Popen", fake_popen)

    result = integration_launcher.launch(
        ws, manifest=_manifest(stack="fastapi"), db=None, slug="demo_sif",
    )

    assert result.action == "failed"
    assert integration_launcher.port_allocator.released == []


# ---------------------------------------------------------------------------
# 6. _app_data_dir — persistent per-app volume, traversal-guarded
# ---------------------------------------------------------------------------


def test_app_data_dir_creates_under_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal canonical creates (and returns) var/app_data/<canonical>/."""
    root = tmp_path / "app_data"
    monkeypatch.setattr(integration_launcher, "APP_DATA_ROOT", root)

    d = integration_launcher._app_data_dir("materialtwin_web")
    assert d == (root / "materialtwin_web").resolve()
    assert d.is_dir()


def test_app_data_dir_rejects_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """canonical comes from manifest.id — it must never escape the data root."""
    root = tmp_path / "app_data"
    monkeypatch.setattr(integration_launcher, "APP_DATA_ROOT", root)

    for bad in ("../evil", "../../etc", "", "a/../../b"):
        with pytest.raises(ValueError):
            integration_launcher._app_data_dir(bad)
    # nothing outside the root was created
    assert not (tmp_path / "evil").exists()


# ---------------------------------------------------------------------------
# 5. 기동 실패는 **이유가 남아야** 한다 (2026-09-20 cae00: heax_demo_nextjs)
# ---------------------------------------------------------------------------
#
# 왜 — 라우트가 없으면 허브 Caddy 의 SPA catch-all 이 **200 + 허브 첫 화면**을 돌려준다.
# 겉으론 정상이라 헬스게이트가 본문으로 잡아내야 겨우 드러나는데, 정작 "왜 안 떴나" 를
# 적어 둔 자리가 없었다. 기동 전 실패는 앱 로그 파일을 아예 만들지 않았기 때문이다.


def _fail_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, start_rc: int,
              start_err: str = "", exec_exits: bool = False):
    """SIF 경로를 태우되 instance start 결과를 시험이 정한다."""
    ws = tmp_path / "demo_sif"
    ws.mkdir(exist_ok=True)
    sif = tmp_path / "demo_sif.sif"
    sif.write_bytes(b"fake-sif")
    calls: dict[str, int] = {"start": 0, "exec": 0}

    def fake_instance_list(**_kw):
        return []

    def fake_instance_start(**kw):
        calls["start"] += 1
        return subprocess.CompletedProcess(args=["instance", "start"], returncode=start_rc,
                                           stdout="", stderr=start_err)

    def fake_instance_exec(name, argv, **kw):
        calls["exec"] += 1
        return SimpleNamespace(pid=4242, poll=lambda: (1 if exec_exits else None), returncode=1)

    monkeypatch.setattr(integration_launcher.apt_runner, "instance_list", fake_instance_list)
    monkeypatch.setattr(integration_launcher.apt_runner, "instance_start", fake_instance_start)
    monkeypatch.setattr(integration_launcher.apt_runner, "instance_exec", fake_instance_exec)
    return ws, sif, calls


def test_a_failed_instance_start_is_reported_with_its_real_reason(
    tmp_path: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`apt_runner.run` 은 check=True 를 안 쓴다 — 실패가 **예외가 아니라 종료코드**로 온다.
    그래서 옛 `except CalledProcessError` 는 죽은 코드였고, 실패해도 exec 로 내려가
    "process exited early" 라는 엉뚱한 이유만 남았다."""
    ws, sif, calls = _fail_env(tmp_path, monkeypatch, start_rc=255,
                               start_err="FATAL: could not open image /x.sif")

    result = integration_launcher.launch(ws, manifest=_manifest(), db=None,
                                         slug="demo_sif", sif_path=sif)

    assert result.action == "failed"
    assert "could not open image" in (result.error or ""), result.error
    assert calls["exec"] == 0, "start 가 실패했는데 exec 로 내려갔다"


def test_already_exists_is_not_a_failure(
    tmp_path: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """목록 조회가 한 박자 늦었을 뿐 인스턴스는 거기 있다 — 이걸 실패로 보면 멀쩡한 앱이 안 뜬다."""
    ws, sif, calls = _fail_env(tmp_path, monkeypatch, start_rc=255,
                               start_err="FATAL: instance heax_app_demo_sif already exists")

    result = integration_launcher.launch(ws, manifest=_manifest(), db=None,
                                         slug="demo_sif", sif_path=sif)

    assert result.action == "started", result.error
    assert calls["exec"] == 1


def test_the_reason_lands_in_the_app_log_where_people_look(
    tmp_path: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """운영자는 `var/logs/integration_<앱>.log` 를 본다. 거기가 비어 있으면 이유는 없는 것이다."""
    ws, sif, _ = _fail_env(tmp_path, monkeypatch, start_rc=1, start_err="mount failed: /data")

    integration_launcher.launch(ws, manifest=_manifest(), db=None,
                                slug="demo_sif", sif_path=sif)

    log = integration_launcher.LOG_DIR / "integration_demo_sif.log"
    assert log.exists(), "실패 이유를 적을 파일조차 안 만들었다"
    assert "launch failed" in log.read_text(encoding="utf-8")
    assert "mount failed" in log.read_text(encoding="utf-8")


def test_an_early_exit_also_explains_itself(
    tmp_path: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws, sif, _ = _fail_env(tmp_path, monkeypatch, start_rc=0, exec_exits=True)

    result = integration_launcher.launch(ws, manifest=_manifest(), db=None,
                                         slug="demo_sif", sif_path=sif)

    assert result.action == "failed" and result.pid == 4242
    log = integration_launcher.LOG_DIR / "integration_demo_sif.log"
    assert "process exited early" in log.read_text(encoding="utf-8")


def test_a_failure_does_not_make_the_app_look_launched(
    tmp_path: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⚠ 실패에 상태 파일을 쓰면 안 된다 — `api/v1/mcp.py:_ever_launched` 가 상태 파일의
    **존재**로 "한 번이라도 뜬 앱" 을 판정한다. 한 번도 못 뜬 앱이 게이트웨이에 등록되면
    영구 다운 백엔드가 된다(그 주석이 막으려던 바로 그것)."""
    ws, sif, _ = _fail_env(tmp_path, monkeypatch, start_rc=2, start_err="boom")

    integration_launcher.launch(ws, manifest=_manifest(), db=None,
                                slug="demo_sif", sif_path=sif)

    assert integration_launcher._read_state("demo_sif") is None, "실패인데 기동된 것으로 기록했다"
