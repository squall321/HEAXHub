#!/usr/bin/env bash
# 플레이스홀더 스크립트.
# windows_gui 앱은 실제 실행이 Windows Agent 측에서 일어나므로,
# 이 스크립트는 포탈이 직접 호출하지 않는다.
#
# 다만 신청·승인 자동화 파이프라인의 무결성 검사 (run.sh 존재 여부 확인) 를
# 통과시키기 위해 같은 인자 시그니처로 stub 동작을 수행한다.

set -euo pipefail

INPUT_DIR="${1:?missing input dir}"
OUTPUT_DIR="${2:?missing output dir}"
PARAMS_FILE="${3:?missing params.json}"

mkdir -p "$OUTPUT_DIR"

cat > "$OUTPUT_DIR/result.json" <<EOF
{
  "status": "failed",
  "summary": {
    "message": "windows_gui app must be dispatched via Windows Agent, not executed locally."
  },
  "warnings": [],
  "errors": [
    "Linux runner attempted to execute a windows_gui app directly. Use WindowsAgentClient."
  ],
  "outputs": {}
}
EOF

echo "[windows-gui] stub: input=$INPUT_DIR output=$OUTPUT_DIR params=$PARAMS_FILE" >&2
exit 1
