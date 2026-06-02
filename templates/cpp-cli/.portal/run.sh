#!/usr/bin/env bash
# HEAXHub cpp-cli 진입점.
# 인자: $1 입력 디렉터리, $2 출력 디렉터리, $3 params.json
#
# 환경변수:
#   APP_SIF       - 실행할 SIF 이미지 경로 (HEAXHub 의 ApptainerRunner 가 주입).
#                   비어 있으면 ../sif/app.sif 또는 ./app.sif 를 탐색한다.
#   APPTAINER_BIN - apptainer 바이너리 (기본 'apptainer')

set -euo pipefail

INPUT_DIR="${1:?missing input dir}"
OUTPUT_DIR="${2:?missing output dir}"
PARAMS_FILE="${3:?missing params.json}"

mkdir -p "$OUTPUT_DIR"

APPTAINER="${APPTAINER_BIN:-apptainer}"

# SIF 경로 결정
SIF="${APP_SIF:-}"
if [ -z "$SIF" ]; then
    if [ -f "../sif/app.sif" ]; then SIF="../sif/app.sif";
    elif [ -f "./app.sif" ];     then SIF="./app.sif";
    fi
fi

if [ -z "$SIF" ] || [ ! -f "$SIF" ]; then
    echo "[cpp-cli] error: SIF image not found. Set APP_SIF or build app.sif first." >&2
    exit 3
fi

# params.json 에서 추가 인자 추출 (jq 가 있으면)
EXTRA_ARGS=()
if command -v jq >/dev/null 2>&1 && [ -f "$PARAMS_FILE" ]; then
    while IFS= read -r line; do
        [ -n "$line" ] && EXTRA_ARGS+=("$line")
    done < <(jq -r '.args[]? // empty' "$PARAMS_FILE")
fi

echo "[cpp-cli] exec apptainer image=$SIF" >&2

exec "$APPTAINER" exec \
    --cleanenv \
    --bind "$INPUT_DIR:/job/input:ro" \
    --bind "$OUTPUT_DIR:/job/output" \
    --bind "$PARAMS_FILE:/job/params.json:ro" \
    --env "JOB_INPUT=/job/input" \
    --env "JOB_OUTPUT=/job/output" \
    --env "JOB_PARAMS=/job/params.json" \
    "$SIF" \
    mytool "${EXTRA_ARGS[@]}"
