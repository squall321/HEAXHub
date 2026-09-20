# 인스턴스 판정이 "모름"을 "없음"으로 읽지 않는지 — 그 오판이 되돌리기 어려운 일을 시킨다
"""왜 — `apptainer instance list | awk | grep -qx` 는 `pipefail` 아래서 **떠 있는 인스턴스를 "없다"**
로 읽는다(`grep -q` 조기 종료 → apptainer SIGPIPE 141). 실측(같은 꼴의 KooRemapper 감독자, 2026-09-19):
유휴 400회 중 0회지만 `instance list` 를 6개 동시에 돌리는 경합에서 **600회 중 88회(14.7%)**.
이 박스는 워커가 45초마다 훑고 배포가 수시로 도는 바로 그 상태다.

이 판정으로 하는 일이 문제다 — `boot.sh` 는 "없음" 이면 `~/.apptainer/instances/<이름>.json` 을 지우고
(떠 있는 인스턴스면 관리 불능이 된다), `stop.sh` 는 "없음" 이면 정지를 건너뛴다(내렸다고 말하고 안 내린다).

⚠ 한계 — `boot.sh`·`stop.sh` 는 `--once` 같은 무해한 실행 모드가 없어 여기서는 **분기 조건만** 본다.
실제 동작은 `instance_running` 시험(위 셋)이 덮는다.
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APPT = ROOT / "deploy" / "apptainer"


def _func(name: str, path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    i = src.index(f"{name}() {{")
    j = src.index("\n}\n", i)
    return src[i:j + 3]


def _stub(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _probe(tmp_path, stub_body: str) -> str:
    """가짜 apptainer 를 PATH 앞에 두고 `instance_running` 을 **실제로** 돌린다."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _stub(bin_dir / "apptainer", stub_body)
    script = (f"set -euo pipefail\nPATH={bin_dir}:/usr/bin:/bin\n"
              + _func("instance_running", APPT / "_common.sh")
              + '\ninstance_running heax-pg && rc=0 || rc=$?\necho "rc=$rc"\n')
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin"})
    return r.stdout + r.stderr


def test_a_running_instance_is_not_reported_as_gone(tmp_path):
    """목록이 파이프 버퍼(64KiB)보다 크면 옛 구현은 떠 있는 것을 "없다"로 읽었다."""
    body = ('echo "INSTANCE NAME    PID     IP    IMAGE"\n'
            'echo "heax-pg          1       -     x.sif"\n'          # 찾는 것이 맨 앞
            'for i in $(seq 1 4000); do echo "filler$i  2  -  y.sif"; done\n')
    assert "rc=0" in _probe(tmp_path, body), "떠 있는 인스턴스를 못 봤다"


def test_a_failed_listing_is_unknown_not_absent(tmp_path):
    """조회 실패(2)와 부재(1)를 가른다 — 뭉개면 '모름'이 삭제·건너뜀의 근거가 된다."""
    assert "rc=2" in _probe(tmp_path, 'echo "cannot read instance dir" >&2\nexit 255\n')


def test_an_absent_instance_is_still_absent(tmp_path):
    """반대로 망가뜨리지 않았는지 — 정말 없으면 1 이어야 기동·정리가 돈다."""
    assert "rc=1" in _probe(tmp_path, 'echo "INSTANCE NAME PID"\necho "other 1"\n')


def test_boot_only_deletes_state_when_certainly_absent():
    """**확실히 없을 때(1)만** 상태 파일을 지운다 — 2(모름)에 지우면 떠 있는 인스턴스가 관리 불능."""
    src = (APPT / "boot.sh").read_text(encoding="utf-8")
    assert 'instance_running "$inst" 2>/dev/null; _ir_rc=$?' in src, "판정 rc 를 안 받는다"
    assert '[ "$_ir_rc" -eq 1 ]' in src, "'없음(1)' 이 아니라 '0이 아님' 으로 지우고 있다"
    assert 'if ! instance_running "$inst" 2>/dev/null; then' not in src, "옛 분기가 남아 있다"


def test_stop_skips_only_when_certainly_absent():
    """모르면 **내려 본다** — 없는 것을 stop 하는 건 무해하고, 건너뛰면 거짓 보고가 된다."""
    src = (APPT / "stop.sh").read_text(encoding="utf-8")
    assert 'instance_running "$inst"; _ir_rc=$?' in src, "판정 rc 를 안 받는다"
    assert '[ "$_ir_rc" -ne 1 ]' in src, "'없음이 아닐 때' 가 아니라 다른 조건으로 내리고 있다"
    assert "grep -qx" not in src, "옛 파이프 판정이 남아 있다"
