#!/usr/bin/env bash
# heax-demo-cli — job_runner entrypoint.
#
# HEAXHub job orchestrator 가 호출하는 인터페이스:
#   $1 : input dir   (사용자가 업로드한 파일들이 들어있는 디렉터리)
#   $2 : output dir  (이 스크립트가 결과 파일을 떨어뜨릴 디렉터리)
#   $3 : params.json (inputs 폼의 값이 들어있는 JSON 파일)
#
# 종료 코드 0 = 성공, 그 외 = 실패.
set -euo pipefail

INPUT_DIR="${1:-./input}"
OUTPUT_DIR="${2:-./output}"
PARAMS_JSON="${3:-./params.json}"

mkdir -p "$OUTPUT_DIR"

cd "$(dirname "$0")/.."   # repo root

python -m heax_demo_cli.cli \
  --input-dir "$INPUT_DIR" \
  --output-dir "$OUTPUT_DIR" \
  --params "$PARAMS_JSON"
