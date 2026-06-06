#!/usr/bin/env bash
# Start HEAX Hub served UNDER the HWAX portal sub-path (base = /heax-hub/).
# The portal reverse-proxies https://hwax.sec.samsung.net/heax-hub/ → this app, passing the
# prefix through, so assets/router/api must all sit under /heax-hub/ (handled by VITE_BASE_PATH).
#
#   ./start-behind-portal.sh            # vite dev on :4173, base /heax-hub/
#   PORT=4173 ./start-behind-portal.sh
#
# Standalone (no portal)?  Just run the normal dev/build (base defaults to "/").
# Production (Caddy serving the built dist)?  Build with the base, then serve dist under /heax-hub/:
#   VITE_BASE_PATH=/heax-hub/ pnpm --dir frontend build
set -euo pipefail
export VITE_BASE_PATH="${VITE_BASE_PATH:-/heax-hub/}"
cd "$(dirname "$0")/frontend"
echo "→ HEAX Hub dev with base ${VITE_BASE_PATH} on :${PORT:-4173}"
exec pnpm dev --host 0.0.0.0 --port "${PORT:-4173}"
