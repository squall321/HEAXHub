#!/usr/bin/env bash
# HEAXHub — 핀 standalone Node + pnpm 설치 (시스템 무손상).
#
# 왜:
#   프론트엔드(Vite/React) 빌드는 pnpm 이 필요한데, 폐쇄망 서버엔 node/pnpm 이 없다.
#   apptainer(.deb)·python(python-build-standalone) 을 .tools/ 에 "풀기만" 하는 것과
#   똑같이, node 공식 standalone + pnpm standalone 바이너리를 .tools/node-<ver>/ 에
#   푼다. → root 불필요, 시스템 node 무관. 의존성(node_modules)은 별도로 pnpm 오프라인
#   스토어(mirror-*-drive.sh 가 나르는 npm 미러)로 채운다.
#
# 소스 우선순위:
#   1) 이미 .tools/node-<ver>/bin/{node,pnpm} 존재하고 동작 → skip
#   2) cache/node-<ver>-linux-<arch>.tar.gz (vendored, 오프라인) → 추출
#   3) Drive 폴백(mirror 가 올린 tarball) → 추출
#   4) nodejs.org + github(pnpm) 에서 다운로드 → 조립 → cache 생성
#
# 사용:
#   bash deploy/apptainer/install-node.sh
#   NODE_VERSION=20.19.6 PNPM_VERSION=10.23.0 bash deploy/apptainer/install-node.sh
#   bash deploy/apptainer/install-node.sh --force
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

NODE_VERSION="${NODE_VERSION:-20.19.6}"
PNPM_VERSION="${PNPM_VERSION:-10.23.0}"
NODE_ARCH="${NODE_ARCH:-x64}"   # node/pnpm 릴리스 네이밍은 linux-x64
PREFIX="$TOOLS_DIR/node-$NODE_VERSION"
NODE_BIN="$PREFIX/bin/node"
PNPM_BIN="$PREFIX/bin/pnpm"
CACHE_TARBALL="$CACHE_DIR/node-${NODE_VERSION}-linux-${NODE_ARCH}.tar.gz"

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,28p' "$0" | sed 's/^# \?//'; exit 0 ;;
  esac
done

mkdir -p "$TOOLS_DIR" "$CACHE_DIR"

# ── 1) 이미 설치돼 있으면 skip ────────────────────────────────────────────────
if [[ $FORCE -eq 0 && -x "$NODE_BIN" && -x "$PNPM_BIN" ]] \
   && "$NODE_BIN" --version >/dev/null 2>&1 \
   && PATH="$PREFIX/bin:$PATH" "$PNPM_BIN" --version >/dev/null 2>&1; then
  ok "이미 설치됨: $PREFIX (node $("$NODE_BIN" --version), pnpm $(PATH="$PREFIX/bin:$PATH" "$PNPM_BIN" --version))"
  echo "$PREFIX/bin"
  exit 0
fi

[[ $FORCE -eq 1 ]] && rm -rf "$PREFIX"

# 추출 헬퍼: cache 타르볼(최상위 dir = node-<ver>)을 .tools/ 로 푼다.
extract_cache() {
  step "cache 타르볼 추출 → $PREFIX"
  rm -rf "$PREFIX"
  env -u TAR_OPTIONS tar -xzf "$CACHE_TARBALL" -C "$TOOLS_DIR"
}

# 조립된 트리(node+pnpm)를 cache 타르볼로 굳힌다(다음 실행/오프라인 번들 재사용).
freeze_cache() {
  step "cache 타르볼 생성 → $CACHE_TARBALL"
  env -u TAR_OPTIONS tar -czf "$CACHE_TARBALL" -C "$TOOLS_DIR" "node-$NODE_VERSION"
  ok "cache: $CACHE_TARBALL ($(du -h "$CACHE_TARBALL" | cut -f1))"
}

# nodejs.org 에서 node standalone + github 에서 pnpm standalone 을 받아 PREFIX 로 조립.
download_assemble() {
  step "node $NODE_VERSION + pnpm $PNPM_VERSION 다운로드 (linux-$NODE_ARCH)"
  export_proxy 2>/dev/null || true
  local tmp; tmp="$(mktemp -d)"
  local node_url="https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz"
  local pnpm_url="https://github.com/pnpm/pnpm/releases/download/v${PNPM_VERSION}/pnpm-linux-${NODE_ARCH}"
  note "node: $node_url"
  curl -fsSL -m 300 "$node_url" -o "$tmp/node.tar.xz" || { err "node 다운로드 실패"; rm -rf "$tmp"; exit 1; }
  rm -rf "$PREFIX"; mkdir -p "$PREFIX"
  # node tarball 최상위는 node-vX-linux-x64/ → 내용물을 PREFIX 로.
  env -u TAR_OPTIONS tar -xJf "$tmp/node.tar.xz" -C "$PREFIX" --strip-components=1
  note "pnpm: $pnpm_url"
  curl -fsSL -m 120 "$pnpm_url" -o "$PREFIX/bin/pnpm" || { err "pnpm 다운로드 실패"; rm -rf "$tmp"; exit 1; }
  chmod +x "$PREFIX/bin/pnpm"
  rm -rf "$tmp"
  freeze_cache
}

# ── 2) vendored cache 우선 → 2b) Drive 폴백 → 4) 다운로드 ──────────────────────
if [[ -f "$CACHE_TARBALL" ]]; then
  ok "vendored cache 사용: $CACHE_TARBALL"
  extract_cache
elif drive_fetch "$(basename "$CACHE_TARBALL")" "$CACHE_TARBALL"; then
  ok "→ Drive 폴백에서 node tarball 받음: $CACHE_TARBALL"
  extract_cache
else
  download_assemble
fi

# ── 검증 ──────────────────────────────────────────────────────────────────────
if [[ ! -x "$NODE_BIN" ]] || ! "$NODE_BIN" --version >/dev/null 2>&1; then
  err "설치 후 node 실행 실패: $NODE_BIN"; exit 1
fi
if [[ ! -x "$PNPM_BIN" ]] || ! PATH="$PREFIX/bin:$PATH" "$PNPM_BIN" --version >/dev/null 2>&1; then
  err "설치 후 pnpm 실행 실패: $PNPM_BIN"; exit 1
fi
ok "설치 완료: $PREFIX (node $("$NODE_BIN" --version), pnpm $(PATH="$PREFIX/bin:$PATH" "$PNPM_BIN" --version))"
note "PATH 에 추가해서 사용:  export PATH=\"$PREFIX/bin:\$PATH\""
echo "$PREFIX/bin"
