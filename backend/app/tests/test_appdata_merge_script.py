# appdata-merge-from-drive.sh — SQLite 부속 파일을 운영에 심지 않고, 안전백업은 WAL 까지 담는지 · 출처 자연키에 발행처가 없는지(10-17 cae00 리뷰 X1 · X2 · X3)
#
# 스크립트 원문을 가짜 루트에 복사해 돌린다. Drive 는 가짜 rclone(PATH 맨 앞), 병합기는 대역이다 — 대역은 예전 병합기처럼
# staging 사본 옆에 -wal · -shm 을 만든다(mode=ro 로 WAL 모드 DB 를 열면 생기던 것).
import importlib.util
import io
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
APPT = ROOT / "deploy" / "apptainer"

_FAKE_RCLONE = r'''#!/usr/bin/env bash
# 가짜 rclone — copy <원격>/app-data/latest/app-data.tar.gz <대상>/ 만
[ "$1" = "copy" ] || { echo "fake rclone: 모르는 명령 $*" >&2; exit 9; }
cp "$FAKE_TAR" "$3/"
'''

_FAKE_MERGER = '''import sys
from pathlib import Path
src = Path(sys.argv[1])
Path(str(src) + "-wal").write_bytes(b"")                 # 예전 병합기가 staging 에 남기던 부속 파일
Path(str(src) + "-shm").write_bytes(b"\\0" * 32)
print("merged")
'''


def _db(path: Path, rows: int, *, wal: bool = False) -> None:
    c = sqlite3.connect(path)
    if wal:
        c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE t (x INTEGER)")
    c.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    c.commit()
    c.close()


def _tar(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in members.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture()
def box(tmp_path):
    root = tmp_path / "heax"
    (root / "deploy/apptainer").mkdir(parents=True)
    shutil.copy(APPT / "appdata-merge-from-drive.sh", root / "deploy/apptainer/")
    (root / "deploy/apptainer/_materialtwin_merge.py").write_text(_FAKE_MERGER, encoding="utf-8")
    (root / ".env").write_text("HEAX_DRIVE_REMOTE=fake:HEAXHub/dist\n", encoding="utf-8")
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "rclone").write_text(_FAKE_RCLONE)
    (shim / "rclone").chmod(0o755)
    env = {"PATH": f"{shim}:/usr/bin:/bin", "HOME": str(tmp_path), "FAKE_TAR": str(tmp_path / "app-data.tar.gz")}
    who = subprocess.run(["bash", "-c", "command -v rclone"], env=env, capture_output=True, text=True).stdout.strip()
    assert who == str(shim / "rclone"), f"가짜 rclone 이 아니라 {who} 가 불린다 — 실제 Drive 로 나간다"
    live = root / "var/app_data"

    def run(members: dict[str, bytes]):
        (tmp_path / "app-data.tar.gz").write_bytes(_tar(members))
        return subprocess.run(["bash", str(root / "deploy/apptainer/appdata-merge-from-drive.sh")], cwd=root, env=env,
                              capture_output=True, text=True, timeout=120)
    return live, tmp_path, run


def test_SQLite_부속_파일과_잠금_안전백업은_운영에_심지_않는다(box):
    live, tmp, run = box
    (live / "mt").mkdir(parents=True)
    _db(live / "mt/m.db", 3)                                   # 앱이 닫혀 있다 — 운영 옆에 -wal 이 없다
    src = tmp / "src.db"
    _db(src, 5)
    r = run({"mt/m.db": src.read_bytes(), "mt/m.db-wal": b"WAL from dev", "mt/m.db-shm": b"\0" * 32,
             "mt/m.db-journal": b"j", "mt/app.lock": b"1", "mt/m.db.pre-merge-19990101-000000Z": b"old",
             "models/weights.bin": b"weights"})
    assert r.returncode == 0, r.stdout + r.stderr
    for junk in ("m.db-wal", "m.db-shm", "m.db-journal", "app.lock", "m.db.pre-merge-19990101-000000Z"):
        assert not (live / "mt" / junk).exists(), f"{junk} 를 운영에 심었다 — 남의 -wal 은 다음 열 때 되살아난다"
    assert (live / "models/weights.bin").read_bytes() == b"weights", "모델 가중치 같은 비-DB 앱데이터는 그대로 심는다"
    assert sqlite3.connect(live / "mt/m.db").execute("SELECT count(*) FROM t").fetchone()[0] == 3


def test_안전백업은_앱이_쓰는_중인_DB_의_WAL_까지_담는다(box):
    """cp 사본은 -wal 에만 있는 최근 쓰기를 빠뜨린다 — 리스크 DB 사본이 스키마 2 · 자격 0행이었다(실제는 3 · 1행)."""
    live, tmp, run = box
    (live / "risk").mkdir(parents=True)
    _db(live / "risk/r.db", 1, wal=True)
    app = sqlite3.connect(live / "risk/r.db")                  # 앱이 쥐고 있다 — 새 행은 -wal 에만 있다
    app.execute("PRAGMA wal_autocheckpoint=0")
    app.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(100, 110)])
    app.commit()
    assert (live / "risk/r.db-wal").stat().st_size > 0
    try:
        src = tmp / "src.db"
        _db(src, 1)
        r = run({"risk/r.db": src.read_bytes()})
        assert r.returncode == 0, r.stdout + r.stderr
        backups = sorted((live / "risk").glob("r.db.pre-merge-*"))
        assert len(backups) == 1, backups
        n = sqlite3.connect(backups[0]).execute("SELECT count(*) FROM t").fetchone()[0]
        assert n == 11, f"안전백업이 WAL 의 10행을 빠뜨렸다({n}행)"
    finally:
        app.close()


def _merger_module(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["_materialtwin_merge.py", "src.db", "dst.db"])
    spec = importlib.util.spec_from_file_location("mt_merge_under_test", APPT / "_materialtwin_merge.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_출처_자연키는_두_목록이_같고_나중에_채워지는_발행처가_없다(monkeypatch):
    """발행처가 나중에 채워지자 같은 출처가 새 행이 되어 정정 4건이 중복으로 들어갔다(재료 254). 두 목록이 어긋나면 출처 병합 분기가 조용히 죽는다."""
    m = _merger_module(monkeypatch)
    plan_key = next(k for t, k, _ in m.PLAN if t == "source")
    assert plan_key == list(m.SOURCE_NATURAL_KEY)
    assert "publisher" not in plan_key and "authors" not in plan_key and "year" not in plan_key


def test_병합기는_staging_사본을_immutable_로_연다():
    """mode=ro 만이면 WAL 모드 DB 를 열며 staging 에 -wal · -shm 을 만든다 — 시드 제외와 함께 두 겹으로 막는다."""
    src = (APPT / "_materialtwin_merge.py").read_text(encoding="utf-8")
    assert 'sqlite3.connect(f"file:{SRC}?mode=ro&immutable=1", uri=True)' in src
