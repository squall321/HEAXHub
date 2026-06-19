#!/usr/bin/env bash
# HEAXHub web_app (service 모드) 진입점.
#
# service 모드에서는 manifest.launch.command 가 직접 uvicorn 을 띄우므로 이 스크립트는
# 필수가 아니다. 다만 로컬에서 단독 실행하거나 비표준 부팅이 필요할 때를 위한 편의
# 진입점으로 남겨 둔다. 포탈과 동일하게 $PORT / $ROOT_PATH 를 존중한다.
#
# 핵심: 포어그라운드(exec)로 띄운다. service 모드에서 프로세스 수명 관리는 포탈이 한다
#       (예전 nohup 백그라운드 방식은 쓰지 않는다).
set -euo pipefail

PORT="${PORT:-8080}"             # 포탈이 주입; 로컬 단독 실행 시 8080 폴백
ROOT_PATH="${ROOT_PATH:-}"       # 포탈이 /apps/<slug> 주입; 로컬은 빈 값

echo "[webapp] uvicorn :$PORT root-path='${ROOT_PATH}'" >&2
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    --root-path "$ROOT_PATH" \
    --workers 1 \
    --log-level info
