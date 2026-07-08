#!/usr/bin/env bash
# 온라인 빌드 호스트에서 pip 패키지 + npm 툴체인/스토어를 Google Drive 에 올린다.
# Docker Hub/PyPI/npm 이 막힌 폐쇄망 서버가 Drive 로 폴백해 받게 하는 미러. dist-to-drive.sh 패턴.
#
# 나르는 것:
#   pip : build-requirements.txt 의 패키지  (pip download)          → pkgs-<TS>/pip/  + latest/pip/
#   npm : node+pnpm vendored tarball + pnpm fetch 오프라인 스토어    → pkgs-<TS>/npm/  + latest/npm/
#         → 서버가 프론트(Vite/React)를 pnpm 으로 "직접" 빌드할 수 있게 한다(폐쇄망).
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
REMOTE="${HEAX_DRIVE_REMOTE:-}"
[ -n "$REMOTE" ] || { echo "✗ HEAX_DRIVE_REMOTE not set in .env (e.g. HeaxDrive:HEAXHub/dist)"; exit 1; }
REMOTE="${REMOTE%/}"
RETAIN="${HEAX_DRIVE_RETAIN:-3}"

TS="$(date -u +%Y%m%d-%H%M%SZ)"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
PUSHED=0

# ── pip 미러 (build-requirements.txt) ─────────────────────────────────────────
REQ="${BUILD_REQUIREMENTS:-build-requirements.txt}"
if [ -f "$REQ" ] && grep -qvE '^\s*(#.*)?$' "$REQ"; then
  PIP="$(command -v pip3 || command -v pip || true)"
  [ -n "$PIP" ] || { echo "✗ pip not installed (pip 미러에 필요)"; exit 1; }
  mkdir -p "$STAGE/pip"
  echo "→ pip download -r $REQ → stage/pip/"
  "$PIP" download -r "$REQ" -d "$STAGE/pip"
  shopt -s nullglob; files=("$STAGE"/pip/*); shopt -u nullglob
  if [ ${#files[@]} -gt 0 ]; then
    for f in "${files[@]}"; do echo "  · $(basename "$f")"; done
    ( cd "$STAGE/pip" && sha256sum ./* > SHA256SUMS.pip )
    echo "→ uploading pip → $REMOTE/pkgs-$TS/pip/ (+ latest/pip/)"
    rclone copy --progress "$STAGE/pip/" "$REMOTE/pkgs-$TS/pip/"
    rclone copy --progress "$STAGE/pip/" "$REMOTE/latest/pip/"   # latest 는 누적(copy)
    PUSHED=1
  else
    echo "! pip download 결과 비어 있음 — pip 미러 생략"
  fi
else
  echo "! $REQ 없음/활성 패키지 없음 — pip 미러 생략(npm 은 계속)"
fi

# ── npm 미러 (node+pnpm 툴체인 + pnpm 오프라인 스토어) ─────────────────────────
# 폐쇄망 서버가 프론트를 스스로 빌드하도록 node/pnpm 바이너리 + 락파일 기반 스토어를 나른다.
if [ -f frontend/pnpm-lock.yaml ]; then
  echo "→ npm 미러 준비 (node+pnpm 툴체인 + pnpm fetch 스토어)"
  # (1) node+pnpm vendored tarball 보장 (install-node.sh 가 cache/ 에 생성)
  bash deploy/apptainer/install-node.sh >/dev/null || { echo "✗ install-node.sh 실패"; exit 1; }
  NODE_CACHE="$(ls -t deploy/apptainer/cache/node-*-linux-*.tar.gz 2>/dev/null | head -1)"
  [ -n "$NODE_CACHE" ] || { echo "✗ node cache tarball 없음 (install-node.sh 확인)"; exit 1; }
  VNODE_BIN="$ROOT_DIR/$(ls -d deploy/apptainer/.tools/node-*/bin 2>/dev/null | head -1)"
  mkdir -p "$STAGE/npm"
  cp "$NODE_CACHE" "$STAGE/npm/"
  echo "  · $(basename "$NODE_CACHE") ($(du -h "$NODE_CACHE" | cut -f1))"
  # (2) pnpm fetch → 락파일 전량을 오프라인 스토어로. CI=true 는 no-TTY 환경 필수.
  ( cd frontend && CI=true PATH="$VNODE_BIN:$PATH" pnpm fetch --store-dir "$STAGE/store" >/dev/null )
  tar czf "$STAGE/npm/pnpm-store.tar.gz" -C "$STAGE" store
  rm -rf "$STAGE/store"
  echo "  · pnpm-store.tar.gz ($(du -h "$STAGE/npm/pnpm-store.tar.gz" | cut -f1))"
  ( cd "$STAGE/npm" && sha256sum ./* > SHA256SUMS.npm )
  echo "→ uploading npm → $REMOTE/pkgs-$TS/npm/ (+ latest/npm/)"
  rclone copy --progress "$STAGE/npm/" "$REMOTE/pkgs-$TS/npm/"
  rclone copy --progress "$STAGE/npm/" "$REMOTE/latest/npm/"
  PUSHED=1
else
  echo "! frontend/pnpm-lock.yaml 없음 — npm 미러 생략"
fi

[ "$PUSHED" = 1 ] || { echo "✗ 올린 게 없음 (pip·npm 둘 다 비어 있음)"; exit 1; }

# ── 보존정책 (오래된 pkgs-<TS>/ purge) ────────────────────────────────────────
if [ "$RETAIN" -gt 0 ]; then
  echo "→ retention: keep last $RETAIN pkgs set(s)"
  rclone lsf --dirs-only "$REMOTE/" 2>/dev/null | sed 's#/$##' | grep -E '^pkgs-' \
    | sort | head -n -"$RETAIN" | while read -r old; do
        echo "  · deleting $old/"; rclone purge "$REMOTE/$old" 2>/dev/null || true
      done
fi

echo
echo "✓ pushed mirror to $REMOTE"
echo "  On server:  set HEAX_DRIVE_REMOTE in .env  →  ./deploy/apptainer/mirror-from-drive.sh"
