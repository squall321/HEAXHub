#!/usr/bin/env bash
# Push HEAXHub fallback artifacts to Google Drive via rclone, so a server that reaches Drive but
# NOT Docker Hub/PyPI/GitHub can still pull them and run. Pushes: frontend dist + vendored runtimes
# (apptainer.deb, python.tar.gz from deploy/apptainer/cache/) + app-build base SIFs (base_*.sif)
# + optional service SIFs (HEAX_DRIVE_WITH_SIFS) + per-app SIFs (var/sifs/<slug>.sif, 데모 제외 —
# HEAX_DRIVE_WITH_APP_SIFS=1 기본). latest/ accumulates (copy, not mirror).
# → 폐쇄망 서버는 dist-from-drive 로 앱 SIF 까지 받아 git·빌드 없이 앱을 바로 띄운다.
#
# Run on an ONLINE build host, AFTER building the SPA for the portal sub-path:
#   VITE_BASE_PATH=/heax-hub/ pnpm --dir frontend build      # base baked into frontend/dist
#   ./deploy/apptainer/dist-to-drive.sh
# (vite.config 는 VITE_BASE_PATH 를 읽는다 — 과거 HEAX_BASE_PATH 표기는 무효라 루트 base
#  dist 가 업로드돼 포털 서브패스에서 자산 404를 냈다. 아래 가드가 재발을 차단한다.)
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

# ── base 가드: 포털 서브패스(/heax-hub/)로 빌드되지 않은 dist 는 업로드를 거부한다 ──
# (루트 base dist 가 배포되면 포털에서 자산 404 전면 장애. 의도적 루트 배포는
#  HEAX_DIST_ALLOW_ROOT_BASE=1 로만 허용.)
if [ -f frontend/dist/index.html ] && [ "${HEAX_DIST_ALLOW_ROOT_BASE:-0}" != "1" ]; then
  if ! grep -q '"/heax-hub/assets/' frontend/dist/index.html; then
    echo "✗ frontend/dist 가 /heax-hub/ base 로 빌드되지 않음 — 포털 배포 시 자산 404가 난다."
    echo "  다시 빌드:  VITE_BASE_PATH=/heax-hub/ pnpm --dir frontend build"
    echo "  (의도적 루트 배포면 HEAX_DIST_ALLOW_ROOT_BASE=1)"
    exit 1
  fi
fi

TS="$(date -u +%Y%m%d-%H%M%SZ)"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
# frontend/dist 는 있으면 포함, 없으면 런타임/base 만 푸시(폴백 저장소 목적).
if [ -f frontend/dist/index.html ]; then
  ( cd frontend && tar -czf "$STAGE/frontend-dist.tar.gz" dist )
  echo "  · including frontend-dist.tar.gz"
  # 이 Drive 는 포털 서브경로 배포용. dist 가 루트(/) base 면 /heax-hub/ 아래에서
  # 에셋이 /assets/... 로 요청돼 404 가 난다. 루트 base 면 경고(차단은 안 함).
  if grep -q '"/assets/' frontend/dist/index.html 2>/dev/null; then
    echo "  ⚠ 경고: dist 가 루트(/) base 로 빌드됨 — 포털 서브경로(/heax-hub/ 등) 배포 시 에셋 404." >&2
    echo "         재빌드 권장: VITE_BASE_PATH=/heax-hub/ pnpm --dir frontend build" >&2
  fi
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

# ── per-app SIFs (등록 앱: materialtwin·laminate·thermal-shock 등) ─────────────
# 폐쇄망 서버가 git·빌드 없이 최신 앱 SIF 를 받아 그대로 띄우게 한다(var/sifs/<slug>.sif
# + .sif.hash). 데모(heax-demo-*)는 제외. 끄기: HEAX_DRIVE_WITH_APP_SIFS=0 ·
# 명시목록: HEAX_DRIVE_APP_SIFS="materialtwin-web thermal-shock-mcp".
if [ "${HEAX_DRIVE_WITH_APP_SIFS:-1}" = "1" ]; then
  APP_SIF_DIR="$ROOT_DIR/var/sifs"
  _ship_app_sif() {  # $1=절대 sif 경로
    local f="$1" b; b="$(basename "$f")"
    cp "$f" "$STAGE/"; echo "  · app SIF: $b ($(du -h "$f" | cut -f1))"
    [ -f "$f.hash" ] && cp "$f.hash" "$STAGE/"
  }
  if [ -n "${HEAX_DRIVE_APP_SIFS:-}" ]; then          # 명시 목록
    for slug in $HEAX_DRIVE_APP_SIFS; do
      [ -f "$APP_SIF_DIR/$slug.sif" ] && _ship_app_sif "$APP_SIF_DIR/$slug.sif"
    done
  else                                                # 기본: 데모 제외한 전체 앱 SIF
    for f in "$APP_SIF_DIR"/*.sif; do
      [ -f "$f" ] || continue
      case "$(basename "$f")" in heax-demo-*) continue;; esac
      _ship_app_sif "$f"
    done
  fi
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
