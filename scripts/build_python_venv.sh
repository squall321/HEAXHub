#!/usr/bin/env bash
# build_python_venv.sh — 파이썬 앱 빌드.
#
# 사용법:
#     build_python_venv.sh <app_id> <python_version>
#
# 동작:
#   1. WORKSPACE_ROOT/<app_id>/venv 생성
#   2. pip / wheel 업그레이드
#   3. upstream/requirements.txt 있으면 설치, 없으면 pyproject.toml 설치
#   4. build/build.log 와 build/status.json 작성
#
# 환경 변수:
#   WORKSPACE_ROOT       - 워크스페이스 루트 (기본 ./app_workspaces)
#   PYTHON_BUILD_PATH    - 사용할 python 바이너리 (선택, 지정 시 python_version 인자보다 우선)

set -euo pipefail

LOG_PREFIX="[build-py]"
log()  { echo "$LOG_PREFIX $*" >&2; }
fail() { echo "$LOG_PREFIX ERROR: $*" >&2; exit 1; }

if [ "$#" -lt 2 ]; then
    fail "usage: $0 <app_id> <python_version>  (e.g. $0 my_tool 3.11)"
fi

APP_ID="$1"
PY_VER="$2"

WORKSPACE_ROOT="${WORKSPACE_ROOT:-./app_workspaces}"
WS_DIR="$WORKSPACE_ROOT/$APP_ID"
UPSTREAM_DIR="$WS_DIR/upstream"
VENV_DIR="$WS_DIR/venv"
BUILD_DIR="$WS_DIR/build"
BUILD_LOG="$BUILD_DIR/build.log"
STATUS_FILE="$BUILD_DIR/status.json"

[ -d "$WS_DIR" ]       || fail "workspace not found: $WS_DIR"
[ -d "$UPSTREAM_DIR" ] || fail "upstream not found: $UPSTREAM_DIR"

mkdir -p "$BUILD_DIR"

# 결과 status.json 작성 헬퍼 (성공/실패 통일된 포맷)
write_status() {
    local status="$1"
    local message="$2"
    local req_used="$3"
    local commit
    commit=$(git -C "$UPSTREAM_DIR" rev-parse HEAD 2>/dev/null || echo "unknown")
    local now
    now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    cat > "$STATUS_FILE" <<EOF
{
  "status": "$status",
  "app_id": "$APP_ID",
  "python_version": "$PY_VER",
  "commit": "$commit",
  "requirements_source": "$req_used",
  "venv_path": "$VENV_DIR",
  "build_log": "$BUILD_LOG",
  "finished_at": "$now",
  "message": "$message"
}
EOF
}

# 실패 시 status.json 에 failed 기록
trap 'rc=$?; if [ $rc -ne 0 ]; then write_status "failed" "see build.log" "${REQ_USED:-unknown}"; fi; exit $rc' EXIT

# ----- python 바이너리 결정 -----
PY_BIN=""
if [ -n "${PYTHON_BUILD_PATH:-}" ] && [ -x "$PYTHON_BUILD_PATH" ]; then
    PY_BIN="$PYTHON_BUILD_PATH"
elif command -v "python${PY_VER}" >/dev/null 2>&1; then
    PY_BIN=$(command -v "python${PY_VER}")
elif command -v python3 >/dev/null 2>&1; then
    PY_BIN=$(command -v python3)
    log "WARNING: python${PY_VER} not found, falling back to $PY_BIN"
else
    fail "no suitable python binary found"
fi

log "using python: $PY_BIN ($("$PY_BIN" --version 2>&1))"

# 기존 venv 삭제 (재빌드 대응)
if [ -d "$VENV_DIR" ]; then
    log "removing existing venv: $VENV_DIR"
    rm -rf "$VENV_DIR"
fi

# build.log 시작
{
    echo "=== HEAXHub build-py log ==="
    echo "app_id   : $APP_ID"
    echo "python   : $PY_BIN ($("$PY_BIN" --version 2>&1))"
    echo "started  : $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    echo "workspace: $WS_DIR"
    echo "---"
} > "$BUILD_LOG"

log "creating venv at $VENV_DIR"
"$PY_BIN" -m venv "$VENV_DIR" >>"$BUILD_LOG" 2>&1

# venv 활성화 (서브셸 대신 직접 실행)
PIP="$VENV_DIR/bin/pip"

log "upgrading pip, setuptools, wheel"
"$PIP" install --upgrade pip setuptools wheel >>"$BUILD_LOG" 2>&1

# 의존성 설치 우선순위: requirements.txt > pyproject.toml
REQ_TXT="$UPSTREAM_DIR/requirements.txt"
PYPROJECT="$UPSTREAM_DIR/pyproject.toml"
REQ_USED="none"

if [ -f "$REQ_TXT" ]; then
    log "installing from $REQ_TXT"
    "$PIP" install -r "$REQ_TXT" >>"$BUILD_LOG" 2>&1
    REQ_USED="requirements.txt"
elif [ -f "$PYPROJECT" ]; then
    log "installing project via pip install $UPSTREAM_DIR"
    "$PIP" install "$UPSTREAM_DIR" >>"$BUILD_LOG" 2>&1
    REQ_USED="pyproject.toml"
else
    log "no requirements.txt or pyproject.toml — skipping dependency install"
fi

log "build success"
write_status "success" "build completed" "$REQ_USED"

# 성공 종료 (trap 의 failed 분기 회피)
trap - EXIT
exit 0
