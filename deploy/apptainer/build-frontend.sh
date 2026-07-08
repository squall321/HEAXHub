#!/usr/bin/env bash
# 프론트엔드(Vite/React) dist 를 빌드한다 — 폐쇄망이면 vendored node+pnpm + 오프라인 스토어로.
#
# 우선순위:
#   1) .tools/node-<ver>/bin 의 vendored node+pnpm 사용(install-node.sh / mirror-from-drive.sh).
#      없으면 PATH 의 시스템 pnpm 폴백.
#   2) var/pkg-mirror/npm/store 오프라인 스토어가 있으면 pnpm install --offline,
#      없으면 온라인 install.
#   3) HEAX_BASE_PATH(포털 서브패스, 예 /heax-hub/)를 VITE_BASE_PATH 로 넘겨 빌드.
#
# 사용:  HEAX_BASE_PATH=/heax-hub/ bash deploy/apptainer/build-frontend.sh
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env 2>/dev/null || true

FRONTEND_DIR="$ROOT_DIR/frontend"
[ -f "$FRONTEND_DIR/pnpm-lock.yaml" ] || { err "frontend/pnpm-lock.yaml 없음"; exit 1; }

# 1) vendored node+pnpm 우선
VNODE_BIN="$(ls -d "$TOOLS_DIR"/node-*/bin 2>/dev/null | sort -V | tail -1 || true)"
if [ -n "$VNODE_BIN" ] && [ -x "$VNODE_BIN/pnpm" ]; then
  export PATH="$VNODE_BIN:$PATH"
  note "vendored 툴체인 사용: $VNODE_BIN (node $("$VNODE_BIN/node" --version), pnpm $(pnpm --version))"
elif command -v pnpm >/dev/null 2>&1; then
  note "시스템 pnpm 사용: $(command -v pnpm) ($(pnpm --version))"
else
  err "node/pnpm 없음 — 온라인에서 install-node.sh 또는 mirror-from-drive.sh 로 툴체인을 받으세요."
  exit 1
fi

cd "$FRONTEND_DIR"
STORE="$ROOT_DIR/var/pkg-mirror/npm/store"

# 2) 의존성 설치 (오프라인 스토어 있으면 --offline)
if [ -d "$STORE" ]; then
  step "pnpm install --offline (스토어: $STORE)"
  CI=true pnpm install --offline --frozen-lockfile --store-dir "$STORE"
else
  step "pnpm install (온라인 — 오프라인 스토어 없음)"
  CI=true pnpm install --frozen-lockfile
fi

# 3) 빌드 (포털 서브패스 base 주입)
BASE="${HEAX_BASE_PATH:-/}"
step "pnpm build (VITE_BASE_PATH=$BASE)"
CI=true VITE_BASE_PATH="$BASE" pnpm build

[ -f dist/index.html ] || { err "빌드 후 dist/index.html 없음"; exit 1; }
ok "frontend dist 빌드 완료 → $FRONTEND_DIR/dist (base $BASE)"
