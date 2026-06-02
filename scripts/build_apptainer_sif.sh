#!/usr/bin/env bash
# build_apptainer_sif.sh — Apptainer SIF 빌드.
#
# 사용법:
#     build_apptainer_sif.sh <app_id>
#
# 동작:
#   1. overlay/.portal/Apptainer.def 또는 upstream/Apptainer.def 또는
#      upstream/.portal/Apptainer.def 중 가장 먼저 발견되는 정의 파일 사용
#   2. apptainer build → sif/app.sif
#   3. build/build.log, build/status.json 작성
#
# 환경 변수:
#   WORKSPACE_ROOT - 워크스페이스 루트 (기본 ./app_workspaces)
#   APPTAINER_BIN  - apptainer 바이너리 경로 (기본: 'apptainer' on PATH)

set -euo pipefail

LOG_PREFIX="[build-sif]"
log()  { echo "$LOG_PREFIX $*" >&2; }
fail() { echo "$LOG_PREFIX ERROR: $*" >&2; exit 1; }

if [ "$#" -lt 1 ]; then
    fail "usage: $0 <app_id>"
fi

APP_ID="$1"
WORKSPACE_ROOT="${WORKSPACE_ROOT:-./app_workspaces}"
APPTAINER="${APPTAINER_BIN:-apptainer}"

WS_DIR="$WORKSPACE_ROOT/$APP_ID"
UPSTREAM_DIR="$WS_DIR/upstream"
OVERLAY_PORTAL="$WS_DIR/overlay/.portal"
SIF_DIR="$WS_DIR/sif"
BUILD_DIR="$WS_DIR/build"
BUILD_LOG="$BUILD_DIR/build.log"
STATUS_FILE="$BUILD_DIR/status.json"
SIF_PATH="$SIF_DIR/app.sif"

[ -d "$WS_DIR" ]       || fail "workspace not found: $WS_DIR"
[ -d "$UPSTREAM_DIR" ] || fail "upstream not found: $UPSTREAM_DIR"

command -v "$APPTAINER" >/dev/null 2>&1 || fail "apptainer not found (set APPTAINER_BIN)"

mkdir -p "$SIF_DIR" "$BUILD_DIR"

# Apptainer.def 탐색 (overlay 우선)
DEF_FILE=""
for candidate in \
    "$OVERLAY_PORTAL/Apptainer.def" \
    "$UPSTREAM_DIR/.portal/Apptainer.def" \
    "$UPSTREAM_DIR/Apptainer.def"
do
    if [ -f "$candidate" ]; then
        DEF_FILE="$candidate"
        break
    fi
done

# Dockerfile fallback. The production target is offline, so this path is
# intentionally noisy: it requires a local Docker daemon that the target
# server will NOT have. Authors should ship an Apptainer.def instead.
DOCKERFILE=""
if [ -z "$DEF_FILE" ]; then
    for cand in \
        "$OVERLAY_PORTAL/Dockerfile" \
        "$UPSTREAM_DIR/.portal/Dockerfile" \
        "$UPSTREAM_DIR/Dockerfile"
    do
        if [ -f "$cand" ]; then
            DOCKERFILE="$cand"
            break
        fi
    done
fi

if [ -z "$DEF_FILE" ] && [ -z "$DOCKERFILE" ]; then
    fail "no Apptainer.def or Dockerfile found (looked in overlay/.portal, upstream/.portal, upstream)"
fi

if [ -n "$DEF_FILE" ]; then
    log "using definition: $DEF_FILE"
else
    log "WARNING: no Apptainer.def found; attempting Dockerfile fallback ($DOCKERFILE)"
    log "WARNING: this requires a local Docker daemon. Offline production"
    log "WARNING: targets MUST ship an Apptainer.def — Dockerfile path will fail."
fi

write_status() {
    local status="$1" message="$2"
    local commit
    commit=$(git -C "$UPSTREAM_DIR" rev-parse HEAD 2>/dev/null || echo "unknown")
    local now
    now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    local size="0"
    if [ -f "$SIF_PATH" ]; then
        size=$(stat -c%s "$SIF_PATH" 2>/dev/null || echo "0")
    fi
    cat > "$STATUS_FILE" <<EOF
{
  "status": "$status",
  "app_id": "$APP_ID",
  "commit": "$commit",
  "definition": "$DEF_FILE",
  "sif_path": "$SIF_PATH",
  "sif_size_bytes": $size,
  "build_log": "$BUILD_LOG",
  "finished_at": "$now",
  "message": "$message"
}
EOF
}

trap 'rc=$?; if [ $rc -ne 0 ]; then write_status "failed" "see build.log"; fi; exit $rc' EXIT

{
    echo "=== HEAXHub build-sif log ==="
    echo "app_id    : $APP_ID"
    echo "apptainer : $($APPTAINER --version 2>&1 | head -1)"
    echo "definition: $DEF_FILE"
    echo "started   : $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    echo "---"
} > "$BUILD_LOG"

# 기존 SIF 백업
if [ -f "$SIF_PATH" ]; then
    mv "$SIF_PATH" "$SIF_PATH.prev"
    log "backed up existing SIF → app.sif.prev"
fi

log "building $SIF_PATH"
# Offline-safe build:
#   --force        : overwrite existing $SIF_PATH (we already backed it up).
#   --disable-cache: don't touch ~/.apptainer cache (no internet on prod).
#   --no-https     : redundant for def files but documents intent.
# We never pass --remote / --library; those require network registries.
if [ -n "$DEF_FILE" ]; then
    "$APPTAINER" build --force --disable-cache "$SIF_PATH" "$DEF_FILE" >>"$BUILD_LOG" 2>&1
else
    # Dockerfile fallback path — needs a local Docker daemon, fails offline.
    if ! command -v docker >/dev/null 2>&1; then
        fail "Dockerfile fallback requested but 'docker' not on PATH. \
Offline target servers MUST ship an Apptainer.def — see templates/cpp-cli/.portal/Apptainer.def."
    fi
    log "building local docker image first → docker-daemon://heaxhub-build-$APP_ID:tmp"
    if ! docker build -t "heaxhub-build-$APP_ID:tmp" -f "$DOCKERFILE" \
            "$(dirname "$DOCKERFILE")" >>"$BUILD_LOG" 2>&1; then
        fail "docker build failed (offline server? ship an Apptainer.def instead)"
    fi
    "$APPTAINER" build --force --disable-cache "$SIF_PATH" \
        "docker-daemon://heaxhub-build-$APP_ID:tmp" >>"$BUILD_LOG" 2>&1 || \
        fail "apptainer build from docker-daemon failed (offline?)"
fi

[ -f "$SIF_PATH" ] || fail "SIF was not produced at $SIF_PATH"

log "build success ($(du -h "$SIF_PATH" | cut -f1))"
write_status "success" "build completed"

trap - EXIT
exit 0
