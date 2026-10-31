# 시험이 이 박스의 실제 서비스에 닿지 않게 한다 — 앱 lifespan 기동을 기본으로 막고(HEAX_TEST_LIVE=1 이면 끈다) 런처의 두 디렉터리를 시험마다 임시로 돌린다
#
# 런처는 기동이 실패하면 상태 디렉터리에 되풀이 억제 기록(<앱>.launch-backoff)을 남긴다(10-17 cae00 리뷰 X4). 디렉터리를 돌리지
# 않는 시험(예: 모르는 스택)이 진짜 리포에 그것을 쓰면 다음 실행부터 'backoff' 를 받는다. 직접 돌리는 시험은 그 값이 이긴다.
#
# lifespan 가드(2026-10-31) — `with TestClient(app)` 은 앱 lifespan 을 그대로 돈다(test_api_smoke · test_pat · test_publish_route).
# 막지 않으면 기동 clone · scan · reconcile 이 이 리포의 integrations/ 를 이 박스의 실제 DB · Caddy · apptainer 로 돌려, SIF 를 짓고
# 같은 이름으로 떠 있는 heax_app_* 안에 서버를 하나 더 띄운다. 10-30 dev 에서 그 서버가 포트 충돌로 죽자 기동 실패 정리가 떠 있던
# 인스턴스 다섯을 멈췄다(docs/2026-10-31-prod-cutover/context-notes.md 「사고」). 기본으로 막는 것은 셋이다.
#   1. integrations 뿌리 둘 — 세션 내내 빈 임시 폴더다. 자기 뿌리를 monkeypatch 하는 시험은 그 값이 이긴다.
#   2. lifespan 기동의 clone · scan · reconcile — 앱이 기동하는 동안만 빈 대역으로 바꾸고 끝나면 되돌린다. 그 함수를 직접 부르거나
#      대역으로 바꿔 쓰는 시험(스캐너 · reconcile · 런처 시험)은 lifespan 밖이라 그대로 돈다. 기동하는 동안에는 시험의 대역도 덮는다.
#   3. 기동하는 동안 apptainer · Caddy admin 길목에 덫을 건다 — integrations 길(런처 · SIF 빌더)의 인스턴스 start · exec · stop ·
#      list · build 는 모두 apt_runner.local_apptainer_path 를, 라우트 호출은 모두 proxy_manager._admin_url 을 지난다. 가드가 모르는
#      새 기동 단계가 닿으면 부르기 전에 막고 lifespan 을 실패시킨다(기동 단계는 실패를 삼키므로 덫이 막은 것을 따로 모아 알린다).
# 세션 내내 덫을 걸지 않는 까닭 — apptainer 해석 · 라우트 시험이 그 길목을 직접 부르거나 대역으로 바꿔 쓴다.
# 오늘의 길(lifespan 이 이 박스의 integrations/ · DB · apptainer · Caddy 에 그대로 닿는다)로 돌리려면 HEAX_TEST_LIVE=1 이다.
# 세션 첫 줄에 어느 쪽인지 찍는다. 실제 Caddy · DB 를 쓰는 종단 시험은 integration 표지로 기본 실행에서 빠진다(pytest -m integration).
import os
import shutil
import tempfile
from contextlib import AsyncExitStack, ExitStack, asynccontextmanager
from pathlib import Path
from unittest import mock

import pytest

LIVE_ENV = "HEAX_TEST_LIVE"

# lifespan 이 기동 때 부르는 단계와 그 자리에 설 대역 — lifespan 이 결과를 읽는 꼴에 맞춘다.
_STARTUP_STEPS = {
    "app.services.integration_workspaces.ensure_all_cloned": lambda *a, **k: [],
    "app.services.integrations_scanner.scan_integrations": lambda *a, **k: [],
    "app.workers.integration_tasks.reconcile_integrations": lambda *a, **k: {"count": 0, "by_action": {}, "items": []},
}
# 기동하는 동안 덫을 거는 길목과 실패 글에 쓸 이름.
_TRIPWIRES = {
    "app.services.apt_runner.local_apptainer_path": "apptainer",
    "app.services.proxy_manager._admin_url": "Caddy admin",
}


def _guard_lifespan(original):
    """``original`` lifespan 을 감싸 기동하는 동안만 위 단계를 대역으로, 길목을 덫으로 바꾼다."""

    @asynccontextmanager
    async def guarded(app):
        tripped: list[str] = []

        def trip(what):
            def blocked(*args, **kwargs):
                tripped.append(what)
                raise RuntimeError(f"시험 가드 — lifespan 기동이 {what} 에 닿으려 했다")

            return blocked

        async with AsyncExitStack() as stack:
            with ExitStack() as patches:
                for target, stand_in in _STARTUP_STEPS.items():
                    patches.enter_context(mock.patch(target, stand_in))
                for target, what in _TRIPWIRES.items():
                    patches.enter_context(mock.patch(target, trip(what)))
                state = await stack.enter_async_context(original(app))
            if tripped:
                raise RuntimeError(
                    f"시험 가드 — lifespan 기동이 {' · '.join(dict.fromkeys(tripped))} 에 닿으려 해 부르기 전에 막았다. "
                    f"새 기동 단계면 app/tests/conftest.py 의 _STARTUP_STEPS 에 대역을 넣는다. "
                    f"이 박스의 실제 서비스로 돌리려면 {LIVE_ENV}=1."
                )
            yield state

    return guarded


def pytest_configure(config):
    # 첫 줄은 바로 내보낸다(flush) — stdout 이 파이프면 버퍼에 남았다가 첫 시험의 출력 포착 안에서 비워져, 통과한 시험과 함께 버려진다.
    if os.environ.get(LIVE_ENV) == "1":
        print(f"[HEAX 시험 가드] 꺼짐({LIVE_ENV}=1) — 앱 lifespan 이 이 박스의 integrations/ · DB · apptainer · Caddy 에 그대로 닿는다", flush=True)
        return

    from app import main
    from app.services import integrations_scanner
    from app.workers import integration_tasks

    empty = Path(tempfile.mkdtemp(prefix="heax-test-empty-integrations-"))
    config.add_cleanup(lambda: shutil.rmtree(empty, ignore_errors=True))
    mp = pytest.MonkeyPatch()
    config.add_cleanup(mp.undo)
    mp.setattr(integrations_scanner, "INTEGRATIONS_ROOT", empty)
    mp.setattr(integration_tasks, "_INTEGRATIONS_ROOT", empty)
    mp.setattr(main.app.router, "lifespan_context", _guard_lifespan(main.app.router.lifespan_context))
    # create_app() 으로 새로 만드는 앱도 감싼 lifespan 을 받는다.
    mp.setattr(main, "lifespan", _guard_lifespan(main.lifespan))
    print(
        f"[HEAX 시험 가드] 켜짐(기본) — lifespan 의 clone · scan · reconcile 을 건너뛰고 apptainer · Caddy admin 에 닿으면 실패한다. "
        f"integrations 뿌리는 빈 폴더 {empty}. 이 박스의 실제 서비스로 돌리려면 {LIVE_ENV}=1",
        flush=True,
    )


@pytest.fixture(autouse=True)
def _isolate_launcher_dirs(tmp_path_factory, monkeypatch):
    from app.services import integration_launcher

    base = tmp_path_factory.mktemp("launcher")
    (base / "state").mkdir()
    (base / "logs").mkdir()
    monkeypatch.setattr(integration_launcher, "STATE_DIR", base / "state")
    monkeypatch.setattr(integration_launcher, "LOG_DIR", base / "logs")
