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
# 파이프+조기종료(grep -q)는 pipefail 아래서 SIGPIPE 오판을 만든다 — 목록을 먼저 받는다
# (오판하면 latest/ 를 건너뛰고 **조용히 낡은 dist** 로 떨어진다).
_listing="$(rclone lsf "$SRC/" 2>/dev/null || true)"
case $'\n'"$_listing"$'\n' in
  *$'\n'frontend-dist.tar.gz$'\n'*) _have_dist=1 ;;
  *) _have_dist=0 ;;
esac
if [ "$_have_dist" = "0" ]; then
  NEWEST="$(rclone lsf --dirs-only "$REMOTE/" 2>/dev/null | sed 's#/$##' | grep -E '^dist-' | sort | tail -n 1 || true)"
  [ -n "$NEWEST" ] || { echo "✗ no dist on $REMOTE. Push from an online host: ./deploy/apptainer/dist-to-drive.sh"; exit 1; }
  SRC="$REMOTE/$NEWEST"
fi
echo "→ source: $SRC"

# 같은 내용이면 손대지 않는다 — 살아 있는 apptainer 인스턴스 밑의 SIF 를 덮어쓰면 squashfs 가 깨지고, cp 는 mtime 을 리셋해 포털 update-all 의
# 재기동 판정(지문: 이름·크기·mtime)이 매번 달라진다. 영구 캐시(rclone 이 안 바뀐 파일을 건너뛴다)와 짝이다. HWAXPortal docs/update-all-skip-unchanged.
_install_if_changed() { if [ -f "$2" ] && cmp -s "$1" "$2"; then echo "  · $(basename "$2") 같음 — 그대로"; return 0; fi; cp -p "$1" "$2"; return 0; }
# 영구 캐시 — 임시 디렉터리면 rclone 이 비교할 것이 없어 매번 전량 전송이다(앱 SIF 여럿, Drive ~2MB/s). 캐시에 받으면 안 바뀐 파일은 전송 0.
STAGE="${HEAX_DRIVE_CACHE:-$ROOT_DIR/deploy/apptainer/cache/.drive-dist}"; mkdir -p "$STAGE"
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
  for s in "${sifs[@]}"; do _install_if_changed "$s" "$SIFDIR/$(basename "$s")"; echo "  ✓ staged $(basename "$s") → $SIFDIR"; done
fi
shopt -u nullglob

# 벤더링 런타임 → deploy/apptainer/cache/ (install-apptainer/install-python 가 .tools 로 추출)
mkdir -p "$ROOT_DIR/deploy/apptainer/cache"
shopt -s nullglob
for v in "$STAGE"/apptainer_*.deb "$STAGE"/python-*-x86_64-linux.tar.gz; do
  _install_if_changed "$v" "$ROOT_DIR/deploy/apptainer/cache/$(basename "$v")"; echo "  ✓ staged $(basename "$v") → deploy/apptainer/cache/"
done
# base image SIF → SIFDIR (builder 가 localimage 로 사용)
for b in "$STAGE"/base_*.sif; do
  mkdir -p "$SIFDIR"; _install_if_changed "$b" "$SIFDIR/$(basename "$b")"; echo "  ✓ staged $(basename "$b") → $SIFDIR"
done
shopt -u nullglob

# per-app SIFs → var/sifs/  (heaxhub_*/base_* 아닌 *.sif = 등록 앱 SIF).
# 폐쇄망 서버가 git·빌드 없이 이 SIF 로 앱을 바로 띄운다(.sif.hash 도 함께 = 스캔이
# 커밋 일치로 인식해 재빌드 스킵). start.sh/스캔이 var/sifs/<slug>.sif 를 그대로 사용.
mkdir -p "$ROOT_DIR/var/sifs"
shopt -s nullglob
for s in "$STAGE"/*.sif; do
  case "$(basename "$s")" in heaxhub_*|base_*) continue;; esac
  _install_if_changed "$s" "$ROOT_DIR/var/sifs/$(basename "$s")"; echo "  ✓ app SIF $(basename "$s") → var/sifs/"
  [ -f "$s.hash" ] && cp -p "$s.hash" "$ROOT_DIR/var/sifs/"
done
shopt -u nullglob

echo
echo "✓ dist ready — now run:  bash deploy/apptainer/start.sh   (Caddy serves it; no build)"
