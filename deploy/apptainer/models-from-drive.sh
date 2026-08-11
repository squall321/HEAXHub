#!/usr/bin/env bash
# 대용량 앱 모델 가중치(voice_recorder TTS 등)를 Drive <remote>:HEAXHub/models/<app> 에서
# 각 앱 런타임 모델 디렉토리 var/app_data/<app>/models(컨테이너 /data/models)로 증분 동기한다.
# app-data tar 엔 크기 때문에 안 담기는(별도 models-to-drive.sh 로 올린) 가중치용.
# 비치명적 — remote/rclone/모델 없으면 조용히 생략(첫 배포·미업로드면 정상).
#
#   bash deploy/apptainer/models-from-drive.sh
#   HEAX_MODEL_APPS="voice_recorder foo" bash deploy/apptainer/models-from-drive.sh
set -uo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$ROOT_DIR"
env_get() { [ -f .env ] && sed -n "s/^$1=//p" .env | tail -1 | sed 's/^["'"'"']//; s/["'"'"']$//'; }

REMOTE="${HEAX_DRIVE_REMOTE:-$(env_get HEAX_DRIVE_REMOTE)}"
[ -n "$REMOTE" ] || { echo "· HEAX_DRIVE_REMOTE 미설정 — models 동기 생략"; exit 0; }
REMOTE="${REMOTE%/}"; REMOTE="${REMOTE%/dist}"      # dist 형제로 models/ 사용
RCLONE="$(command -v rclone || true)"; [ -n "$RCLONE" ] || { echo "· rclone 없음 — models 동기 생략"; exit 0; }

APPDATA="$ROOT_DIR/var/app_data"
# 모델을 별도 Drive 경로로 나르는 앱 목록(런타임 모델 dir 이 app-data tar 밖).
APPS="${HEAX_MODEL_APPS:-voice_recorder}"

for app in $APPS; do
  src="$REMOTE/models/$app"
  dst="$APPDATA/$app/models"
  if ! "$RCLONE" lsf "$src/" >/dev/null 2>&1; then
    echo "· $app: Drive 에 models 없음($src) — 생략(dev 에서 models-to-drive 먼저)"
    continue
  fi
  mkdir -p "$dst"
  echo "→ models: $src → $dst"
  if "$RCLONE" copy "$src" "$dst" --transfers 4 --checkers 8 2>/dev/null; then
    echo "  ✓ $app models ($(du -sh "$dst" 2>/dev/null | cut -f1))"
  else
    echo "  · $app models 동기 실패(비치명)"
  fi
done
