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

STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
"$RCLONE" copy "$DEST/latest/app-data.tar.gz" "$STAGE/" 2>/dev/null || true
[ -f "$STAGE/app-data.tar.gz" ] || { echo "· Drive 에 app-data 없음 — merge 생략(첫 배포면 정상)"; exit 0; }
mkdir -p "$STAGE/src"; tar -xzf "$STAGE/app-data.tar.gz" -C "$STAGE/src"

APPDATA="$ROOT_DIR/var/app_data"; mkdir -p "$APPDATA"
rc=0
# dev 스냅샷의 각 *.db 를 운영의 같은 상대경로 DB 에 merge
while IFS= read -r sdb; do
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
done < <(find "$STAGE/src" -type f ! -name "*.db" ! -name "*-wal" ! -name "*-shm" ! -name "*-journal" \
           ! -name "*.lock" ! -name "*.pre-merge-*" 2>/dev/null)

[ "$rc" = 0 ] && echo "✓ app-data merge 완료 — dev 신규 반영 + cae00 데이터 보존" \
             || echo "⚠ 일부 merge 실패 — 위 로그 확인(운영 DB 는 안전백업으로 롤백 가능)"
exit "$rc"
