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
[ -n "${APPS// /}" ] || echo "  · models/ 에 내용이 있는 앱 없음 — 반입 대상 없음"

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
