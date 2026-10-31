# dist-to-drive.sh — 박스 덮어쓰기(manifest.local.yaml)가 있는 앱의 SIF 는 기본으로 싣지 않는지(HH-15 검토)
#
# SIF 캐시 열쇠(.sif.hash)는 효과 매니페스트 전체를 해시한다. SIF 를 지어 보내는 박스(dev)가 덮어쓴 앱의 열쇠는 받는 박스(추적
# 매니페스트로 셈한다)와 맞지 않아, cae00 은 캐시 미스로 보고 다시 지으려다 실패하고, 쓰지 않는 SIF 라도 깔리면 허브를 다시 띄운다.
# 원문 스크립트를 가짜 루트에서 돌린다 — rclone 은 대역(받은 명령을 적고, copy 는 로컬 폴더로 복사한다).
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

_FAKE_RCLONE = r'''#!/usr/bin/env bash
echo "$*" >> "$RCLONE_LOG"
args=(); for a in "$@"; do case "$a" in --*) ;; *) args+=("$a") ;; esac; done
p() { echo "$FAKE_DRIVE/${1#*:}"; }
case "${args[0]}" in
  copy) mkdir -p "$(p "${args[2]}")"; cp -r "${args[1]}"/. "$(p "${args[2]}")/" ;;
  cat) cat "$(p "${args[1]}")" 2>/dev/null || exit 1 ;;
  rcat) mkdir -p "$(dirname "$(p "${args[1]}")")"; cat > "$(p "${args[1]}")" ;;
  lsf) ls -1 "$(p "${args[1]}")" 2>/dev/null | sed 's#$#/#' ;;
  purge) : ;;
  *) echo "fake rclone: 모르는 명령 $*" >&2; exit 9 ;;
esac
'''


@pytest.fixture()
def box(tmp_path):
    root = tmp_path / "heax"
    (root / "deploy/apptainer").mkdir(parents=True)
    shutil.copy(ROOT / "deploy/apptainer/dist-to-drive.sh", root / "deploy/apptainer/")
    (root / ".env").write_text("HEAX_DRIVE_REMOTE=Fake:HEAXHub/dist\n", encoding="utf-8")
    sifs = root / "var/sifs"
    sifs.mkdir(parents=True)
    for slug in ("doc-forge", "step_forge"):
        (sifs / f"{slug}.sif").write_bytes(b"sif-" + slug.encode())
        (sifs / f"{slug}.sif.hash").write_text("k" * 64, encoding="utf-8")
        (root / "integrations" / slug / ".portal").mkdir(parents=True)
        (root / "integrations" / slug / ".portal" / "manifest.yaml").write_text(f"id: {slug}\n", encoding="utf-8")
    (root / "integrations/step_forge/.portal/manifest.local.yaml").write_text("launch: {mode: service}\n", encoding="utf-8")
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "rclone").write_text(_FAKE_RCLONE)
    (shim / "rclone").chmod(0o755)
    drive, log = tmp_path / "drive", tmp_path / "rclone.log"

    def run(**env):
        full = {"PATH": f"{shim}:/usr/bin:/bin", "HOME": str(tmp_path), "FAKE_DRIVE": str(drive), "RCLONE_LOG": str(log), **env}
        who = subprocess.run(["bash", "-c", "command -v rclone"], env=full, capture_output=True, text=True).stdout.strip()
        assert who == str(shim / "rclone"), f"가짜 rclone 이 아니라 {who} 가 불린다 — 실제 Drive 로 나간다"
        r = subprocess.run(["bash", str(root / "deploy/apptainer/dist-to-drive.sh")], cwd=root, env=full,
                           capture_output=True, text=True, timeout=120)
        latest = drive / "HEAXHub/dist/latest"
        return r, sorted(p.name for p in latest.iterdir()) if latest.exists() else []
    return run


def test_덮어쓴_앱의_SIF_는_기본으로_싣지_않고_까닭을_찍는다(box):
    r, latest = box()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "doc-forge.sif" in latest and "doc-forge.sif.hash" in latest, "덮어쓰지 않은 앱은 그대로 싣는다"
    assert "step_forge.sif" not in latest and "step_forge.sif.hash" not in latest, "이 박스의 효과 매니페스트로 지은 SIF 를 실었다"
    assert "○ app SIF step_forge.sif 싣지 않음" in r.stdout and "HEAX_DRIVE_APP_SIFS" in r.stdout
    assert "싣지 않은 app SIF(박스 덮어쓰기): step_forge" in r.stdout, "끝 요약에도 남아야 한다"


def test_이름을_대면_경고와_함께_싣는다(box):
    r, latest = box(HEAX_DRIVE_APP_SIFS="doc-forge step_forge")
    assert r.returncode == 0, r.stdout + r.stderr
    assert {"doc-forge.sif", "step_forge.sif", "step_forge.sif.hash"} <= set(latest)
    assert "⚠ app SIF step_forge.sif" in r.stderr and "doc-forge" not in r.stderr
