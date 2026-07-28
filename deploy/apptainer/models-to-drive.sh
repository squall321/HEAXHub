#!/usr/bin/env bash
# 대용량 앱 모델 가중치(voice_recorder TTS 등)를 온라인 박스에서 Drive
# <remote>:HEAXHub/models/<app> 로 올린다. models-from-drive.sh 의 업로드 짝.
#
# 왜 app-data tar(appdata-to-drive) 와 분리하나:
#   TTS 가중치는 수백 MB~GB 라 app-data tar 에 담으면 매 배포마다 통째로 오간다.
#   모델은 여기(models/)로 rclone 증분 동기 — 바뀐 것만 오른다.
#
# 소스: var/app_data/<app>/models/  (앱이 온라인에서 1회 받아둔 가중치)
#   대상: <remote>:HEAXHub/models/<app>/
#
# 사용:
#   bash deploy/apptainer/models-to-drive.sh
#   HEAX_MODEL_APPS="voice_recorder foo" bash deploy/apptainer/models-to-drive.sh
#
# Needs in .env:  HEAX_DRIVE_REMOTE=HeaxDrive:HEAXHub/dist  (dist 형제로 models/ 사용)
set -uo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$ROOT_DIR"
env_get() { [ -f .env ] && sed -n "s/^$1=//p" .env | tail -1 | sed 's/^["'"'"']//; s/["'"'"']$//'; }

REMOTE="${HEAX_DRIVE_REMOTE:-$(env_get HEAX_DRIVE_REMOTE)}"
[ -n "$REMOTE" ] || { echo "· HEAX_DRIVE_REMOTE 미설정 — models 업로드 생략"; exit 0; }
REMOTE="${REMOTE%/}"; REMOTE="${REMOTE%/dist}"      # dist 형제로 models/ 사용
RCLONE="$(command -v rclone || true)"; [ -n "$RCLONE" ] || { echo "· rclone 없음 — models 업로드 생략"; exit 0; }

APPDATA="$ROOT_DIR/var/app_data"
APPS="${HEAX_MODEL_APPS:-voice_recorder}"

pushed=0
for app in $APPS; do
  src="$APPDATA/$app/models"
  dst="$REMOTE/models/$app"
  if [ ! -d "$src" ] || [ -z "$(ls -A "$src" 2>/dev/null)" ]; then
    echo "· $app: $src 비어있음 — 업로드 생략(먼저 온라인에서 가중치를 받아 이 경로에 두세요)"
    continue
  fi
  echo "→ models: $src → $dst ($(du -sh "$src" 2>/dev/null | cut -f1))"
  if "$RCLONE" copy "$src" "$dst" --transfers 4 --checkers 8 --progress 2>&1 | tail -1; then
    echo "  ✓ $app models 업로드"
    pushed=1
  else
    echo "  · $app models 업로드 실패(비치명)"
  fi
done

[ "$pushed" = 1 ] && echo "✓ 서버에서:  bash deploy/apptainer/models-from-drive.sh" \
  || echo "· 올린 모델 없음 — var/app_data/<app>/models 에 가중치를 먼저 받아두세요"
