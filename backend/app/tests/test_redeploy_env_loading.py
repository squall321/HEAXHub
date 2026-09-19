# redeploy-app.sh 가 자동 경로와 같은 호스트 env 를 싣는지 — 앱 env 가 조용히 빠지던 자리(2026-09-19)
#
# 런처는 **os.environ 의 `APPTAINERENV_*` 만** 컨테이너로 넘긴다(apt_runner:214·289). 스크립트를
# 셸에서 바로 부르면 앱은 멀쩡히 뜨는데 앱 env 만 빠져서, 실패가 정상 기동과 똑같이 생긴다.
# 그래서 **스크립트의 실제 텍스트**를 떼어 가짜 루트에서 돌린다 — 블록을 지우면 이 시험이 깨진다.
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "deploy" / "apptainer" / "redeploy-app.sh"
BEGIN = "# ⚠ 자동 경로와 **같은** 호스트 env 를 싣는다."


def _env_block() -> str:
    src = SCRIPT.read_text(encoding="utf-8")
    i = src.find(BEGIN)
    assert i > 0, f"env 로딩 블록을 못 찾았다 — 앵커가 바뀌었으면 이 시험을 먼저 고쳐라: {BEGIN!r}"
    j = src.find("\nfi\n", i)
    assert j > i, "블록이 fi 로 닫히지 않는다"
    return src[i : j + 4]


def _run(script: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", f"set -euo pipefail\nROOT={cwd}\n{script}"],
                          capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin"})


def test_env_이_있으면_APPTAINERENV_가_자식에게_실린다(tmp_path):
    (tmp_path / ".env").write_text(
        # 따옴표 친 공백값(source 되는 .env 는 이래야 한다)과 **정의되지 않은 참조** —
        # set -u 를 안 풀면 두 번째 줄에서 즉사한다(실제 .env 에 있는 모양이다).
        'APPTAINERENV_SENTINEL=v1\nOTHER="a b"\nMAYBE=${NOT_DEFINED_ANYWHERE}\n', encoding="utf-8")
    r = _run(_env_block() + '\necho "GOT=[${APPTAINERENV_SENTINEL:-}]"', tmp_path)
    assert r.returncode == 0, r.stderr
    assert "GOT=[v1]" in r.stdout, r.stdout + r.stderr
    assert "APPTAINERENV_* 1개" in r.stdout, "몇 개가 넘어가는지 사람이 볼 수 있어야 한다"
    assert "v1" not in r.stdout.replace("GOT=[v1]", ""), "값 자체를 운영 출력에 뿌리지 않는다"


def test_env_이_없으면_조용히_넘어가지_않는다(tmp_path):
    r = _run(_env_block(), tmp_path)
    assert r.returncode == 0, "없다고 배포를 막을 것까진 아니다"
    assert "WARN" in r.stderr and ".env" in r.stderr, "빠진 것을 말해야 한다(보는 자리는 stderr)"


def test_스크립트가_런처를_부르기_전에_env_를_읽는다():
    """순서가 뒤집히면(파이썬 먼저) 값이 안 실린다 — 텍스트 순서로 고정한다."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert src.find(BEGIN) < src.index('"$PY" - <<'), "env 로딩이 런처 호출보다 뒤에 있다"
