#!/usr/bin/env bash
# Pull HEAXHub fallback artifacts from Google Drive (rclone) and place them: frontend dist →
# frontend/dist, vendored runtimes (apptainer.deb/python.tar.gz) → deploy/apptainer/cache/,
# base SIFs (base_*.sif) + service SIFs → ~/serviceApptainers. Lets a Drive-reachable but
# Docker-Hub/PyPI/GitHub-blocked server set up + build without those upstreams.
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

# Restore frontend/dist (있을 때만 — 런타임/base 만 올라온 푸시도 지원)
if [ -f "$STAGE/frontend-dist.tar.gz" ]; then
  ( cd "$ROOT_DIR/frontend" && tar -xzf "$STAGE/frontend-dist.tar.gz" )
  echo "  ✓ extracted frontend/dist"
else
  echo "  · frontend-dist 없음 — 런타임/base 만 반입"
fi

# Service SIFs (postgres/redis/caddy/mailhog) — cae00 can't pull/build them, so stage whatever was
# shipped into ~/serviceApptainers (create the dir; start.sh expects it there).
SIFDIR="${SIF_DIR:-$HOME/serviceApptainers}"
shopt -s nullglob
sifs=("$STAGE"/heaxhub_*.sif)
if [ ${#sifs[@]} -gt 0 ]; then
  mkdir -p "$SIFDIR"
  for s in "${sifs[@]}"; do cp "$s" "$SIFDIR/"; echo "  ✓ staged $(basename "$s") → $SIFDIR"; done
fi
shopt -u nullglob

# 벤더링 런타임 → deploy/apptainer/cache/ (install-apptainer/install-python 가 .tools 로 추출)
mkdir -p "$ROOT_DIR/deploy/apptainer/cache"
shopt -s nullglob
for v in "$STAGE"/apptainer_*.deb "$STAGE"/python-*-x86_64-linux.tar.gz; do
  cp "$v" "$ROOT_DIR/deploy/apptainer/cache/"; echo "  ✓ staged $(basename "$v") → deploy/apptainer/cache/"
done
# base image SIF → SIFDIR (builder 가 localimage 로 사용)
for b in "$STAGE"/base_*.sif; do
  mkdir -p "$SIFDIR"; cp "$b" "$SIFDIR/"; echo "  ✓ staged $(basename "$b") → $SIFDIR"
done
shopt -u nullglob

# per-app SIFs → var/sifs/  (heaxhub_*/base_* 아닌 *.sif = 등록 앱 SIF).
# 폐쇄망 서버가 git·빌드 없이 이 SIF 로 앱을 바로 띄운다(.sif.hash 도 함께 = 스캔이
# 커밋 일치로 인식해 재빌드 스킵). start.sh/스캔이 var/sifs/<slug>.sif 를 그대로 사용.
mkdir -p "$ROOT_DIR/var/sifs"
shopt -s nullglob
for s in "$STAGE"/*.sif; do
  case "$(basename "$s")" in heaxhub_*|base_*) continue;; esac
  cp "$s" "$ROOT_DIR/var/sifs/"; echo "  ✓ app SIF $(basename "$s") → var/sifs/"
  [ -f "$s.hash" ] && cp "$s.hash" "$ROOT_DIR/var/sifs/"
done
shopt -u nullglob

echo
echo "✓ dist ready — now run:  bash deploy/apptainer/start.sh   (Caddy serves it; no build)"
