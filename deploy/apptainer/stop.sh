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

# 호스팅 앱 인스턴스(heax_app_*)도 정지 — 배포가 SIF 를 교체할 때 인스턴스가 실행 중이면
# squashfs 마운트가 깨져(exec 시 libc.so.6 로드 실패) reconcile 이 재기동을 건너뛰어 앱이
# 404 가 됐다(materialtwin_web·voice_recorder). 여기서 내려두면 백엔드 부팅·celery reconcile
# 이 새 SIF 로 깨끗이 콜드스타트한다. (mxwp_web/api 를 배포가 stop 하는 것과 같은 처방.)
for inst in $("$APPTAINER" instance list 2>/dev/null | awk 'NR>1 && $1 ~ /^heax_app_/{print $1}'); do
  echo "→ stop $inst (앱 인스턴스 — 새 SIF 로 재기동 위해)"
  "$APPTAINER" instance stop "$inst" 2>&1 | tail -1
done

for inst in heax-caddy heax-pg heax-redis heax-mailhog; do
  if "$APPTAINER" instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$inst"; then
    echo "→ stop $inst"
    "$APPTAINER" instance stop "$inst" 2>&1 | tail -1
  fi
done

echo "✓ stopped"
