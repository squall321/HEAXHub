#!/usr/bin/env bash
# Push HEAXHub fallback artifacts to Google Drive via rclone, so a server that reaches Drive but
# NOT Docker Hub/PyPI/GitHub can still pull them and run. Pushes: frontend dist + vendored runtimes
# (apptainer.deb, python.tar.gz from deploy/apptainer/cache/) + app-build base SIFs (base_*.sif)
# + optional service SIFs (HEAX_DRIVE_WITH_SIFS). latest/ accumulates (copy, not mirror).
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
# Read ONLY the keys we need from .env (don't `source` it — a value with an unquoted space would
# run as a command, e.g. `Admin: command not found`).
env_get() { [ -f .env ] && sed -n "s/^$1=//p" .env | tail -1 | sed 's/^["'"'"']//; s/["'"'"']$//'; }
HEAX_DRIVE_REMOTE="${HEAX_DRIVE_REMOTE:-$(env_get HEAX_DRIVE_REMOTE)}"
HEAX_DRIVE_RETAIN="${HEAX_DRIVE_RETAIN:-$(env_get HEAX_DRIVE_RETAIN)}"
SIF_DIR="${SIF_DIR:-$(env_get SIF_DIR)}"

command -v rclone >/dev/null 2>&1 || { echo "✗ rclone not installed (https://rclone.org/install/)"; exit 1; }
REMOTE="${HEAX_DRIVE_REMOTE:-}"
[ -n "$REMOTE" ] || { echo "✗ HEAX_DRIVE_REMOTE not set in .env (e.g. HeaxDrive:HEAXHub/dist)"; exit 1; }
REMOTE="${REMOTE%/}"
RETAIN="${HEAX_DRIVE_RETAIN:-3}"

TS="$(date -u +%Y%m%d-%H%M%SZ)"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
# frontend/dist 는 있으면 포함, 없으면 런타임/base 만 푸시(폴백 저장소 목적).
if [ -f frontend/dist/index.html ]; then
  ( cd frontend && tar -czf "$STAGE/frontend-dist.tar.gz" dist )
  echo "  · including frontend-dist.tar.gz"
else
  echo "! frontend/dist 없음 — dist 생략, 런타임/base 아티팩트만 푸시"
fi

# Ship the service SIFs too (cae00 can't `apptainer pull docker://...` or build them). They change
# rarely, so ship them ONCE with HEAX_DRIVE_WITH_SIFS=1 (or HEAX_DRIVE_WITH_CADDY=1 for caddy only).
SIFDIR="${SIF_DIR:-$HOME/serviceApptainers}"
if [ "${HEAX_DRIVE_WITH_SIFS:-0}" = "1" ]; then
  for s in heaxhub_postgres heaxhub_redis heaxhub_caddy heaxhub_mailhog; do
    [ -f "$SIFDIR/$s.sif" ] && { cp "$SIFDIR/$s.sif" "$STAGE/"; echo "  · including $s.sif"; }
  done
elif [ "${HEAX_DRIVE_WITH_CADDY:-0}" = "1" ] && [ -f "$SIFDIR/heaxhub_caddy.sif" ]; then
  cp "$SIFDIR/heaxhub_caddy.sif" "$STAGE/heaxhub_caddy.sif"; echo "  · including caddy SIF"
fi

# ── 벤더링 런타임 + base image SIF (Drive 폴백 저장소) ───────────────────────
# 1차(Docker Hub/PyPI/GitHub)가 막혀도 서버가 Drive 로 폴백해 받게 한다.
for v in deploy/apptainer/cache/apptainer_*.deb \
         deploy/apptainer/cache/python-*-x86_64-linux.tar.gz; do
  [ -f "$v" ] && { cp "$v" "$STAGE/"; echo "  · including $(basename "$v")"; }
done
if [ "${HEAX_DRIVE_WITH_BASE:-1}" = "1" ]; then
  for b in "$SIFDIR"/base_*.sif; do
    [ -f "$b" ] && { cp "$b" "$STAGE/"; echo "  · including $(basename "$b")"; }
  done
fi

( cd "$STAGE" && sha256sum ./* > SHA256SUMS )
echo "→ uploading to $REMOTE/dist-$TS/ (+ latest/)"
rclone copy --progress "$STAGE/" "$REMOTE/dist-$TS/"
# latest/ 는 sync(미러·삭제) 대신 copy(누적) — 부분 푸시가 기존 아티팩트를 지우지 않게.
rclone copy --progress "$STAGE/" "$REMOTE/latest/"

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
