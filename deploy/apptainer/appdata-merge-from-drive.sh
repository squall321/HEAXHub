#!/usr/bin/env bash
# Drive 의 heax 앱 데이터(app-data tar)를 cae00 운영 var/app_data 에 MERGE(비파괴 병합)한다.
# appdata-from-drive.sh(덮어쓰기/보존)와 달리, dev 신규 데이터를 자연키 매칭 + id 재매핑으로
# cae00 운영 DB에 '추가'하고 cae00 자체 등록분은 유지한다(FK 무결성 보장 — dev 실증 완료).
# 라이브 DB가 없으면(첫 배포) 시드 복사. SQLite 전용(materialtwin 등).
#
#   bash deploy/apptainer/appdata-merge-from-drive.sh
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$ROOT_DIR"
APPT_DIR="$ROOT_DIR/deploy/apptainer"
MERGER="$APPT_DIR/_materialtwin_merge.py"
env_get() { [ -f .env ] && sed -n "s/^$1=//p" .env | tail -1 | sed 's/^["'"'"']//; s/["'"'"']$//'; }

REMOTE="${HEAX_DRIVE_REMOTE:-$(env_get HEAX_DRIVE_REMOTE)}"
[ -n "$REMOTE" ] || { echo "· HEAX_DRIVE_REMOTE 미설정 — merge 생략"; exit 0; }
REMOTE="${REMOTE%/}"; REMOTE="${REMOTE%/dist}"; DEST="$REMOTE/app-data"
RCLONE="$(command -v rclone || true)"; [ -n "$RCLONE" ] || { echo "· rclone 없음 — merge 생략"; exit 0; }

# 영구 캐시(SE-8) — 임시 디렉터리에 받으면 매번 50MB 를 새로 받았다(11-03 · 11-05 모두 0건 추가). 캐시에 받으면 rclone 이 안 바뀐
# tar 를 건너뛴다. 풀기는 매번 임시 디렉터리에 한다(로컬이라 싸다) — 아래 DB 밖 심기가 tar 가 같아도 계속 돌 수 있게.
CACHE="${HEAX_APPDATA_CACHE:-$APPT_DIR/cache/.drive-appdata}"; mkdir -p "$CACHE"
TAR="$CACHE/app-data.tar.gz"
"$RCLONE" copy "$DEST/latest/app-data.tar.gz" "$CACHE/" 2>/dev/null || true
[ -f "$TAR" ] || { echo "· Drive 에 app-data 없음 — merge 생략(첫 배포면 정상)"; exit 0; }
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/src"; tar -xzf "$TAR" -C "$STAGE/src"

APPDATA="$ROOT_DIR/var/app_data"; mkdir -p "$APPDATA"
# 지난번에 병합까지 끝낸 tar 의 해시 — 같으면 안전백업 · DB 병합을 건너뛴다(병합은 멱등이라 다시 돌아도 0건이고, 사본만 쌓였다).
TAR_SHA="$(sha256sum "$TAR" | cut -d' ' -f1)"
MERGED_MARK="$CACHE/merged.sha256"
SKIP_MERGE=0
[ -f "$MERGED_MARK" ] && [ "$(cat "$MERGED_MARK")" = "$TAR_SHA" ] && SKIP_MERGE=1
rc=0
[ "$SKIP_MERGE" = 1 ] && echo "  · app-data tar 가 지난 병합 때와 같다(${TAR_SHA:0:12}) — 안전백업 · DB 병합 건너뜀"
# dev 스냅샷의 각 *.db 를 운영의 같은 상대경로 DB 에 merge
while [ "$SKIP_MERGE" = 0 ] && IFS= read -r sdb; do
  rel="${sdb#"$STAGE/src/"}"
  live="$APPDATA/$rel"
  if [ ! -f "$live" ]; then
    mkdir -p "$(dirname "$live")"; cp "$sdb" "$live"
    echo "  · $rel: 라이브 없음 → 시드 복사(첫 배포)"; continue
  fi
  # 운영 직전 안전백업(롤백용, 로컬) — cp 가 아니라 SQLite 백업 API 다. 앱이 쓰는 중이면 최근 쓰기가 -wal 에만 있어 cp 사본은 그것을
  # 빠뜨린다(10-17 cae00 리뷰 X2 — 리스크 DB 사본이 스키마 2 · 자격 0행이었다, 실제는 3 · 1행).
  python3 - "$live" "$live.pre-merge-$(date -u +%Y%m%d-%H%M%SZ)" <<'PY' || echo "    ⚠ $rel: 안전백업 실패 — 롤백 사본 없이 merge 한다"
import sqlite3, sys
s = sqlite3.connect(sys.argv[1]); d = sqlite3.connect(sys.argv[2])
s.backup(d); d.close(); s.close()
PY
  echo "  → merge: $rel"
  if out="$(python3 "$MERGER" "$sdb" "$live" 2>&1)"; then
    echo "    ✓ $out"
  else
    echo "    ✗ merge 실패(운영 무손상 — 안전백업 존재): $out"; rc=1
  fi
done < <(find "$STAGE/src" -name "*.db" 2>/dev/null)

# 비-DB 앱데이터(모델 가중치·커브 parquet·레지스트리 json 등)는 위 .db merge 가 통째로 누락한다.
# 라이브에 '없는' 파일만 시드 복사(비파괴 — 기존 파일은 건드리지 않는다). 이게 없어서:
#   · thermal_shock predict_sed → /data/models 비어 'E400 학습된 모델이 없습니다'
#   · materialtwin 응력-변형 커브(*.parquet) 누락 → 커브 플롯/피팅 실패
while IFS= read -r sf; do
  rel="${sf#"$STAGE/src/"}"
  live="$APPDATA/$rel"
  [ -f "$live" ] && continue   # 기존 파일 보존(merge 대상 아님)
  mkdir -p "$(dirname "$live")"
  if cp "$sf" "$live" 2>/dev/null; then echo "  · seed(non-db): $rel"; fi
# SQLite 부속 파일(-wal · -shm · -journal) · 잠금 · 안전백업은 심지 않는다 — 운영 DB 옆에 남의 -wal 이 놓이면 SQLite 는 그것이 이 DB 의
# 것인지 가리지 못하고 다음 열 때 되살린다(10-17 cae00 리뷰 X1 — 그때는 병합기가 staging 에서 만든 빈 파일이라 무해했다).
# knobs.env 도 심지 않는다 — 올리는 쪽(appdata-to-drive.sh)이 빼는 게 1차 방어지만, 과거에
# 이미 올라간 사본이 있을 수 있으니 받는 쪽도 막는다(cae00 운영 수정요청 2026-10-27 HH-05).
done < <(find "$STAGE/src" -type f ! -name "*.db" ! -name "*-wal" ! -name "*-shm" ! -name "*-journal" \
           ! -name "*.lock" ! -name "*.pre-merge-*" ! -name "knobs.env" 2>/dev/null)

# 안전백업은 DB 마다 최근 N개만 남긴다(SE-8 — 11-24 cae00 에 348개 · 8GB 가 쌓였다). 하나는 늘 남긴다.
KEEP="${HEAX_APPDATA_PREMERGE_KEEP:-3}"
case "$KEEP" in ''|*[!0-9]*|0) KEEP=3 ;; esac
pruned=0
while IFS= read -r base; do
  while IFS= read -r old; do rm -f -- "$old" && pruned=$((pruned + 1)); done < <(
    find "$(dirname "$base")" -maxdepth 1 -type f -name "$(basename "$base").pre-merge-*" | sort -r | tail -n +"$((KEEP + 1))")
done < <(find "$APPDATA" -type f -name "*.pre-merge-*" 2>/dev/null | sed 's/\.pre-merge-[^/]*$//' | sort -u)
[ "$pruned" -gt 0 ] && echo "  · 옛 안전백업 ${pruned}개 지움(DB 마다 최근 ${KEEP}개 남김)"
[ "$rc" = 0 ] && [ "$SKIP_MERGE" = 0 ] && echo "$TAR_SHA" > "$MERGED_MARK"

[ "$rc" = 0 ] && echo "✓ app-data merge 완료 — dev 신규 반영 + cae00 데이터 보존" \
             || echo "⚠ 일부 merge 실패 — 위 로그 확인(운영 DB 는 안전백업으로 롤백 가능)"
exit "$rc"
