#!/usr/bin/env bash
# 운영서버 외부 연계 서비스(IP로 뜬 백엔드+프론트)를 HEAXHub 에 proxy 로 등록/동기화한다.
# var/external-apps.yaml(운영서버 로컬, gitignore)만 읽어 그쪽 앱만 갱신한다 — 내 관리 앱은 안 건드림.
# update-all(내 흐름)과 완전히 분리된 별도 apply. 자세한 건 deploy/apptainer/README-external-apps.md.
#
#   bash deploy/apptainer/register-external.sh
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$ROOT_DIR"

# backend/.venv python 우선(yaml + 백엔드 import 로 즉시 reconcile 가능), 없으면 host python3(매니페스트만).
PY=""
for cand in "$ROOT_DIR/backend/.venv/bin/python" python3 python; do
  if command -v "$cand" >/dev/null 2>&1 || [ -x "$cand" ]; then
    if "$cand" -c 'import yaml' >/dev/null 2>&1; then PY="$cand"; break; fi
  fi
done
[ -n "$PY" ] || { echo "✗ yaml 사용 가능한 python 을 못 찾음 (backend/.venv 또는 host python3+pyyaml 필요)"; exit 1; }

# 백엔드 import(즉시 reconcile)를 위해 cwd=backend + .env 로드. 실패해도 파이썬 쪽에서 best-effort 처리.
export PYTHONPATH="$ROOT_DIR/backend${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" "$ROOT_DIR/deploy/apptainer/_external_apps.py" "$@"
