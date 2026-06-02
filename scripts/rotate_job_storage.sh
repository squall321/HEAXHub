#!/usr/bin/env bash
# rotate_job_storage.sh — 오래된 job 디렉터리를 압축 아카이브로 변환.
#
# 사용법:
#     rotate_job_storage.sh [retain_days]
#
# 동작:
#   1. JOB_STORAGE_ROOT 하위에서 mtime이 retain_days 보다 오래된
#      job_xxx 디렉터리 (job_storage/{Y}/{M}/job_xxx) 를 찾는다.
#   2. zstd 가 있으면 .tar.zst, 없으면 .tar.gz 로 압축
#   3. 압축 성공 시 원본 디렉터리 삭제
#
# 환경 변수:
#   JOB_STORAGE_ROOT - job 저장 루트 (기본 ./job_storage)

set -euo pipefail

LOG_PREFIX="[rotate]"
log()  { echo "$LOG_PREFIX $*" >&2; }
fail() { echo "$LOG_PREFIX ERROR: $*" >&2; exit 1; }

RETAIN_DAYS="${1:-90}"

if ! echo "$RETAIN_DAYS" | grep -Eq '^[0-9]+$'; then
    fail "retain_days must be a non-negative integer (got: $RETAIN_DAYS)"
fi

JOB_STORAGE_ROOT="${JOB_STORAGE_ROOT:-./job_storage}"
[ -d "$JOB_STORAGE_ROOT" ] || fail "JOB_STORAGE_ROOT not found: $JOB_STORAGE_ROOT"

# 압축 도구 결정
if command -v zstd >/dev/null 2>&1 && tar --help 2>/dev/null | grep -q -- '--zstd'; then
    USE_ZSTD=1
    EXT="tar.zst"
else
    USE_ZSTD=0
    EXT="tar.gz"
fi

log "root=$JOB_STORAGE_ROOT retain_days=$RETAIN_DAYS compressor=$EXT"

# job_storage/{YYYY}/{MM}/job_* 패턴만 처리
COUNT_ARCHIVED=0
COUNT_FAILED=0

# find -mindepth/-maxdepth 로 정확한 깊이만 매칭
while IFS= read -r -d '' job_dir; do
    # 안전 체크: 'job_' 접두사
    base=$(basename "$job_dir")
    case "$base" in
        job_*) ;;
        *) continue ;;
    esac

    parent=$(dirname "$job_dir")
    archive="$parent/${base}.${EXT}"

    if [ -e "$archive" ]; then
        log "WARN: archive already exists, skipping: $archive"
        continue
    fi

    log "archiving $job_dir → $archive"
    if [ "$USE_ZSTD" -eq 1 ]; then
        if tar --zstd -cf "$archive" -C "$parent" "$base" 2>/dev/null; then
            rm -rf "$job_dir"
            COUNT_ARCHIVED=$((COUNT_ARCHIVED + 1))
        else
            log "WARN: failed to archive $job_dir"
            rm -f "$archive"
            COUNT_FAILED=$((COUNT_FAILED + 1))
        fi
    else
        if tar -czf "$archive" -C "$parent" "$base" 2>/dev/null; then
            rm -rf "$job_dir"
            COUNT_ARCHIVED=$((COUNT_ARCHIVED + 1))
        else
            log "WARN: failed to archive $job_dir"
            rm -f "$archive"
            COUNT_FAILED=$((COUNT_FAILED + 1))
        fi
    fi
done < <(find "$JOB_STORAGE_ROOT" -mindepth 3 -maxdepth 3 -type d -name 'job_*' -mtime "+$RETAIN_DAYS" -print0)

log "done: archived=$COUNT_ARCHIVED failed=$COUNT_FAILED"

if [ "$COUNT_FAILED" -gt 0 ]; then
    exit 2
fi
exit 0
