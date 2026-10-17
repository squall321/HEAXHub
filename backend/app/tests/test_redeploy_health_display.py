# redeploy-app.sh 의 헬스 표시 — 판정(500 미만 하나면 산다)은 런처와 같고, 보이는 것은 2xx · 3xx 를 먼저 고르는지(10-17)
#
# 예전엔 처음 500 미만에서 멈춰, 헬스가 /api/health 인 앱(리스크 앱)도 첫 후보 /apps/<앱>/api/health 의 404 로 'health=404' 를
# 찍었다 — 앱은 정상인데 사람은 실패로 읽는다. 판정 구획을 원문 그대로 떼어 작은 HTTP 서버에 대고 돌린다.
import http.server
import subprocess
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SRC = (ROOT / "deploy/apptainer/redeploy-app.sh").read_text(encoding="utf-8")


def _probe_block() -> str:
    i = SRC.index('  BARE="${BASE%/}"')
    j = SRC.index("    exit 1\n  fi\n", i) + len("    exit 1\n  fi\n")
    return SRC[i:j]


@pytest.fixture()
def server():
    routes: dict[str, int] = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(routes.get(self.path, routes.get("*", 404)))
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], routes
    srv.shutdown()
    srv.server_close()


def _run(port: int) -> subprocess.CompletedProcess:
    script = f"set -uo pipefail\nPORT={port}\nHP=/api/health\nBASE=/apps/hwax_risk\n{_probe_block()}\necho END\n"
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60,
                          env={"PATH": "/usr/bin:/bin"})


def test_헬스가_뒤_후보에_있으면_그것을_보인다(server):
    port, routes = server
    routes["/api/health"] = 200                                 # /apps/hwax_risk/api/health 는 404
    r = _run(port)
    assert r.returncode == 0 and "health(/api/health)=200" in r.stdout, r.stdout + r.stderr
    assert "=404" not in r.stdout


def test_4xx_만이면_성공이되_헬스_경로를_못_찾았다고_말한다(server):
    port, _ = server
    r = _run(port)
    assert r.returncode == 0 and "앱은 응답한다" in r.stdout and "2xx 를 내는 헬스 경로는 못 찾았다" in r.stdout, r.stdout + r.stderr


def test_전부_500_이면_예전처럼_실패다(server):
    port, routes = server
    routes["*"] = 500
    r = _run(port)
    assert r.returncode == 1 and "후보 경로 4개 모두 실패" in r.stderr, r.stdout + r.stderr
