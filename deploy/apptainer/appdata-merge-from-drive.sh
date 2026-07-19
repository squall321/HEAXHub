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
  # 운영 직전 안전백업(롤백용, 로컬)
  cp "$live" "$live.pre-merge-$(date -u +%Y%m%d-%H%M%SZ)" 2>/dev/null || true
  echo "  → merge: $rel"
  if out="$(python3 "$MERGER" "$sdb" "$live" 2>&1)"; then
    echo "    ✓ $out"
  else
    echo "    ✗ merge 실패(운영 무손상 — 안전백업 존재): $out"; rc=1
  fi
done < <(find "$STAGE/src" -name "*.db" 2>/dev/null)

[ "$rc" = 0 ] && echo "✓ app-data merge 완료 — dev 신규 반영 + cae00 데이터 보존" \
             || echo "⚠ 일부 merge 실패 — 위 로그 확인(운영 DB 는 안전백업으로 롤백 가능)"
exit "$rc"
