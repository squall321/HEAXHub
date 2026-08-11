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
# 기본값이 voice_recorder 하드코딩이었고 오버라이드하는 호출자가 어디에도 없다.
# 그런데 실제 가중치를 가진 앱은 thermal_shock_mcp(16M) 하나이고 voice_recorder/models 는
# 비어 있다 — 스크립트는 '비어있음 — 생략' 만 찍고 끝나 정작 대상은 스캔조차 안 됐다.
# 그 문구는 '아직 안 받아둔 정상 상태'로 읽혀 경고로 보이지도 않는다.
# 목록을 하드코딩하지 말고 내용이 있는 models/ 를 가진 앱을 실측으로 잡는다 —
# 앱이 늘어도 목록 갱신을 잊을 수 없다. 명시 지정은 HEAX_MODEL_APPS 로 계속 가능하다.
_scan_model_apps() {
  local d
  for d in "$ROOT_DIR"/var/app_data/*/models; do
    [ -d "$d" ] || continue
    [ -n "$(ls -A "$d" 2>/dev/null)" ] || continue
    basename "$(dirname "$d")"
  done
}
APPS="${HEAX_MODEL_APPS:-$(_scan_model_apps | tr "\n" " ")}"
[ -n "${APPS// /}" ] || echo "  · models/ 에 내용이 있는 앱 없음 — 업로드 대상 없음"

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
