#!/usr/bin/env bash
# Stop HEAXHub local dev stack.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Use the same (extracted, no-D-Bus) apptainer as start.sh — see its header.
APPTAINER="${HEAX_APPTAINER:-${HEAXHUB_APPT_BIN:-}}"
if [ -z "$APPTAINER" ]; then
  for c in "$ROOT"/deploy/apptainer/.tools/apptainer-*/usr/bin/apptainer \
           "$ROOT"/infra/apptainer/bin-*/usr/bin/apptainer \
           "$ROOT"/../HWAXPortal/infra/apptainer/bin-*/usr/bin/apptainer \
           "$HOME"/Projects/HWAXPortal/infra/apptainer/bin-*/usr/bin/apptainer \
           "$HOME"/claude/HWAXPortal/infra/apptainer/bin-*/usr/bin/apptainer; do
    [ -x "$c" ] && { APPTAINER="$c"; break; }
  done
fi
: "${APPTAINER:=apptainer}"

echo "→ stop backend / worker / frontend"
pkill -f 'uvicorn app.main:app.*--port 4040' 2>/dev/null || true
pkill -f 'celery -A app.workers.celery_app' 2>/dev/null || true
pkill -f 'vite.*--port 4173' 2>/dev/null || true

for inst in heax-caddy heax-pg heax-redis heax-mailhog; do
  if "$APPTAINER" instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$inst"; then
    echo "→ stop $inst"
    "$APPTAINER" instance stop "$inst" 2>&1 | tail -1
  fi
done

echo "✓ stopped"
