#!/usr/bin/env bash
# provision_workspace.sh — 신청 승인 시 호출되는 워크스페이스 프로비저너.
#
# 사용법:
#     provision_workspace.sh <app_id> <git_url>
#
# 동작:
#   1. WORKSPACE_ROOT/<app_id>/ 디렉터리 생성
#   2. git clone --depth 1 <git_url> upstream/
#   3. overlay/.portal/ 생성
#   4. upstream 에 .portal/manifest.yaml 이 있으면 overlay 로 복사
#   5. overlay/upstream.lock 에 commit sha 기록
#
# 환경 변수:
#   WORKSPACE_ROOT  - 워크스페이스 루트 (기본 ./app_workspaces)
#   ALLOWED_GIT_HOSTS - 콤마 분리 화이트리스트 (선택, 비어 있으면 검사 생략)

set -euo pipefail

LOG_PREFIX="[provision]"
log()  { echo "$LOG_PREFIX $*" >&2; }
fail() { echo "$LOG_PREFIX ERROR: $*" >&2; exit 1; }

# ----- 인자 검증 -----
if [ "$#" -lt 2 ]; then
    fail "usage: $0 <app_id> <git_url>"
fi

APP_ID="$1"
GIT_URL="$2"

if ! echo "$APP_ID" | grep -Eq '^[a-z][a-z0-9_]{2,63}$'; then
    fail "invalid app_id '$APP_ID' (must match ^[a-z][a-z0-9_]{2,63}\$)"
fi

# ----- 환경 변수 -----
WORKSPACE_ROOT="${WORKSPACE_ROOT:-./app_workspaces}"

# ----- git 호스트 화이트리스트 (선택) -----
if [ -n "${ALLOWED_GIT_HOSTS:-}" ]; then
    host=$(echo "$GIT_URL" | sed -E 's#^(https?://|git@)([^/:]+).*#\2#')
    allowed=0
    IFS=',' read -ra hosts <<< "$ALLOWED_GIT_HOSTS"
    for h in "${hosts[@]}"; do
        h_trim=$(echo "$h" | xargs)
        if [ "$host" = "$h_trim" ]; then
            allowed=1
            break
        fi
    done
    if [ "$allowed" -ne 1 ]; then
        fail "git host '$host' is not in ALLOWED_GIT_HOSTS ($ALLOWED_GIT_HOSTS)"
    fi
fi

# ----- 경로 결정 -----
WS_DIR="$WORKSPACE_ROOT/$APP_ID"
UPSTREAM_DIR="$WS_DIR/upstream"
OVERLAY_DIR="$WS_DIR/overlay"
PORTAL_DIR="$OVERLAY_DIR/.portal"
LOCK_FILE="$OVERLAY_DIR/upstream.lock"

if [ -e "$WS_DIR" ]; then
    fail "workspace already exists: $WS_DIR (delete or use a different app_id)"
fi

log "creating workspace at $WS_DIR"
mkdir -p "$WS_DIR" "$PORTAL_DIR"

# ----- git clone -----
log "git clone --depth 1 $GIT_URL → $UPSTREAM_DIR"
if ! git clone --depth 1 "$GIT_URL" "$UPSTREAM_DIR" >/dev/null; then
    fail "git clone failed for $GIT_URL"
fi

# ----- manifest 복사 (있을 때만) -----
SRC_MANIFEST="$UPSTREAM_DIR/.portal/manifest.yaml"
if [ -f "$SRC_MANIFEST" ]; then
    cp "$SRC_MANIFEST" "$PORTAL_DIR/manifest.yaml"
    log "copied manifest.yaml from upstream"
else
    log "WARNING: upstream has no .portal/manifest.yaml — operator must author one"
fi

# overlay 측 표준 run.sh 가 upstream 에 있으면 동시에 복사 (편의)
SRC_RUN="$UPSTREAM_DIR/.portal/run.sh"
if [ -f "$SRC_RUN" ] && [ ! -f "$PORTAL_DIR/run.sh" ]; then
    cp "$SRC_RUN" "$PORTAL_DIR/run.sh"
    chmod +x "$PORTAL_DIR/run.sh"
fi

# ----- commit sha 기록 -----
COMMIT_SHA=$(git -C "$UPSTREAM_DIR" rev-parse HEAD)
ISO_NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat > "$LOCK_FILE" <<EOF
url: $GIT_URL
commit: $COMMIT_SHA
provisioned_at: $ISO_NOW
EOF

log "wrote $LOCK_FILE (commit=$COMMIT_SHA)"
log "done: $WS_DIR"
