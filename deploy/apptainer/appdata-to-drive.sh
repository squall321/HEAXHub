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
# 칸(HEAX_APPDATA_CHANNEL)과 앱(HEAX_APPDATA_ONLY) — 리스크 앱의 매일 암호 백업처럼 한 앱만 박스별 칸에 올릴 때(HWAXRisk
# backend/scripts/backup-to-drive.sh, 10-17 결정 2). 기본 칸 app-data 는 dev 가 올리고 cae00 이 병합해 받는 공유 칸이다 — 다른 박스가
# 거기 올리면 그 흐름을 뒤섞는다.
CHANNEL="${HEAX_APPDATA_CHANNEL:-app-data}"
ONLY="${HEAX_APPDATA_ONLY:-}"
_bad_rel() { case "$1" in ''|/*|..|../*|*/..|*/../*) return 0 ;; *) return 1 ;; esac; }
_bad_rel "$CHANNEL" && { echo "✗ HEAX_APPDATA_CHANNEL 이 이상하다: $CHANNEL"; exit 1; }
DEST="$REMOTE/$CHANNEL"
RETAIN="${HEAX_DRIVE_RETAIN:-$(env_get HEAX_DRIVE_RETAIN)}"; RETAIN="${RETAIN:-5}"
RCLONE="$(command -v rclone || true)"
[ -n "$RCLONE" ] || { echo "✗ rclone 미설치"; exit 1; }

APPDATA="$ROOT_DIR/var/app_data"
[ -d "$APPDATA" ] && [ -n "$(ls -A "$APPDATA" 2>/dev/null)" ] || { echo "· var/app_data 비어있음 — 백업 대상 없음"; exit 0; }

TS="$(date -u +%Y%m%d-%H%M%SZ)"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
SNAP="$STAGE/app_data"; mkdir -p "$SNAP"
if [ -z "$ONLY" ]; then
  cp -a "$APPDATA/." "$SNAP/"
else
  for a in $ONLY; do
    _bad_rel "$a" && { echo "✗ HEAX_APPDATA_ONLY 의 앱 이름이 이상하다: $a"; exit 1; }
    [ -d "$APPDATA/$a" ] || { echo "✗ var/app_data/$a 가 없다"; exit 1; }
    mkdir -p "$SNAP/$(dirname "$a")"; cp -a "$APPDATA/$a" "$SNAP/$a"
  done
fi

# SQLite DB 는 WAL 사본 대신 .backup 원자적 스냅샷으로 교체(쓰기 중에도 일관)
python3 - "$APPDATA" "$SNAP" <<'PY'
import sys, os, glob, sqlite3
src_root, snap_root = sys.argv[1], sys.argv[2]
for dst in glob.glob(os.path.join(snap_root, "**", "*.db"), recursive=True):   # 사본에 든 것만(HEAX_APPDATA_ONLY 이면 그 앱만)
    rel = os.path.relpath(dst, snap_root); db = os.path.join(src_root, rel)
    for ext in ("-wal", "-shm"):                 # WAL/SHM 사본은 스냅샷에 불필요
        if os.path.exists(dst + ext): os.remove(dst + ext)
    try:
        s = sqlite3.connect(f"file:{db}?mode=ro", uri=True); d = sqlite3.connect(dst)
        s.backup(d); d.close(); s.close()
        print(f"  · 스냅샷 {rel}")
    except Exception as e:                        # 스냅샷 실패 시 원본 사본 유지(비치명적)
        print(f"  ⚠ {rel} 스냅샷 실패({e}) — 원본 사본 유지")
PY

# 시크릿은 tar 에 넣지 않는다 — cred.key(Fernet 대칭키)와 risk_review.db 가 한 tarball 로 나가면
# `Fernet(cred.key).decrypt(portal_pat_enc)` 로 모든 사용자 PAT 원문이 복구된다. secrets.env 도 같은 등급이다.
# knobs.env 는 비밀은 아니지만 박스마다 다르게 정하는 설정(예: HWAXRISK_REVIEW_ENABLED)이라
# 올라가면 다른 운영 박스가 받아 심을 수 있다 — 리스크 앱 검토 스위치 값이 틀리면 앱이 기동하지
# 않는다(cae00 운영 수정요청 2026-10-27 HH-05).
find "$SNAP" \( -name 'cred.key' -o -name 'secrets.env' -o -name 'knobs.env' \) -type f -print -delete \
  | sed 's|^|  · 제외 |' || true

# 죽은 DB 백업 사본도 넣지 않는다. 이름이 `.db` 로 끝나지 않아 위 스냅샷 루프도, 복원 측
# appdata-merge-from-drive.sh 의 .db merge 루프도 이들을 건너뛴다 — 대신 merge 의 '비-DB 시드'
# 루프로 흘러가 `[ -f "$live" ] && continue` 로 cae00 에 한 번 심기면 영구히 남는다.
# 실측(2026-09-01): materialtwin_web 의 .pre-w5xxx / .pre-merge 사본 13개 = 967MB 가 tar 를
# 5배 넘게 부풀리고 있었다. 원본은 건드리지 않는다 — $SNAP 은 사본이다.
find "$SNAP" \( -name '*.db.pre-*' -o -name '*.db.bak*' \) -type f -delete -print \
  | sed 's|^|  · 제외(죽은 백업) |' || true

# 빼 달라고 한 경로(HEAX_BACKUP_EXCLUDE — var/app_data 기준, 공백으로 가름)와, 암호 사본(*.age)이 곁에 있는 평문은 싣지 않는다 — 누가
# 부르든(포털 build-all 도 이 스크립트를 부른다) 평문이 나가지 않게. 예전엔 이 변수를 읽지 않아 리스크 DB 평문이 암호 사본과 함께
# 올라갔다(10-17 결정 2). `<x>.age` 의 평문은 `<x>`(파일 — -wal · -shm · -journal 도) 또는 `<x>` 가 `.tar` 로 끝나면 그 이름의 폴더다.
_drop() {  # $1=SNAP 안 경로 · $2=까닭
  [ -e "$1" ] || return 0
  rm -rf -- "$1" "$1-wal" "$1-shm" "$1-journal"; echo "  · 제외($2) ${1#"$SNAP/"}"
}
for rel in ${HEAX_BACKUP_EXCLUDE:-}; do
  _bad_rel "$rel" && { echo "✗ HEAX_BACKUP_EXCLUDE 의 경로가 이상하다: $rel"; exit 1; }
  _drop "$SNAP/$rel" 요청
done
while IFS= read -r enc; do
  plain="${enc%.age}"
  _drop "$plain" "암호 사본이 있다"
  [ "${plain%.tar}" != "$plain" ] && [ -d "${plain%.tar}" ] && _drop "${plain%.tar}" "암호 사본이 있다"
done < <(find "$SNAP" -name '*.age' -type f 2>/dev/null)
tar -czf "$STAGE/app-data.tar.gz" -C "$SNAP" .
SZ="$(du -h "$STAGE/app-data.tar.gz" | cut -f1)"
echo "→ 업로드 $DEST/app-data-$TS/  (+ latest/)  [$SZ]"
"$RCLONE" copy "$STAGE/app-data.tar.gz" "$DEST/app-data-$TS/"
"$RCLONE" copy "$STAGE/app-data.tar.gz" "$DEST/latest/"

# 보존정책 — 최신 RETAIN 개만 유지
mapfile -t OLD < <("$RCLONE" lsf --dirs-only "$DEST/" 2>/dev/null | grep '^app-data-' | sort | head -n "-${RETAIN}") || true
for d in "${OLD[@]:-}"; do [ -n "$d" ] && "$RCLONE" purge "$DEST/$d" 2>/dev/null || true; done
echo "✓ app-data → Drive ($TS)"
