#!/usr/bin/env bash
# build-requirements.txt 의 pip 패키지를 온라인 빌드 호스트에서 download 해 Google Drive 에 올린다.
# Docker Hub/PyPI 가 막힌 서버가 Drive 로 폴백해 받게 하는 pip 미러. dist-to-drive.sh 패턴 복제.
#
# 흐름:  stage 생성 → pip download -r build-requirements.txt -d stage/pip/ →
#         SHA256SUMS.pip → rclone copy → pkgs-<TS>/pip/ (+ latest/pip/ 누적) → 보존정책 purge
#
# Run on an ONLINE build host:
#   ./deploy/apptainer/mirror-to-drive.sh
#
# Needs in .env:  HEAX_DRIVE_REMOTE=HeaxDrive:HEAXHub/dist   (rclone remote+path, dist-*.sh 와 공유)
# rclone must be configured once (`rclone config` → drive). Reuses any existing remote alias.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
# Read ONLY the keys we need from .env (don't `source` it — a value with an unquoted space would
# run as a command, e.g. `Admin: command not found`).
env_get() { [ -f .env ] && sed -n "s/^$1=//p" .env | tail -1 | sed 's/^["'"'"']//; s/["'"'"']$//'; }
HEAX_DRIVE_REMOTE="${HEAX_DRIVE_REMOTE:-$(env_get HEAX_DRIVE_REMOTE)}"
HEAX_DRIVE_RETAIN="${HEAX_DRIVE_RETAIN:-$(env_get HEAX_DRIVE_RETAIN)}"

command -v rclone >/dev/null 2>&1 || { echo "✗ rclone not installed (https://rclone.org/install/)"; exit 1; }
command -v pip >/dev/null 2>&1 || command -v pip3 >/dev/null 2>&1 || { echo "✗ pip not installed"; exit 1; }
PIP="$(command -v pip3 || command -v pip)"
REMOTE="${HEAX_DRIVE_REMOTE:-}"
[ -n "$REMOTE" ] || { echo "✗ HEAX_DRIVE_REMOTE not set in .env (e.g. HeaxDrive:HEAXHub/dist)"; exit 1; }
REMOTE="${REMOTE%/}"
RETAIN="${HEAX_DRIVE_RETAIN:-3}"

REQ="${BUILD_REQUIREMENTS:-build-requirements.txt}"
[ -f "$REQ" ] || { echo "✗ $REQ 없음 — repo 루트에 build-requirements.txt 가 있어야 함"; exit 1; }
# 주석/빈 행을 제외한 실제 패키지가 하나라도 있는지 확인 (없으면 받을 게 없음).
if ! grep -qvE '^\s*(#.*)?$' "$REQ"; then
  echo "! $REQ 에 활성 패키지가 없음 (전부 주석/빈 행) — 미러링 생략"
  exit 0
fi

TS="$(date -u +%Y%m%d-%H%M%SZ)"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/pip"

echo "→ pip download -r $REQ → stage/pip/"
"$PIP" download -r "$REQ" -d "$STAGE/pip"

shopt -s nullglob
files=("$STAGE"/pip/*)
[ ${#files[@]} -gt 0 ] || { echo "✗ 받은 패키지가 없음 (pip download 결과 비어 있음)"; exit 1; }
for f in "${files[@]}"; do echo "  · $(basename "$f")"; done
shopt -u nullglob

( cd "$STAGE/pip" && sha256sum ./* > SHA256SUMS.pip )

echo "→ uploading to $REMOTE/pkgs-$TS/pip/ (+ latest/pip/)"
rclone copy --progress "$STAGE/pip/" "$REMOTE/pkgs-$TS/pip/"
# latest/ 는 sync(미러·삭제) 대신 copy(누적) — 부분 푸시가 기존 미러를 지우지 않게.
rclone copy --progress "$STAGE/pip/" "$REMOTE/latest/pip/"

if [ "$RETAIN" -gt 0 ]; then
  echo "→ retention: keep last $RETAIN pkgs set(s)"
  rclone lsf --dirs-only "$REMOTE/" 2>/dev/null | sed 's#/$##' | grep -E '^pkgs-' \
    | sort | head -n -"$RETAIN" | while read -r old; do
        echo "  · deleting $old/"; rclone purge "$REMOTE/$old" 2>/dev/null || true
      done
fi

echo
echo "✓ pushed pip mirror to $REMOTE"
echo "  On server:  set HEAX_DRIVE_REMOTE in .env  →  ./deploy/apptainer/mirror-from-drive.sh"
