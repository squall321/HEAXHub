# start.sh 의 로그 — 백엔드 · 워커 · beat 는 덧붙여 쓰고 기동 때 커졌으면 한 벌만 넘기며, Caddy 는 스스로 돌리는지(10-17 결정 16)
#
# 예전엔 셋 다 `>` 로 열어 기동마다 앞 기록과 바로 앞에서 backend.log 에 덧붙인 alembic 출력까지 지웠고, `>`(O_APPEND 아님)라 포털
# logrotate 의 copytruncate 에도 못 넣었다. Caddy 는 셸이 `> /data/caddy.log` 로 받아 회전이 없었다(cae00 557MB).
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
START = (ROOT / "deploy/apptainer/start.sh").read_text(encoding="utf-8")


def test_백엔드_워커_beat_는_덧붙여_쓰고_기동_전에_크기를_본다():
    for name in ("backend", "worker", "beat"):
        assert not re.search(rf"[^>]> var/logs/{name}\.log", START), f"{name}.log 를 아직 > 로 연다 — 기동마다 지운다"
        assert f">> var/logs/{name}.log 2>&1 &" in START
        assert f"_keep_log var/logs/{name}.log" in START
    assert START.index("_keep_log var/logs/backend.log") < START.index("alembic upgrade head )"), "alembic 출력보다 먼저 넘겨야 한다"


def test_keep_log_는_64MB_를_넘었을_때만_한_벌_넘긴다(tmp_path):
    i = START.index("_keep_log() {")
    fn = START[i:START.index("\n}\n", i) + 3]
    big, small = tmp_path / "worker.log", tmp_path / "beat.log"
    with open(big, "wb") as f:
        f.truncate(64 * 1024 * 1024 + 1)                       # 성긴 파일 — 실제로 64MB 를 쓰지 않는다
    small.write_text("작다\n")
    r = subprocess.run(["bash", "-c", f"set -euo pipefail\n{fn}\n_keep_log '{big}'; _keep_log '{small}'; _keep_log '{tmp_path}/없다.log'; echo END"],
                       capture_output=True, text=True, timeout=30, env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 0 and "END" in r.stdout, r.stderr
    assert not big.exists() and (tmp_path / "worker.log.1").stat().st_size == 64 * 1024 * 1024 + 1
    assert small.read_text() == "작다\n" and not (tmp_path / "beat.log.1").exists()


def test_Caddy_는_로그를_스스로_돌린다():
    boot = json.loads((ROOT / "deploy/apptainer/caddy_bootstrap.json").read_text(encoding="utf-8"))
    w = boot["logging"]["logs"]["default"]["writer"]
    assert w["output"] == "file" and w["filename"] == "/data/caddy.log"
    assert w["roll_size_mb"] <= 100 and w["roll_keep"] >= 1 and w.get("roll_gzip", True)
    assert "> /data/caddy.log" not in START and ">> /data/caddy-stdio.log" in START, \
        "셸이 같은 파일을 > 로 쥐면 Caddy 의 회전과 엇갈려 앞을 덮는다"
    assert os.path.basename(w["filename"]) != "caddy-stdio.log"


WATCHDOG = (ROOT / "scripts/watchdog.sh").read_text(encoding="utf-8")


def test_감시도_로그를_덧붙여_쓴다():
    """결정 16 은 start.sh 만 고쳤다 — 매분 도는 감시가 백엔드를 다시 띄울 때 `>` 로 열어, 기동 중에 죽은 앞 프로세스의 기록을 지웠다(10-18 dev)."""
    for name in ("backend", "worker", "beat"):
        assert not re.search(rf"[^>]> var/logs/{name}\.log", WATCHDOG), f"감시가 {name}.log 를 아직 > 로 연다"
        assert f">> var/logs/{name}.log 2>&1 &" in WATCHDOG


def _backend_block() -> str:
    i = WATCHDOG.index("# Backend\nif confirm_down")
    return WATCHDOG[i:WATCHDOG.index("# Celery worker", i)]


def test_감시는_막_띄운_백엔드를_기동_중에_죽이지_않는다(tmp_path):
    """start.sh 가 띄운 백엔드는 기동 reconcile 동안 /health 가 1분 넘게 안 뜬다 — 다음 분의 감시가 그것을 죽이고 두 번 띄웠다(10-18 dev 실측).
    backend.rev(띄운 즉시 쓰는 기록)의 시각부터 STARTUP_GRACE 초 동안은 건드리지 않는다. pkill · nohup 은 셸 함수로 막아 진짜를 부르지 않는다."""
    (tmp_path / "var/run").mkdir(parents=True)
    rev = tmp_path / "var/run/backend.rev"
    stubs = ("confirm_down() { return 0; }\nhttp_ok() { return 1; }\nlog() { echo \"LOG $*\"; }\n"
             "backoff_gate() { echo GATE; return 1; }\nhealthy_reset() { echo HEALTHY; }\n"
             "pkill() { echo PKILL; }\nnohup() { echo NOHUP; }\nrecovered=()\nAPI_PORT=4040\nSTARTUP_GRACE=300\n")

    def run(age: int | None) -> str:
        if age is None:
            rev.unlink(missing_ok=True)
        else:
            rev.write_text("abc")
            t = __import__("time").time() - age
            os.utime(rev, (t, t))
        r = subprocess.run(["bash", "-c", "set -uo pipefail\n" + stubs + _backend_block()], cwd=tmp_path,
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
        assert "PKILL" not in r.stdout and "NOHUP" not in r.stdout, "시험은 다시 띄우는 갈래에 들어가지 않는다(backoff_gate 가 막는다)"
        return r.stdout

    assert "기다린다" in run(10) and "GATE" not in run(10), "막 띄운 백엔드 — 건드리지 않는다"
    assert "GATE" in run(301) and "기다린다" not in run(301), "기동 여유가 지나면 종전대로 복구를 따진다"
    assert "GATE" in run(None), "backend.rev 가 없으면(start.sh 를 거치지 않은 박스) 종전대로"


def test_감시가_다시_띄우면_띄운_시각을_새로_찍는다():
    blk = _backend_block()
    i = blk.index(">> var/logs/backend.log")
    assert "touch var/run/backend.rev" in blk[i:blk.index('backoff_mark "backend"', i)], \
        "감시 자신이 띄운 백엔드도 다음 분의 감시가 기동 중에 죽이지 않게"
