#!/usr/bin/env bash
# heax 앱의 런타임 데이터(var/app_data — 예: materialtwin 재료 DB)를 Drive로 백업한다.
# dist-to-drive(SPA)·mirror-to-drive(npm/pip)엔 없는 "앱 데이터" 표면을 커버 — 코드 git pull 로는
# 안 오는 SQLite 데이터를 cae00 이 복원할 수 있게 한다. SQLite 는 .backup 로 원자적 스냅샷 후 tar.
#   HEAX_DRIVE_REMOTE=<remote>:HEAXHub/dist  (.env, dist-*.sh 와 공유 — app-data/ 하위폴더로 저장)
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
env_get() { [ -f .env ] && sed -n "s/^$1=//p" .env | tail -1 | sed 's/^["'"'"']//; s/["'"'"']$//'; }

REMOTE="${HEAX_DRIVE_REMOTE:-$(env_get HEAX_DRIVE_REMOTE)}"
[ -n "$REMOTE" ] || { echo "✗ HEAX_DRIVE_REMOTE 미설정 (.env, 예: HeaxDrive:HEAXHub/dist)"; exit 1; }
REMOTE="${REMOTE%/}"; REMOTE="${REMOTE%/dist}"          # dist 형제로 app-data/ 사용
DEST="$REMOTE/app-data"
RETAIN="${HEAX_DRIVE_RETAIN:-$(env_get HEAX_DRIVE_RETAIN)}"; RETAIN="${RETAIN:-5}"
RCLONE="$(command -v rclone || true)"
[ -n "$RCLONE" ] || { echo "✗ rclone 미설치"; exit 1; }

APPDATA="$ROOT_DIR/var/app_data"
[ -d "$APPDATA" ] && [ -n "$(ls -A "$APPDATA" 2>/dev/null)" ] || { echo "· var/app_data 비어있음 — 백업 대상 없음"; exit 0; }

TS="$(date -u +%Y%m%d-%H%M%SZ)"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
SNAP="$STAGE/app_data"; mkdir -p "$SNAP"
cp -a "$APPDATA/." "$SNAP/"

# SQLite DB 는 WAL 사본 대신 .backup 원자적 스냅샷으로 교체(쓰기 중에도 일관)
python3 - "$APPDATA" "$SNAP" <<'PY'
import sys, os, glob, sqlite3
src_root, snap_root = sys.argv[1], sys.argv[2]
for db in glob.glob(os.path.join(src_root, "**", "*.db"), recursive=True):
    rel = os.path.relpath(db, src_root); dst = os.path.join(snap_root, rel)
    for ext in ("-wal", "-shm"):                 # WAL/SHM 사본은 스냅샷에 불필요
        if os.path.exists(dst + ext): os.remove(dst + ext)
    try:
        s = sqlite3.connect(f"file:{db}?mode=ro", uri=True); d = sqlite3.connect(dst)
        s.backup(d); d.close(); s.close()
        print(f"  · 스냅샷 {rel}")
    except Exception as e:                        # 스냅샷 실패 시 원본 사본 유지(비치명적)
        print(f"  ⚠ {rel} 스냅샷 실패({e}) — 원본 사본 유지")
PY

tar -czf "$STAGE/app-data.tar.gz" -C "$SNAP" .
SZ="$(du -h "$STAGE/app-data.tar.gz" | cut -f1)"
echo "→ 업로드 $DEST/app-data-$TS/  (+ latest/)  [$SZ]"
"$RCLONE" copy "$STAGE/app-data.tar.gz" "$DEST/app-data-$TS/"
"$RCLONE" copy "$STAGE/app-data.tar.gz" "$DEST/latest/"

# 보존정책 — 최신 RETAIN 개만 유지
mapfile -t OLD < <("$RCLONE" lsf --dirs-only "$DEST/" 2>/dev/null | grep '^app-data-' | sort | head -n "-${RETAIN}") || true
for d in "${OLD[@]:-}"; do [ -n "$d" ] && "$RCLONE" purge "$DEST/$d" 2>/dev/null || true; done
echo "✓ app-data → Drive ($TS)"
