# appdata-to-drive.sh — 암호 사본(.age)이 곁에 있는 평문과 빼 달라고 한 경로는 싣지 않고, 한 앱만 박스별 칸에 올릴 수 있는지(10-17 결정 2)
#
# 예전엔 HEAX_BACKUP_EXCLUDE 를 읽지 않아 리스크 DB 평문이 암호 사본과 함께 Drive 로 올라갔다(포털 build-all 도 이 스크립트를 부른다).
# 원문 스크립트를 가짜 루트에서 돌린다 — rclone 은 대역(받은 명령을 적고, copy 는 로컬 폴더로 복사한다).
import io
import os
import shutil
import sqlite3
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

_FAKE_RCLONE = r'''#!/usr/bin/env bash
echo "$*" >> "$RCLONE_LOG"
case "$1" in
  copy) mkdir -p "$FAKE_DRIVE/${3#*:}"; cp "$2" "$FAKE_DRIVE/${3#*:}/" ;;
  lsf) for i in 1 2 3 4 5 6 7; do echo "app-data-2026010${i}-000000Z/"; done ;;
  purge) : ;;
  *) echo "fake rclone: 모르는 명령 $*" >&2; exit 9 ;;
esac
'''


def _db(path: Path, rows: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE t (x INTEGER)")
    c.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    c.commit()
    c.close()


@pytest.fixture()
def box(tmp_path):
    root = tmp_path / "heax"
    (root / "deploy/apptainer").mkdir(parents=True)
    shutil.copy(ROOT / "deploy/apptainer/appdata-to-drive.sh", root / "deploy/apptainer/")
    (root / ".env").write_text("HEAX_DRIVE_REMOTE=Fake:HEAXHub/dist\n", encoding="utf-8")
    app = root / "var/app_data"
    _db(app / "hwax_risk/risk_review.db", 3)
    (app / "hwax_risk/risk_review.db.age").write_bytes(b"age-encrypted")
    (app / "hwax_risk/exports").mkdir()
    (app / "hwax_risk/exports/1.jsonl").write_text("{}")
    (app / "hwax_risk/exports.tar.age").write_bytes(b"age-encrypted")
    (app / "hwax_risk/secrets.env").write_text("HWAXRISK_BACKUP_KEY=age1x\n")
    (app / "hwax_risk/origin.json").write_text("{}")
    (app / "hwax_risk/risk_review.db.pre-migrate-v3-1").write_bytes(b"plain old copy")
    _db(app / "materialtwin_web/materialtwin.db", 2)
    (app / "voice_recorder/rec.wav").parent.mkdir(parents=True)
    (app / "voice_recorder/rec.wav").write_bytes(b"RIFF")
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "rclone").write_text(_FAKE_RCLONE)
    (shim / "rclone").chmod(0o755)
    drive, log = tmp_path / "drive", tmp_path / "rclone.log"

    def run(**env):
        full = {"PATH": f"{shim}:/usr/bin:/bin", "HOME": str(tmp_path), "FAKE_DRIVE": str(drive), "RCLONE_LOG": str(log), **env}
        who = subprocess.run(["bash", "-c", "command -v rclone"], env=full, capture_output=True, text=True).stdout.strip()
        assert who == str(shim / "rclone"), f"가짜 rclone 이 아니라 {who} 가 불린다 — 실제 Drive 로 나간다"
        r = subprocess.run(["bash", str(root / "deploy/apptainer/appdata-to-drive.sh")], cwd=root, env=full,
                           capture_output=True, text=True, timeout=120)
        return r, (log.read_text() if log.exists() else "")
    return run, drive


def _members(drive: Path, channel: str) -> set[str]:
    tar = drive / f"HEAXHub/{channel}/latest/app-data.tar.gz"
    with tarfile.open(tar) as t:
        return {m.name.lstrip("./") for m in t.getmembers() if m.isfile()}


def test_암호_사본이_곁에_있는_평문은_누가_불러도_싣지_않는다(box):
    run, drive = box
    r, _ = run()
    assert r.returncode == 0, r.stdout + r.stderr
    got = _members(drive, "app-data")
    assert "hwax_risk/risk_review.db.age" in got and "hwax_risk/exports.tar.age" in got and "hwax_risk/origin.json" in got
    assert not any(m.startswith(("hwax_risk/risk_review.db-", "hwax_risk/exports/")) for m in got), got
    assert "hwax_risk/risk_review.db" not in got, "암호 사본이 있는데 평문 DB 를 올렸다"
    assert "hwax_risk/secrets.env" not in got and "hwax_risk/risk_review.db.pre-migrate-v3-1" not in got
    assert {"materialtwin_web/materialtwin.db", "voice_recorder/rec.wav"} <= got, "다른 앱은 그대로 싣는다"
    assert "제외(암호 사본이 있다) hwax_risk/risk_review.db" in r.stdout


def test_빼_달라고_한_경로는_암호_사본이_없어도_뺀다(box):
    run, drive = box
    r, _ = run(HEAX_BACKUP_EXCLUDE="voice_recorder materialtwin_web/materialtwin.db")
    assert r.returncode == 0, r.stdout + r.stderr
    got = _members(drive, "app-data")
    assert not any(m.startswith("voice_recorder/") for m in got) and "materialtwin_web/materialtwin.db" not in got


def test_한_앱만_박스별_칸에_올리고_보존은_그_칸_안에서다(box):
    run, drive = box
    r, log = run(HEAX_APPDATA_ONLY="hwax_risk", HEAX_APPDATA_CHANNEL="risk-backup/box1")
    assert r.returncode == 0, r.stdout + r.stderr
    got = _members(drive, "risk-backup/box1")
    assert got and all(m.startswith("hwax_risk/") for m in got), got
    assert not (drive / "HEAXHub/app-data").exists(), "공유 칸(dev → cae00)에 올렸다"
    assert "스냅샷 실패" not in r.stdout, "다른 앱 DB 까지 뜨려다 실패를 찍었다"
    purged = [ln for ln in log.splitlines() if ln.startswith("purge ")]
    assert purged == ["purge Fake:HEAXHub/risk-backup/box1/app-data-20260101-000000Z/",
                      "purge Fake:HEAXHub/risk-backup/box1/app-data-20260102-000000Z/"], purged


@pytest.mark.parametrize("env", [{"HEAX_APPDATA_CHANNEL": "../dist"}, {"HEAX_APPDATA_ONLY": "../.."},
                                 {"HEAX_BACKUP_EXCLUDE": "../../etc"}])
def test_이상한_경로는_멈춘다(box, env):
    run, drive = box
    r, _ = run(**env)
    assert r.returncode == 1 and "이상하다" in r.stdout, r.stdout + r.stderr
    assert not drive.exists()
