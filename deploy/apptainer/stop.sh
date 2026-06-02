#!/usr/bin/env bash
# Stop HEAXHub local dev stack.
set -uo pipefail

echo "→ stop backend / worker / frontend"
pkill -f 'uvicorn app.main:app.*--port 4040' 2>/dev/null || true
pkill -f 'celery -A app.workers.celery_app' 2>/dev/null || true
pkill -f 'vite.*--port 4173' 2>/dev/null || true

for inst in heax-caddy heax-pg heax-redis heax-mailhog; do
  if apptainer instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$inst"; then
    echo "→ stop $inst"
    apptainer instance stop "$inst" 2>&1 | tail -1
  fi
done

echo "✓ stopped"
