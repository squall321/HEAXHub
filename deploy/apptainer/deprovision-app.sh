#!/usr/bin/env bash
# 앱 하나를 카탈로그·Caddy·런타임 state 에서 완전히 지운다 (Caddy 라우트 + state + DB 행).
# 스캐너는 App 행을 절대 삭제하지 않으므로(upsert-only), 매니페스트 디렉터리 삭제만으로는
# 카탈로그에서 안 사라진다 — 이 스크립트가 그 다음 단계.
#
# 순서: 1) integrations/<id>/ (또는 ext_<id>/) 를 먼저 git rm 등으로 지운다.
#       2) 이 스크립트로 라우트/state/DB 행을 정리한다.
#
#   bash deploy/apptainer/deprovision-app.sh <app_id>
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$ROOT_DIR"

[ $# -eq 1 ] || { echo "사용법: bash deploy/apptainer/deprovision-app.sh <app_id>"; exit 1; }

PY=""
for cand in "$ROOT_DIR/backend/.venv/bin/python" python3 python; do
  if command -v "$cand" >/dev/null 2>&1 || [ -x "$cand" ]; then
    if "$cand" -c 'import app.db.session' >/dev/null 2>&1; then PY="$cand"; break; fi
  fi
done
[ -n "$PY" ] || {
  echo "✗ 백엔드 import 가능한 python 을 못 찾음 (backend/.venv 필요 — 이 스크립트는 DB 접근이 필수)"
  exit 1
}

export PYTHONPATH="$ROOT_DIR/backend${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" "$ROOT_DIR/deploy/apptainer/_deprovision_app.py" "$1"
