#!/usr/bin/env bash
# HEAXHub — toolchain SIF 빌더 (오프라인 staging 용).
#
# deploy/apptainer/toolchain_*.def 4종을 각각 SIF 로 빌드한다:
#   toolchain_nodejs20.def   → heaxhub_toolchain_nodejs20.sif
#   toolchain_python312.def  → heaxhub_toolchain_python312.sif
#   toolchain_go122.def      → heaxhub_toolchain_go122.sif
#   toolchain_polyglot.def   → heaxhub_toolchain_polyglot.sif
#
# - 이미 SIF 가 있으면 skip (강제 재빌드는 --force).
# - 빌드는 `apptainer build --force <sif> <def>` 로 수행.
#   fakeroot/네임스페이스가 막혀 있으면 sudo 또는 --remote 가 필요할 수 있다.
# - 빌드 후 디스크 사용량과 오프라인 타깃에 배치할 위치를 출력.
#
# 사용:
#   bash deploy/apptainer/build-toolchains.sh                # 4종 전부
#   bash deploy/apptainer/build-toolchains.sh --only nodejs20
#   bash deploy/apptainer/build-toolchains.sh --force
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
    --force)   FORCE=1; shift ;;
    --only)    ONLY="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,22p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) err "unknown arg: $1"; exit 2 ;;
  esac
done

# key → def 파일 매핑. SIF 이름은 항상 heaxhub_toolchain_<key>.sif.
declare -A IMAGES=(
  [nodejs20]="toolchain_nodejs20.def"
  [python312]="toolchain_python312.def"
  [go122]="toolchain_go122.def"
  [polyglot]="toolchain_polyglot.def"
)
BUILD_ORDER=(nodejs20 python312 go122 polyglot)

sif_path_for() {
  local key="$1"
  echo "$APPT_DIR/heaxhub_toolchain_${key}.sif"
}

human_size() {
  local f="$1"
  if [[ -f "$f" ]]; then
    du -h "$f" 2>/dev/null | awk '{print $1}'
  else
    echo "n/a"
  fi
}

build_one() {
  local key="$1"
  local def="${IMAGES[$key]}"
  local def_path="$APPT_DIR/$def"
  local sif; sif="$(sif_path_for "$key")"

  if [[ ! -f "$def_path" ]]; then
    err "def 파일 없음: $def_path"
    return 1
  fi
  if [[ -f "$sif" && $FORCE -eq 0 ]]; then
    ok "skip toolchain[$key] (이미 존재: $sif, $(human_size "$sif")). 강제 재빌드는 --force"
    return 0
  fi

  step "build toolchain[$key]  ($def → $(basename "$sif"))"
  if apptainer build --force "$sif" "$def_path"; then
    ok "toolchain[$key] 빌드 완료 — $(human_size "$sif") @ $sif"
  else
    warn "toolchain[$key] 빌드 실패. 다음 중 하나로 재시도:"
    warn "  1) sudo bash $0 --only $key            (root 권한으로 build)"
    warn "  2) apptainer build --remote $sif $def_path   (Sylabs 토큰 필요)"
    warn "  3) 다른 머신에서 빌드 후 SIF 만 복사"
    return 1
  fi
}

# ── 어떤 키를 빌드할지 결정 ────────────────────────────────────────────────
declare -a TARGETS
if [[ -n "$ONLY" ]]; then
  [[ -n "${IMAGES[$ONLY]:-}" ]] || { err "알 수 없는 toolchain key: $ONLY (사용 가능: ${!IMAGES[*]})"; exit 2; }
  TARGETS=("$ONLY")
else
  TARGETS=("${BUILD_ORDER[@]}")
fi

# ── 빌드 루프 ──────────────────────────────────────────────────────────────
FAILED=()
for key in "${TARGETS[@]}"; do
  if ! build_one "$key"; then
    FAILED+=("$key")
  fi
done

# ── 결과 요약 ──────────────────────────────────────────────────────────────
echo ""
echo "================ toolchain build summary ================"
for key in "${TARGETS[@]}"; do
  sif="$(sif_path_for "$key")"
  if [[ -f "$sif" ]]; then
    printf "  [OK]   %-10s %6s  %s\n" "$key" "$(human_size "$sif")" "$sif"
  else
    printf "  [MISS] %-10s   ---   (not built)\n" "$key"
  fi
done
echo "========================================================="

cat <<EOF

오프라인 타깃에 배치할 위치:
  - 권장: 운영자가 SIF 디렉터리를 지정한 경우
        export HEAXHUB_TOOLCHAIN_SIF_DIR=/path/to/sifs
        해당 경로에 heaxhub_toolchain_*.sif 복사
  - 또는 기존 서비스 SIF 와 같은 디렉터리에 배치
        ~/serviceApptainers/heaxhub_toolchain_*.sif
        또는 deploy/apptainer/heaxhub_toolchain_*.sif
  - 번들에 포함하려면
        bash scripts/prepare_offline_bundle.sh --with-toolchains
EOF

if [[ ${#FAILED[@]} -gt 0 ]]; then
  warn "실패한 toolchain: ${FAILED[*]}"
  exit 1
fi
