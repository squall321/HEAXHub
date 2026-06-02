#!/usr/bin/env bash
# HEAXHub LocalRunner 표준 진입점.
# 인자: $1 입력 디렉터리, $2 출력 디렉터리, $3 params.json
# venv는 LocalRunner가 PATH에 미리 주입한다.

set -euo pipefail

INPUT_DIR="${1:?missing input dir}"
OUTPUT_DIR="${2:?missing output dir}"
PARAMS_FILE="${3:?missing params.json}"

mkdir -p "$OUTPUT_DIR"

exec python -m mytool.main \
    --input  "$INPUT_DIR" \
    --output "$OUTPUT_DIR" \
    --params "$PARAMS_FILE"
