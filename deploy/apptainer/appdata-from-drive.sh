#!/usr/bin/env bash
# Drive의 heax 앱 런타임 데이터(app-data/latest/app-data.tar.gz)를 var/app_data 로 복원한다.
# appdata-to-drive.sh 의 짝. cae00 에서 heax 기동 전에 불러 재료 DB 등을 채운다. 직전 상태는 안전백업.
# 첫 배포/Drive에 없음/rclone 없음 은 조용히 생략(비치명적) — 배포를 막지 않는다.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
env_get() { [ -f .env ] && sed -n "s/^$1=//p" .env | tail -1 | sed 's/^["'"'"']//; s/["'"'"']$//'; }

REMOTE="${HEAX_DRIVE_REMOTE:-$(env_get HEAX_DRIVE_REMOTE)}"
[ -n "$REMOTE" ] || { echo "· HEAX_DRIVE_REMOTE 미설정 — app-data 복원 생략"; exit 0; }
REMOTE="${REMOTE%/}"; REMOTE="${REMOTE%/dist}"
DEST="$REMOTE/app-data"
RCLONE="$(command -v rclone || true)"
[ -n "$RCLONE" ] || { echo "· rclone 없음 — app-data 복원 생략"; exit 0; }

STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
"$RCLONE" copy "$DEST/latest/app-data.tar.gz" "$STAGE/" 2>/dev/null || true
[ -f "$STAGE/app-data.tar.gz" ] || { echo "· Drive에 app-data 없음 — 복원 생략(첫 배포면 정상)"; exit 0; }

APPDATA="$ROOT_DIR/var/app_data"; mkdir -p "$APPDATA"
if [ -n "$(ls -A "$APPDATA" 2>/dev/null)" ]; then      # 직전 상태 안전백업(AIDH restore 패턴)
  BK="$ROOT_DIR/var/app_data.bak-$(date -u +%Y%m%d-%H%M%SZ)"
  cp -a "$APPDATA" "$BK"; echo "· 직전 app_data → $BK"
fi
tar -xzf "$STAGE/app-data.tar.gz" -C "$APPDATA"
echo "✓ app-data 복원됨 → var/app_data (materialtwin 재료 DB 등)"
