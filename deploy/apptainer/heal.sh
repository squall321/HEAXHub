#!/usr/bin/env bash
# HEAXHub 를 현재 상태와 무관하게 "정상"으로 수렴시키는 멱등 복구 스크립트.
#
# start.sh 를 죽이던 두 취약점을 없앤다:
#   (1) set -e — 개별 단계(예: 이미 도는 인스턴스 재-start) 실패에 전체가 즉사.
#       → 이 스크립트는 set -e 를 쓰지 않고, 끝에서 종합 헬스로 판정한다.
#   (2) "instance already exists" — 헬스는 죽었는데 인스턴스 레코드만 남은
#       스테일 상태에서 start.sh 가 같은 이름으로 start 하다 죽는다.
#       → 부트스트랩 전에 헬스 실패 인프라를 stop 해 레코드를 정리한다.
#
# 동작(멱등): 스테일 인프라 정리 → 파이썬 3종 종료 → start.sh 부트스트랩
#            → (안전망) 백엔드 미기동 시 직접 기동 → 종합 검증.
#
# 사용:  bash deploy/apptainer/heal.sh
set -uo pipefail  # -e 는 의도적으로 미사용

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
APPT_DIR="$ROOT_DIR/deploy/apptainer"
# shellcheck source=/dev/null
source "$APPT_DIR/_common.sh"
load_env 2>/dev/null || true
require_apptainer   # $_HEAX_APPT 확정 + apptainer() 함수 라우팅(없으면 여기서 종료)

# start.sh 와 동일한 인프라 포트(정합 유지 — start.sh 상수와 맞춤).
PG_PORT="${PG_PORT:-5732}"
REDIS_PORT="${REDIS_PORT:-6479}"
CADDY_ADMIN_PORT="${CADDY_ADMIN_PORT:-2019}"
CADDY_HTTP_PORT="${CADDY_HTTP_PORT:-4180}"
API_PORT="${API_PORT:-4040}"

# ── 헬스 프로브 ───────────────────────────────────────────────────────────────
record_exists() { apptainer instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$1"; }
pg_ok()    { apptainer exec instance://heax-pg pg_isready -h 127.0.0.1 -p "$PG_PORT" -U heaxhub >/dev/null 2>&1; }
redis_ok() { [ "$(apptainer exec instance://heax-redis redis-cli -p "$REDIS_PORT" ping 2>/dev/null)" = "PONG" ]; }
caddy_ok() { curl -sf -m 3 "http://127.0.0.1:${CADDY_ADMIN_PORT}/config/" >/dev/null 2>&1; }
backend_code() { curl -s -o /dev/null -w '%{http_code}' -m 6 "http://localhost:${API_PORT}/health" 2>/dev/null; }

# 헬스 함수를 몇 번 재시도(일시 부하로 정상 인프라를 스테일로 오판하지 않게).
healthy_retry() { local fn="$1" n="${2:-3}"; for _ in $(seq 1 "$n"); do "$fn" && return 0; sleep 1; done; return 1; }

# ── 1) 스테일 인프라 정리 ─────────────────────────────────────────────────────
# 레코드는 있는데 헬스가 실패면 stop → start.sh 가 clean 하게 재생성한다.
# 정상이면 그대로 두어(start.sh 가 skip) 불필요한 DB 재시작을 피한다.
step "1) 스테일 인프라 인스턴스 정리"
clean_stale() {  # $1=instance 이름  $2=헬스함수
  local name="$1" hf="$2"
  if record_exists "$name"; then
    if healthy_retry "$hf" 3; then
      ok "$name 정상 — 유지(start.sh 가 skip)"
    else
      warn "$name 레코드는 있으나 헬스 실패 → stop(스테일 정리)"
      apptainer instance stop "$name" >/dev/null 2>&1 || true
    fi
  else
    note "$name 미기동 — start.sh 가 올린다"
  fi
}
clean_stale heax-pg    pg_ok
clean_stale heax-redis redis_ok
clean_stale heax-caddy caddy_ok

# ── 2) 파이썬 3종 종료(새 코드로 재기동 준비) ─────────────────────────────────
# start.sh 는 이미 도는 worker/beat 를 pgrep 으로 skip 하므로, 코드 갱신을 확실히
# 반영하려면 먼저 죽여서 start.sh 가 새 프로세스로 띄우게 한다.
step "2) 백엔드/워커/비트 종료"
if pkill -f "uvicorn app.main:app.*--port ${API_PORT}" 2>/dev/null; then ok "uvicorn 종료"; else note "uvicorn 없음"; fi
if pkill -f 'celery -A app.workers.celery_app' 2>/dev/null; then ok "celery worker/beat 종료"; else note "celery 없음"; fi
sleep 2

# ── 3) 부트스트랩(start.sh) ───────────────────────────────────────────────────
# 정상 인프라는 skip, 정리된 것만 clean 기동 + 파이썬 3종 기동. 실패해도 이 스크립트는
# 죽지 않고(안전망 + 검증으로 이어짐) 로그를 남긴다.
step "3) start.sh 부트스트랩"
if bash "$APPT_DIR/start.sh"; then
  ok "start.sh 완료"
else
  err "start.sh 비정상 종료 — 검증에서 무엇이 빠졌는지 확인"
  note "pg 로그 끝줄: $(tail -n1 var/logs/postgres-start.log 2>/dev/null)"
fi

# ── 4) 안전망: 백엔드가 안 떴으면 직접 기동 ───────────────────────────────────
# start.sh 가 파이썬 섹션 전에 죽었어도 인프라만 살아있으면 여기서 백엔드를 살린다.
step "4) 백엔드 안전망"
sleep 2
if [ "$(backend_code)" = "200" ]; then
  ok "backend 이미 정상(:$API_PORT)"
else
  warn "backend 미응답 — 직접 기동 시도"
  ( set -a; source .env 2>/dev/null; set +a
    cd backend
    nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "$API_PORT" \
      > ../var/logs/backend.log 2>&1 & disown
    if ! pgrep -f 'celery -A app.workers.celery_app worker' >/dev/null; then
      nohup .venv/bin/celery -A app.workers.celery_app worker --loglevel=info --concurrency=2 \
        > ../var/logs/worker.log 2>&1 & disown
    fi
    if ! pgrep -f 'celery -A app.workers.celery_app beat' >/dev/null; then
      nohup .venv/bin/celery -A app.workers.celery_app beat --loglevel=info --schedule=../var/celerybeat-schedule \
        > ../var/logs/beat.log 2>&1 & disown
    fi )
fi

# ── 5) 종합 검증 ──────────────────────────────────────────────────────────────
step "5) 검증"
FAIL=0
healthy_retry pg_ok 3    && ok "postgres :$PG_PORT"          || { err "postgres 미응답";           FAIL=1; }
healthy_retry redis_ok 3 && ok "redis :$REDIS_PORT"          || { err "redis 미응답";              FAIL=1; }
healthy_retry caddy_ok 3 && ok "caddy admin :$CADDY_ADMIN_PORT" || { err "caddy admin 미응답";      FAIL=1; }
code=""
for _ in $(seq 1 15); do code="$(backend_code)"; [ "$code" = "200" ] && break; sleep 2; done
if [ "$code" = "200" ]; then
  ok "backend /health 200 (:$API_PORT)"
else
  err "backend /health=$code — 로그 끝 20줄:"
  tail -n 20 var/logs/backend.log 2>/dev/null >&2
  FAIL=1
fi

echo
if [ "$FAIL" = 0 ]; then
  ok "HEAXHub 정상 상태로 수렴 완료"
else
  err "일부 컴포넌트 미복구 — 위 로그를 확인하세요(대개 pg 는 커널 unprivileged userns 전제)."
fi
exit "$FAIL"
