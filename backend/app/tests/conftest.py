# 시험이 박스의 var/integration_state · var/logs 에 쓰지 않게 런처의 두 디렉터리를 시험마다 임시로 돌린다
#
# 런처는 기동이 실패하면 상태 디렉터리에 되풀이 억제 기록(<앱>.launch-backoff)을 남긴다(10-17 cae00 리뷰 X4). 디렉터리를 돌리지
# 않는 시험(예: 모르는 스택)이 진짜 리포에 그것을 쓰면 다음 실행부터 'backoff' 를 받는다. 직접 돌리는 시험은 그 값이 이긴다.
import pytest


@pytest.fixture(autouse=True)
def _isolate_launcher_dirs(tmp_path_factory, monkeypatch):
    from app.services import integration_launcher

    base = tmp_path_factory.mktemp("launcher")
    (base / "state").mkdir()
    (base / "logs").mkdir()
    monkeypatch.setattr(integration_launcher, "STATE_DIR", base / "state")
    monkeypatch.setattr(integration_launcher, "LOG_DIR", base / "logs")
