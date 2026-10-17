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
