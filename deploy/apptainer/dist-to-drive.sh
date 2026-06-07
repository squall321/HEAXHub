#!/usr/bin/env bash
# Push the BUILT frontend dist (+ optionally the caddy SIF) to Google Drive via rclone, so cae00
# (corporate TLS-intercept network: npm + Docker Hub unreachable) pulls them instead of building.
#
# Run on an ONLINE build host, AFTER building the SPA for the portal sub-path:
#   HEAX_BASE_PATH=/heax-hub/ pnpm --dir frontend build      # base baked into frontend/dist
#   ./deploy/apptainer/dist-to-drive.sh
#
# Needs in .env:  HEAX_DRIVE_REMOTE=HeaxDrive:HEAXHub/dist   (rclone remote+path)
# rclone must be configured once (`rclone config` → drive). Reuses any existing remote alias.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
[ -f .env ] && { set -a; . ./.env; set +a; }

command -v rclone >/dev/null 2>&1 || { echo "✗ rclone not installed (https://rclone.org/install/)"; exit 1; }
REMOTE="${HEAX_DRIVE_REMOTE:-}"
[ -n "$REMOTE" ] || { echo "✗ HEAX_DRIVE_REMOTE not set in .env (e.g. HeaxDrive:HEAXHub/dist)"; exit 1; }
REMOTE="${REMOTE%/}"
RETAIN="${HEAX_DRIVE_RETAIN:-3}"

[ -f frontend/dist/index.html ] \
  || { echo "✗ frontend/dist missing — build first: HEAX_BASE_PATH=/heax-hub/ pnpm --dir frontend build"; exit 1; }

TS="$(date -u +%Y%m%d-%H%M%SZ)"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
( cd frontend && tar -czf "$STAGE/frontend-dist.tar.gz" dist )

# Optionally also ship the caddy SIF (cae00 can't `apptainer pull docker://caddy`). Ship once with
# HEAX_DRIVE_WITH_CADDY=1; it rarely changes.
CADDY_SIF="${SIF_DIR:-$HOME/serviceApptainers}/heaxhub_caddy.sif"
if [ "${HEAX_DRIVE_WITH_CADDY:-0}" = "1" ] && [ -f "$CADDY_SIF" ]; then
  cp "$CADDY_SIF" "$STAGE/heaxhub_caddy.sif"; echo "  · including caddy SIF"
fi

( cd "$STAGE" && sha256sum ./* > SHA256SUMS )
echo "→ uploading to $REMOTE/dist-$TS/ (+ latest/)"
rclone copy --progress "$STAGE/" "$REMOTE/dist-$TS/"
rclone sync --progress "$STAGE/" "$REMOTE/latest/"

if [ "$RETAIN" -gt 0 ]; then
  echo "→ retention: keep last $RETAIN set(s)"
  rclone lsf --dirs-only "$REMOTE/" 2>/dev/null | sed 's#/$##' | grep -E '^dist-' \
    | sort | head -n -"$RETAIN" | while read -r old; do
        echo "  · deleting $old/"; rclone purge "$REMOTE/$old" 2>/dev/null || true
      done
fi

echo
echo "✓ pushed to $REMOTE"
echo "  On cae00:  set HEAX_DRIVE_REMOTE in .env  →  ./deploy/apptainer/dist-from-drive.sh  →  start.sh"
