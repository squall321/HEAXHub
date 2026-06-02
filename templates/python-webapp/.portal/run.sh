#!/usr/bin/env bash
# HEAXHub web_app 진입점.
# uvicorn 을 백그라운드로 띄우고, 접속 가능한 URL 을 result.json 에 기록한 뒤
# 즉시 종료한다. 실제 서비스 프로세스는 백그라운드에 남아 동작한다.

set -euo pipefail

INPUT_DIR="${1:?missing input dir}"
OUTPUT_DIR="${2:?missing output dir}"
PARAMS_FILE="${3:?missing params.json}"

mkdir -p "$OUTPUT_DIR"

# 운영자가 manifest 또는 환경변수로 포트를 고정할 수 있다.
PORT="${WEBAPP_PORT:-8080}"
HOST="${WEBAPP_HOST:-0.0.0.0}"
PUBLIC_BASE="${WEBAPP_PUBLIC_BASE:-http://localhost:${PORT}}"

LOG_FILE="$OUTPUT_DIR/uvicorn.log"

echo "[webapp] starting uvicorn host=${HOST} port=${PORT}" >&2

nohup uvicorn app.main:app \
    --host "$HOST" \
    --port "$PORT" \
    --workers 1 \
    --log-level info \
    >"$LOG_FILE" 2>&1 &
PID=$!

# pid 기록 (운영자가 stop 스크립트 작성 시 사용)
echo "$PID" > "$OUTPUT_DIR/uvicorn.pid"

# 간단 헬스체크 대기 (최대 10초)
for _ in $(seq 1 20); do
    if curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

cat > "$OUTPUT_DIR/result.json" <<EOF
{
  "status": "success",
  "summary": {
    "service": "my_python_webapp",
    "host": "${HOST}",
    "port": ${PORT},
    "pid": ${PID}
  },
  "outputs": {
    "url": "${PUBLIC_BASE}/",
    "health": "${PUBLIC_BASE}/health",
    "log": "uvicorn.log"
  },
  "warnings": [],
  "errors": []
}
EOF

echo "[webapp] running at ${PUBLIC_BASE}/ (pid=${PID})" >&2
