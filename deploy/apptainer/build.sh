#!/usr/bin/env bash
# HEAXHub — SIF 빌드.
#
# 4 종 SIF 를 *.def 에서 빌드한다:
#   postgres.sif  postgres.def
#   redis.sif     redis.def
#   mailhog.sif   mailhog.def
#   caddy.sif     caddy.def
#
# 이미 존재하는 SIF 는 skip — 강제 재빌드는 --force.
# apptainer build 는 fakeroot 또는 --remote 가 필요할 수 있다:
#   - 기본: apptainer build --fakeroot
#   - 실패 시 --remote (Sylabs 계정 필요) 안내
#
# 사용:
#   bash deploy/apptainer/build.sh
#   bash deploy/apptainer/build.sh --force
#   bash deploy/apptainer/build.sh --only postgres
set -euo pipefail

# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env 2>/dev/null || true
export_proxy 2>/dev/null || true
require_apptainer

FORCE=0
ONLY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)  FORCE=1; shift ;;
    --only)   ONLY="$2"; shift 2 ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) err "unknown arg: $1"; exit 2 ;;
  esac
done

declare -A IMAGES=(
  [postgres]="postgres.def"
  [redis]="redis.def"
  [mailhog]="mailhog.def"
  [caddy]="caddy.def"
)

build_one() {
  local key="$1"
  local def="${IMAGES[$key]}"
  local sif="$APPT_DIR/${key}.sif"
  if [[ -f "$sif" && $FORCE -eq 0 ]]; then
    ok "skip $key (이미 존재: $sif). 강제 재빌드는 --force"
    return 0
  fi
  step "build $key  ($def → $sif)"
  if apptainer build --fakeroot "$sif" "$APPT_DIR/$def"; then
    ok "$key 빌드 완료"
  else
    warn "fakeroot 빌드 실패. 다음 중 하나로 시도:"
    warn "  1) sudo apt install -y uidmap && sudo apptainer config fakeroot --add $USER"
    warn "  2) apptainer build --remote $sif $def  (Sylabs 계정 토큰 필요)"
    warn "  3) 다른 머신에서 빌드 후 SIF 만 복사"
    return 1
  fi
}

if [[ -n "$ONLY" ]]; then
  [[ -n "${IMAGES[$ONLY]:-}" ]] || { err "알 수 없는 이미지: $ONLY"; exit 2; }
  build_one "$ONLY"
else
  for key in postgres redis mailhog caddy; do
    build_one "$key" || warn "$key 빌드 실패 — 나머지는 계속"
  done
fi
