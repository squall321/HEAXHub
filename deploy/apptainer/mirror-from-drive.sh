#!/usr/bin/env bash
# Google Drive 의 pip + npm 미러를 서버로 받는다(폐쇄망 폴백). dist-from-drive.sh 패턴 복제.
#   pip → var/pkg-mirror/pip/   (pip install --no-index --find-links / 호스팅)
#   npm → node+pnpm 을 .tools/ 에 설치 + pnpm 오프라인 스토어를 var/pkg-mirror/npm/store 에 추출
#         → 서버가 프론트를 pnpm 으로 오프라인 빌드(build-frontend.sh)할 수 있게 한다.
#
# Needs in .env:  HEAX_DRIVE_REMOTE=HeaxDrive:HEAXHub/dist
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
# Read ONLY the keys we need from .env (don't `source` it — a value with an unquoted space would
# run as a command, e.g. `Admin: command not found`).
env_get() { [ -f .env ] && sed -n "s/^$1=//p" .env | tail -1 | sed 's/^["'"'"']//; s/["'"'"']$//'; }
HEAX_DRIVE_REMOTE="${HEAX_DRIVE_REMOTE:-$(env_get HEAX_DRIVE_REMOTE)}"

command -v rclone >/dev/null 2>&1 || { echo "✗ rclone not installed (https://rclone.org/install/)"; exit 1; }
REMOTE="${HEAX_DRIVE_REMOTE:-}"
[ -n "$REMOTE" ] || { echo "✗ HEAX_DRIVE_REMOTE not set in .env (e.g. HeaxDrive:HEAXHub/dist)"; exit 1; }
REMOTE="${REMOTE%/}"

# latest/<kind>/ 에 SHA256SUMS.<kind> 가 있으면 그걸, 없으면 최신 pkgs-<TS>/<kind>/ 를 source 로.
resolve_src() {  # $1=kind(pip|npm) → stdout: source 경로 (없으면 빈 문자열). 항상 0 리턴
  # (set -e 아래서 SRC="$(resolve_src ..)" 가 non-zero 로 스크립트를 죽이지 않게).
  local kind="$1"
  local src="$REMOTE/latest/$kind"
  if rclone lsf "$src/" 2>/dev/null | grep -q "^SHA256SUMS\.$kind\$"; then echo "$src"; return 0; fi
  local newest
  newest="$(rclone lsf --dirs-only "$REMOTE/" 2>/dev/null | sed 's#/$##' | grep -E '^pkgs-' | sort | tail -n 1 || true)"
  if [ -n "$newest" ] && rclone lsf "$REMOTE/$newest/$kind/" 2>/dev/null | grep -q "^SHA256SUMS\.$kind\$"; then
    echo "$REMOTE/$newest/$kind"
  fi
  return 0
}

GOT=0

# ── pip 미러 수령 ─────────────────────────────────────────────────────────────
PIP_SRC="$(resolve_src pip)"
if [ -n "$PIP_SRC" ]; then
  echo "→ pip source: $PIP_SRC"
  DEST="$ROOT_DIR/var/pkg-mirror/pip"; mkdir -p "$DEST"
  rclone copy --progress "$PIP_SRC/" "$DEST/"
  [ -f "$DEST/SHA256SUMS.pip" ] && { ( cd "$DEST" && sha256sum -c SHA256SUMS.pip ) || { echo "✗ pip checksum failed"; exit 1; }; echo "  ✓ pip checksums OK"; }
  echo "  ✓ pip 미러 → $DEST"
  GOT=1
else
  echo "! Drive 에 pip 미러 없음 — 생략"
fi

# ── npm 미러 수령 (node+pnpm 설치 + 오프라인 스토어 추출) ──────────────────────
NPM_SRC="$(resolve_src npm)"
if [ -n "$NPM_SRC" ]; then
  echo "→ npm source: $NPM_SRC"
  NDEST="$ROOT_DIR/var/pkg-mirror/npm"; mkdir -p "$NDEST"
  rclone copy --progress "$NPM_SRC/" "$NDEST/"
  [ -f "$NDEST/SHA256SUMS.npm" ] && { ( cd "$NDEST" && sha256sum -c SHA256SUMS.npm ) || { echo "✗ npm checksum failed"; exit 1; }; echo "  ✓ npm checksums OK"; }
  # node+pnpm → .tools/ : cache 로 복사 후 install-node.sh 가 추출(오프라인).
  NODE_TB="$(ls "$NDEST"/node-*-linux-*.tar.gz 2>/dev/null | head -1 || true)"
  if [ -n "$NODE_TB" ]; then
    mkdir -p deploy/apptainer/cache; cp "$NODE_TB" deploy/apptainer/cache/
    bash deploy/apptainer/install-node.sh >/dev/null && echo "  ✓ node+pnpm → .tools/ 설치"
  fi
  # pnpm 오프라인 스토어 추출 → var/pkg-mirror/npm/store
  if [ -f "$NDEST/pnpm-store.tar.gz" ]; then
    rm -rf "$NDEST/store"
    env -u TAR_OPTIONS tar -xzf "$NDEST/pnpm-store.tar.gz" -C "$NDEST"
    echo "  ✓ pnpm 스토어 → $NDEST/store"
  fi
  echo "  ✓ npm 미러 ready → $NDEST"
  GOT=1
else
  echo "! Drive 에 npm 미러 없음 — 생략"
fi

[ "$GOT" = 1 ] || { echo "✗ Drive 에 pip·npm 미러가 하나도 없음. 온라인에서: ./deploy/apptainer/mirror-to-drive.sh"; exit 1; }

echo
echo "✓ 미러 수령 완료"
[ -d "$ROOT_DIR/var/pkg-mirror/npm/store" ] && \
  echo "  프론트 오프라인 빌드:  bash deploy/apptainer/build-frontend.sh"
