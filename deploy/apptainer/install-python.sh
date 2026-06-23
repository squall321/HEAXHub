#!/usr/bin/env bash
# HEAXHub — 핀 standalone Python 설치 (시스템 무손상).
#
# 왜:
#   백엔드(uvicorn/celery)는 venv 위에서 도는데, 그 venv 를 만드는 base python 이
#   호스트의 시스템 python3.11 이면 "타깃에 python 사전설치" 의존이 생긴다.
#   apptainer 를 .deb 로 .tools/ 에 푸는 것과 똑같이, relocatable standalone
#   Python(python-build-standalone) 을 .tools/python-<ver>/ 에 "풀기만" 한다.
#   → root 불필요, 시스템 python 무관, 백엔드는 그대로 host-native 로 실행되어
#     apptainer 오케스트레이션(중첩 없음)이 정상 동작한다.
#
# 소스 우선순위:
#   1) 이미 .tools/python-<ver>/bin/python3 존재하고 동작 → skip
#   2) cache/python-<ver>-x86_64-linux.tar.gz (vendored, 오프라인) → 추출
#   3) uv 가 있으면 `uv python install` 로 받아 self-contained 복사 + cache 생성
#   4) python-build-standalone 최신 install_only 릴리스 다운로드
#
# 사용:
#   bash deploy/apptainer/install-python.sh
#   PY_VERSION=3.12.13 bash deploy/apptainer/install-python.sh
#   bash deploy/apptainer/install-python.sh --force
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

PY_VERSION="${PY_VERSION:-3.12.13}"
PY_ARCH="${PY_ARCH:-x86_64}"
PREFIX="$TOOLS_DIR/python-$PY_VERSION"
PY_BIN="$PREFIX/bin/python3"
CACHE_TARBALL="$CACHE_DIR/python-${PY_VERSION}-${PY_ARCH}-linux.tar.gz"

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \?//'; exit 0 ;;
  esac
done

mkdir -p "$TOOLS_DIR" "$CACHE_DIR"

# ── 1) 이미 설치돼 있으면 skip ────────────────────────────────────────────────
if [[ $FORCE -eq 0 && -x "$PY_BIN" ]] && "$PY_BIN" --version >/dev/null 2>&1; then
  ok "이미 설치됨: $PY_BIN ($("$PY_BIN" --version 2>&1))"
  echo "$PY_BIN"
  exit 0
fi

[[ $FORCE -eq 1 ]] && rm -rf "$PREFIX"

# 추출 헬퍼: cache 타르볼(최상위 dir = python-<ver>)을 .tools/ 로 푼다.
extract_cache() {
  step "cache 타르볼 추출 → $PREFIX"
  rm -rf "$PREFIX"
  # TAR_OPTIONS=--exclude-vcs-ignores 환경에서도 전체가 풀리도록 무력화.
  env -u TAR_OPTIONS tar -xzf "$CACHE_TARBALL" -C "$TOOLS_DIR"
}

# self-contained 트리를 cache 타르볼로 굳힌다(다음 실행/오프라인 번들 재사용).
freeze_cache() {
  local src="$1"
  step "cache 타르볼 생성 → $CACHE_TARBALL"
  env -u TAR_OPTIONS tar -czf "$CACHE_TARBALL" -C "$(dirname "$src")" "$(basename "$src")"
  ok "cache: $CACHE_TARBALL ($(du -h "$CACHE_TARBALL" | cut -f1))"
}

# ── 2) vendored cache 우선 ────────────────────────────────────────────────────
if [[ -f "$CACHE_TARBALL" ]]; then
  ok "vendored cache 사용: $CACHE_TARBALL"
  extract_cache
# ── 2b) Drive 폴백 (PyPI/GitHub 막혀도 서버가 닿는 Drive 에서) ───────────────
elif drive_fetch "$(basename "$CACHE_TARBALL")" "$CACHE_TARBALL"; then
  ok "→ Drive 폴백에서 python tarball 받음: $CACHE_TARBALL"
  extract_cache
# ── 3) uv 로 취득 (온라인 dev 박스) ──────────────────────────────────────────
elif command -v uv >/dev/null 2>&1; then
  step "uv 로 standalone python $PY_VERSION 취득"
  export_proxy 2>/dev/null || true
  uv python install "$PY_VERSION" >&2
  # uv 의 실제 설치 루트(심볼릭 alias 가 아닌 풀 트리)를 찾는다.
  uv_link="$(uv python find "$PY_VERSION" 2>/dev/null || true)"
  [[ -n "$uv_link" ]] || { err "uv python find 실패"; exit 1; }
  uv_real="$(readlink -f "$uv_link")"          # .../cpython-<ver>.../bin/python3.12
  uv_root="$(dirname "$(dirname "$uv_real")")" # .../cpython-<ver>-linux-x86_64-gnu
  rm -rf "$PREFIX"
  cp -rL "$uv_root" "$PREFIX"                   # -L: symlink dereference → self-contained
  freeze_cache "$PREFIX"
# ── 4) python-build-standalone 다운로드 ──────────────────────────────────────
else
  step "python-build-standalone 최신 install_only 다운로드 ($PY_VERSION, $PY_ARCH)"
  export_proxy 2>/dev/null || true
  api="https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
  url="$(curl -fsSL -m 60 "$api" 2>/dev/null \
        | grep -oE "https://[^\"]*cpython-${PY_VERSION}\+[0-9]+-${PY_ARCH}-unknown-linux-gnu-install_only\.tar\.gz" \
        | head -1 || true)"
  if [[ -z "$url" ]]; then
    err "다운로드 URL 해석 실패(레이트리밋/네트워크). 다음 중 하나:"
    err "  - 온라인 호스트에서 uv 설치 후 재실행, 또는"
    err "  - install_only 타르볼을 직접 받아 $CACHE_TARBALL 에 두고 재실행"
    exit 1
  fi
  tmp="$(mktemp -d)/py.tar.gz"
  curl -fsSL -m 300 "$url" -o "$tmp"
  rm -rf "$PREFIX"; mkdir -p "$PREFIX"
  # install_only 타르볼의 최상위는 python/ → 그 내용물을 PREFIX 로 옮긴다.
  env -u TAR_OPTIONS tar -xzf "$tmp" -C "$PREFIX" --strip-components=1
  rm -rf "$(dirname "$tmp")"
  freeze_cache "$PREFIX"
fi

# ── 검증 ──────────────────────────────────────────────────────────────────────
if [[ ! -x "$PY_BIN" ]] || ! "$PY_BIN" --version >/dev/null 2>&1; then
  err "설치 후 python 실행 실패: $PY_BIN"
  exit 1
fi
ok "설치 완료: $PY_BIN ($("$PY_BIN" --version 2>&1))"
note "venv 예: $PY_BIN -m venv backend/.venv"
echo "$PY_BIN"
