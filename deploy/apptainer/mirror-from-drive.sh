#!/usr/bin/env bash
# Google Drive 의 pip 미러(latest/pip/ 또는 최신 pkgs-<TS>/pip/)를 서버로 받아 var/pkg-mirror/pip/ 에 둔다.
# Docker Hub/PyPI 막힌 망에서 pip 폴백 미러(--find-links / 호스팅용)로 쓴다. dist-from-drive.sh 패턴 복제.
#
# Needs in .env:  HEAX_DRIVE_REMOTE=HeaxDrive:HEAXHub/dist
# After this:  pip install --no-index --find-links var/pkg-mirror/pip <pkg>   (오프라인 설치)
#              또는 BUILD_PIP_FIND_LINKS=http://<host>:4180/pkgs/pip/ 로 미러 호스팅.
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

# latest/pip/ 에 SHA256SUMS.pip 가 있으면 그걸, 없으면 최신 pkgs-<TS>/pip/ 를 source 로.
SRC="$REMOTE/latest/pip"
if ! rclone lsf "$SRC/" 2>/dev/null | grep -q '^SHA256SUMS\.pip$'; then
  NEWEST="$(rclone lsf --dirs-only "$REMOTE/" 2>/dev/null | sed 's#/$##' | grep -E '^pkgs-' | sort | tail -n 1 || true)"
  [ -n "$NEWEST" ] || { echo "✗ no pip mirror on $REMOTE. Push from an online host: ./deploy/apptainer/mirror-to-drive.sh"; exit 1; }
  SRC="$REMOTE/$NEWEST/pip"
fi
echo "→ source: $SRC"

DEST="$ROOT_DIR/var/pkg-mirror/pip"
mkdir -p "$DEST"
rclone copy --progress "$SRC/" "$DEST/"
[ -f "$DEST/SHA256SUMS.pip" ] && { ( cd "$DEST" && sha256sum -c SHA256SUMS.pip ) || { echo "✗ checksum failed"; exit 1; }; echo "  ✓ checksums OK"; }

echo
echo "✓ pip mirror ready → $DEST"
echo "  오프라인 설치:  pip install --no-index --find-links '$DEST' <pkg>"
