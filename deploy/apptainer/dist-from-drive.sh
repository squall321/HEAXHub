#!/usr/bin/env bash
# Pull the built frontend dist (+ optional caddy SIF) from Google Drive, so cae00 serves the SPA
# with NO build. Caddy binds frontend/dist → /srv/web (start.sh), so we just drop dist in place.
#
# Needs in .env:  HEAX_DRIVE_REMOTE=HeaxDrive:HEAXHub/dist
# After this:  bash deploy/apptainer/start.sh   (frontend/dist present → install_all skips the build)
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
# Read ONLY the keys we need from .env (don't `source` it — a value with an unquoted space would
# run as a command, e.g. `Admin: command not found`).
env_get() { [ -f .env ] && sed -n "s/^$1=//p" .env | tail -1 | sed 's/^["'"'"']//; s/["'"'"']$//'; }
HEAX_DRIVE_REMOTE="${HEAX_DRIVE_REMOTE:-$(env_get HEAX_DRIVE_REMOTE)}"
SIF_DIR="${SIF_DIR:-$(env_get SIF_DIR)}"

command -v rclone >/dev/null 2>&1 || { echo "✗ rclone not installed (https://rclone.org/install/)"; exit 1; }
REMOTE="${HEAX_DRIVE_REMOTE:-}"
[ -n "$REMOTE" ] || { echo "✗ HEAX_DRIVE_REMOTE not set in .env (e.g. HeaxDrive:HEAXHub/dist)"; exit 1; }
REMOTE="${REMOTE%/}"

SRC="$REMOTE/latest"
if ! rclone lsf "$SRC/" 2>/dev/null | grep -q '^frontend-dist\.tar\.gz$'; then
  NEWEST="$(rclone lsf --dirs-only "$REMOTE/" 2>/dev/null | sed 's#/$##' | grep -E '^dist-' | sort | tail -n 1 || true)"
  [ -n "$NEWEST" ] || { echo "✗ no dist on $REMOTE. Push from an online host: ./deploy/apptainer/dist-to-drive.sh"; exit 1; }
  SRC="$REMOTE/$NEWEST"
fi
echo "→ source: $SRC"

STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
rclone copy --progress "$SRC/" "$STAGE/"
[ -f "$STAGE/SHA256SUMS" ] && { ( cd "$STAGE" && sha256sum -c SHA256SUMS ) || { echo "✗ checksum failed"; exit 1; }; echo "  ✓ checksums OK"; }

# Restore frontend/dist
( cd "$ROOT_DIR/frontend" && tar -xzf "$STAGE/frontend-dist.tar.gz" )
echo "  ✓ extracted frontend/dist"

# Optional caddy SIF
if [ -f "$STAGE/heaxhub_caddy.sif" ]; then
  mkdir -p "${SIF_DIR:-$HOME/serviceApptainers}"
  cp "$STAGE/heaxhub_caddy.sif" "${SIF_DIR:-$HOME/serviceApptainers}/heaxhub_caddy.sif"
  echo "  ✓ staged caddy SIF → ${SIF_DIR:-$HOME/serviceApptainers}"
fi

echo
echo "✓ dist ready — now run:  bash deploy/apptainer/start.sh   (Caddy serves it; no build)"
