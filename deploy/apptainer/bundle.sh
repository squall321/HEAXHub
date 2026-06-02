#!/usr/bin/env bash
# HEAXHub — 오프라인 번들 생성기.
#
# 다음을 한 tar 안에 포함:
#   - 소스 (backend, frontend src, deploy/apptainer/*, scripts/*, docs/*)
#   - 사전 빌드 산출물: --with-sif 시 deploy/apptainer/*.sif
#                       --with-dist 시 frontend/dist
#                       --with-wheels 시 wheels sidecar (pip download)
#                       --with-deb 시 infra/packages/deb sidecar
#                       --with-agent 시 agents/windows 바이너리
#   - manifest.txt + sha256
#
# 자동 exclude:
#   .git / .venv / node_modules / __pycache__ / .bkit / var/ / dist-bundle/
#
# 출력: /tmp/heaxhub-bundle-YYYYMMDD-HHMMSS.tar.gz (+ sidecar 들)
#
# 사용:
#   bash deploy/apptainer/bundle.sh                 # 코드만
#   bash deploy/apptainer/bundle.sh --all           # SIF + dist + agent (사내망 새 서버)
#   bash deploy/apptainer/bundle.sh --offline-all   # 위 + wheels + deb (완전 폐쇄망)
#   bash deploy/apptainer/bundle.sh --output /path/x.tar.gz
set -euo pipefail

# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env 2>/dev/null || true

TS="$(date +%Y%m%d-%H%M%S)"
OUT_DIR="${ROOT_DIR}/dist-bundle"
OUT="${OUT_DIR}/heaxhub-bundle-${TS}.tar.gz"
WITH_SIF=0
WITH_DIST=0
WITH_AGENT=0
WITH_WHEELS=0
WITH_DEB=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output|-o)   OUT="$2"; shift 2 ;;
    --with-sif)    WITH_SIF=1; shift ;;
    --with-dist)   WITH_DIST=1; shift ;;
    --with-agent)  WITH_AGENT=1; shift ;;
    --with-wheels) WITH_WHEELS=1; shift ;;
    --with-deb)    WITH_DEB=1; shift ;;
    --all)         WITH_SIF=1; WITH_DIST=1; WITH_AGENT=1; shift ;;
    --offline-all) WITH_SIF=1; WITH_DIST=1; WITH_AGENT=1; WITH_WHEELS=1; WITH_DEB=1; shift ;;
    -h|--help)     sed -n '2,22p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) err "unknown arg: $1"; exit 2 ;;
  esac
done

mkdir -p "$OUT_DIR"

# ── 자동 exclude ────────────────────────────────────────────────────────
EXCLUDES=(
  --exclude='.git'
  --exclude='.venv'
  --exclude='__pycache__'
  --exclude='node_modules'
  --exclude='.bkit'
  --exclude='var'
  --exclude='dist-bundle'
  --exclude='.pytest_cache'
  --exclude='.mypy_cache'
  --exclude='.ruff_cache'
  --exclude='.koo-llm-sessions'
  --exclude='*.log'
  --exclude='cache'
  --exclude='.tools'
)

# WITH_* 가 아니면 큰 것들도 제외
[[ $WITH_SIF   -eq 0 ]] && EXCLUDES+=( --exclude='deploy/apptainer/*.sif' --exclude='deploy/apptainer/postgres-base.sif' )
[[ $WITH_DIST  -eq 0 ]] && EXCLUDES+=( --exclude='frontend/dist' )
[[ $WITH_AGENT -eq 0 ]] && EXCLUDES+=( --exclude='agents/windows/bin' --exclude='agents/windows/obj' )

# ── 메인 tar ───────────────────────────────────────────────────────────
step "메인 번들 생성: $OUT"
( cd "$ROOT_DIR/.." && tar czf "$OUT" "${EXCLUDES[@]}" "$(basename "$ROOT_DIR")" )
ok "메인 tar 완료 ($(du -sh "$OUT" | awk '{print $1}'))"

# ── sidecar: wheels ────────────────────────────────────────────────────
if [[ $WITH_WHEELS -eq 1 ]]; then
  step "wheels sidecar 생성"
  WHEELS_DIR_OUT="${OUT%.tar.gz}.wheels"
  mkdir -p "$WHEELS_DIR_OUT"
  PIP="${BACKEND_DIR}/.venv/bin/pip"
  [[ -x "$PIP" ]] || PIP="$(command -v pip3 || command -v pip)"
  FREEZE_TMP="$(mktemp)"
  "$PIP" freeze | sed -e '/^-e /d' -e '/^# /d' > "$FREEZE_TMP"
  "$PIP" download -d "$WHEELS_DIR_OUT" -r "$FREEZE_TMP" --no-build-isolation \
    || warn "pip download 일부 실패 — $WHEELS_DIR_OUT 점검"
  ( cd "$(dirname "$WHEELS_DIR_OUT")" && tar czf "${OUT%.tar.gz}.wheels.tar.gz" "$(basename "$WHEELS_DIR_OUT")" )
  rm -rf "$WHEELS_DIR_OUT" "$FREEZE_TMP"
  ok "wheels sidecar: ${OUT%.tar.gz}.wheels.tar.gz"
fi

# ── sidecar: .deb 캐시 ────────────────────────────────────────────────
if [[ $WITH_DEB -eq 1 ]]; then
  step ".deb sidecar"
  if [[ -d "$ROOT_DIR/infra/packages/deb" ]]; then
    ( cd "$ROOT_DIR/infra/packages" \
      && tar czf "${OUT%.tar.gz}.deb.tar.gz" deb )
    ok ".deb sidecar: ${OUT%.tar.gz}.deb.tar.gz"
  else
    warn "infra/packages/deb 없음 — .deb sidecar 생성 안 함"
  fi
fi

# ── manifest + sha256 ─────────────────────────────────────────────────
step "manifest + sha256"
{
  echo "HEAXHub Offline Bundle"
  echo "built_at: $TS"
  echo "git_sha:  $(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "host:     $(hostname)"
  echo ""
  echo "files:"
  for f in "$OUT" "${OUT%.tar.gz}.wheels.tar.gz" "${OUT%.tar.gz}.deb.tar.gz"; do
    [[ -f "$f" ]] && printf '  %-12s  %s\n' "$(du -sh "$f" | awk '{print $1}')" "$(basename "$f")"
  done
  echo ""
  echo "sha256:"
  for f in "$OUT" "${OUT%.tar.gz}.wheels.tar.gz" "${OUT%.tar.gz}.deb.tar.gz"; do
    [[ -f "$f" ]] && sha256sum "$f" | awk '{printf "  %s  %s\n", $1, $2}'
  done
} > "${OUT%.tar.gz}.manifest.txt"
ok "manifest: ${OUT%.tar.gz}.manifest.txt"

echo
echo "================================================================"
echo " 번들 완료"
echo "  메인         : $OUT"
[[ -f "${OUT%.tar.gz}.wheels.tar.gz" ]] && echo "  wheels       : ${OUT%.tar.gz}.wheels.tar.gz"
[[ -f "${OUT%.tar.gz}.deb.tar.gz" ]]    && echo "  .deb         : ${OUT%.tar.gz}.deb.tar.gz"
echo "  manifest     : ${OUT%.tar.gz}.manifest.txt"
echo
echo " 타깃 서버 사용법:"
echo "   tar xzf $(basename "$OUT") && cd HEAXHub"
echo "   [ -f ../wheels.tar.gz ] && tar xzf ../wheels.tar.gz"
echo "   sudo bash scripts/bootstrap-host.sh"
echo "   bash deploy/apptainer/install_all.sh"
echo "================================================================"
