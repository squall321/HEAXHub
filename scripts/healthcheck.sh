#!/usr/bin/env bash
# healthcheck.sh — 시스템 헬스 체크. JSON으로 stdout 에 출력.
#
# 사용법:
#     healthcheck.sh
#
# 환경 변수:
#   DATABASE_URL       - PostgreSQL DSN (psql 또는 pg_isready 사용)
#   REDIS_URL          - Redis URL (redis-cli 사용)
#   JOB_STORAGE_ROOT   - 디스크 사용량 체크 대상
#   WORKSPACE_ROOT     - 디스크 사용량 체크 대상 (선택)
#   DISK_WARN_PERCENT  - 디스크 사용률 경고 임계값 (기본 85)
#
# 종료 코드:
#   0  모두 정상
#   1  하나 이상의 컴포넌트 비정상

set -euo pipefail

LOG_PREFIX="[healthcheck]"
warn() { echo "$LOG_PREFIX WARN: $*" >&2; }

DISK_WARN_PERCENT="${DISK_WARN_PERCENT:-85}"

# ---- postgres ----
pg_status="unknown"
pg_message=""
if [ -n "${DATABASE_URL:-}" ]; then
    # postgresql+driver://... → postgresql://...  변환 (psql/pg_isready 가 driver 접미사 인식 못 함)
    DSN=$(echo "$DATABASE_URL" | sed -E 's#^postgresql\+[^:]+://#postgresql://#')
    if command -v pg_isready >/dev/null 2>&1; then
        if pg_isready -d "$DSN" -t 3 >/dev/null 2>&1; then
            pg_status="ok"
        else
            pg_status="fail"
            pg_message="pg_isready returned non-zero"
        fi
    elif command -v psql >/dev/null 2>&1; then
        if psql "$DSN" -tAc 'SELECT 1' >/dev/null 2>&1; then
            pg_status="ok"
        else
            pg_status="fail"
            pg_message="psql connect failed"
        fi
    else
        pg_status="skipped"
        pg_message="neither pg_isready nor psql installed"
    fi
else
    pg_status="skipped"
    pg_message="DATABASE_URL not set"
fi

# ---- redis ----
redis_status="unknown"
redis_message=""
if [ -n "${REDIS_URL:-}" ]; then
    if command -v redis-cli >/dev/null 2>&1; then
        if redis-cli -u "$REDIS_URL" PING 2>/dev/null | grep -q PONG; then
            redis_status="ok"
        else
            redis_status="fail"
            redis_message="PING did not return PONG"
        fi
    else
        redis_status="skipped"
        redis_message="redis-cli not installed"
    fi
else
    redis_status="skipped"
    redis_message="REDIS_URL not set"
fi

# ---- disk ----
# JOB_STORAGE_ROOT 와 WORKSPACE_ROOT 각각의 mount 디스크 사용률을 확인
disk_entries="[]"
disk_overall="ok"

build_disk_entry() {
    local label="$1" path="$2"
    [ -e "$path" ] || { echo ""; return; }
    local line use_pct avail used total
    line=$(df -P "$path" 2>/dev/null | tail -1)
    [ -z "$line" ] && { echo ""; return; }
    use_pct=$(echo "$line" | awk '{print $5}' | tr -d '%')
    avail=$(echo "$line" | awk '{print $4}')
    used=$(echo "$line" | awk '{print $3}')
    total=$(echo "$line" | awk '{print $2}')
    local status="ok"
    if [ "$use_pct" -ge "$DISK_WARN_PERCENT" ]; then
        status="warn"
        disk_overall="warn"
    fi
    echo "{\"label\":\"$label\",\"path\":\"$path\",\"used_percent\":$use_pct,\"used_kb\":$used,\"avail_kb\":$avail,\"total_kb\":$total,\"status\":\"$status\"}"
}

entries=()
for pair in \
    "job_storage|${JOB_STORAGE_ROOT:-}" \
    "workspaces|${WORKSPACE_ROOT:-}"
do
    label="${pair%%|*}"
    path="${pair#*|}"
    [ -z "$path" ] && continue
    e=$(build_disk_entry "$label" "$path")
    [ -n "$e" ] && entries+=("$e")
done

if [ "${#entries[@]}" -gt 0 ]; then
    disk_entries="[$(IFS=,; echo "${entries[*]}")]"
fi

# ---- overall status ----
overall="ok"
case "$pg_status" in fail) overall="fail" ;; esac
case "$redis_status" in fail) overall="fail" ;; esac
[ "$disk_overall" = "warn" ] && [ "$overall" = "ok" ] && overall="warn"

now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat <<EOF
{
  "checked_at": "$now",
  "overall": "$overall",
  "components": {
    "postgres": { "status": "$pg_status", "message": "$pg_message" },
    "redis":    { "status": "$redis_status", "message": "$redis_message" },
    "disk":     { "status": "$disk_overall", "entries": $disk_entries, "warn_percent": $DISK_WARN_PERCENT }
  }
}
EOF

[ "$overall" = "fail" ] && exit 1
exit 0
