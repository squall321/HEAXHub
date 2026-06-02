#!/usr/bin/env bash
# HEAXHub watchdog — 매분 실행. 죽은 컴포넌트만 자동 복구.
#
# 검사 대상:
#   1. Apptainer instances: heax-pg / heax-redis / heax-mailhog / heax-caddy
#   2. 호스트 포트 listen: 5732 / 6479 / 8125 / 8126 / 4180 / 4040 / 4173
#   3. HTTP /health endpoint
#   4. Postgres TCP 응답 (pg_isready)
#
# 복구 전략:
#   - 인스턴스 자체가 죽었다 → deploy/apptainer/start.sh (멱등)
#   - 인스턴스는 있는데 내부 데몬 죽었다 → 해당 데몬만 재기동
#   - 백엔드/워커/프론트만 죽었다 → 그 프로세스만 재기동
#
# 로그는 stderr/stdout 으로 — systemd 가 journal 에 기록.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PG_PORT=5732
REDIS_PORT=6479
SMTP_PORT=8125
MAIL_UI_PORT=8126
CADDY_HTTP_PORT=4180
API_PORT=4040
WEB_PORT=4173

TS="$(date '+%Y-%m-%d %H:%M:%S')"
recovered=()

instance_running() {
  apptainer instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$1"
}

port_listening() {
  # 한 번 더 retry — ss는 가끔 첫 호출에서 stale일 수 있음.
  if ss -tln 2>/dev/null | grep -q ":$1 "; then return 0; fi
  sleep 0.5
  ss -tln 2>/dev/null | grep -q ":$1 "
}

http_ok() {
  curl -sf --max-time 3 "$1" > /dev/null 2>&1
}

# ── 1. Apptainer 인스턴스 4종 ─────────────────────────────────
need_full_start=0
for inst in heax-pg heax-redis heax-mailhog heax-caddy; do
  if ! instance_running "$inst"; then
    echo "[$TS] WARN: $inst instance missing"
    need_full_start=1
  fi
done
if [ "$need_full_start" = "1" ]; then
  echo "[$TS] → run start.sh (idempotent)"
  bash deploy/apptainer/start.sh > var/logs/watchdog-start.log 2>&1 || true
  recovered+=("apptainer-instances")
  sleep 5
fi

# ── 2. 인스턴스는 있는데 내부 데몬이 죽은 경우 ────────────────
# Postgres
if instance_running heax-pg && ! port_listening "$PG_PORT"; then
  echo "[$TS] WARN: pg instance up but postgres not listening on $PG_PORT — restarting daemon"
  apptainer exec instance://heax-pg sh -c "
    pkill -9 postgres 2>/dev/null || true
    sleep 1
    setsid nohup postgres -D /var/lib/postgresql/data/pgdata -p $PG_PORT -h 0.0.0.0 > /tmp/postgres.log 2>&1 < /dev/null &
  " > /dev/null 2>&1
  sleep 2
  recovered+=("postgres")
fi
# Redis
if instance_running heax-redis && ! port_listening "$REDIS_PORT"; then
  echo "[$TS] WARN: redis instance up but not listening on $REDIS_PORT — restarting"
  apptainer exec instance://heax-redis sh -c \
    "redis-server --bind 0.0.0.0 --port $REDIS_PORT --dir /data --daemonize yes" 2>&1 | head -3
  recovered+=("redis")
fi
# MailHog
if instance_running heax-mailhog && (! port_listening "$SMTP_PORT" || ! port_listening "$MAIL_UI_PORT"); then
  echo "[$TS] WARN: mailhog ports missing — restarting"
  apptainer exec instance://heax-mailhog sh -c \
    "pkill -9 MailHog 2>/dev/null; setsid nohup MailHog -smtp-bind-addr 0.0.0.0:$SMTP_PORT -ui-bind-addr 0.0.0.0:$MAIL_UI_PORT -api-bind-addr 0.0.0.0:$MAIL_UI_PORT > /tmp/mailhog.log 2>&1 < /dev/null &" \
    > /dev/null 2>&1
  sleep 1
  recovered+=("mailhog")
fi
# Caddy
if instance_running heax-caddy && ! port_listening "$CADDY_HTTP_PORT"; then
  echo "[$TS] WARN: caddy instance up but not listening on $CADDY_HTTP_PORT — restarting"
  apptainer exec instance://heax-caddy sh -c \
    "pkill -9 caddy 2>/dev/null; setsid nohup caddy run --config /etc/caddy/bootstrap.json > /tmp/caddy.log 2>&1 < /dev/null &" \
    > /dev/null 2>&1
  sleep 1
  recovered+=("caddy")
fi

# ── 3. Backend / Worker / Beat / Frontend ─────────────────────
# Backend
if ! http_ok "http://localhost:$API_PORT/health"; then
  echo "[$TS] WARN: backend health failed — restarting uvicorn"
  pkill -f "uvicorn app.main:app.*--port $API_PORT" 2>/dev/null || true
  sleep 1
  nohup bash -c 'set -a; source .env; set +a; cd backend && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port '"$API_PORT" \
    > var/logs/backend.log 2>&1 &
  disown
  recovered+=("backend")
fi
# Celery worker
if ! pgrep -f "celery -A app.workers.celery_app worker" >/dev/null; then
  echo "[$TS] WARN: celery worker missing — restarting"
  nohup bash -c 'set -a; source .env; set +a; cd backend && .venv/bin/celery -A app.workers.celery_app worker --loglevel=info --concurrency=2' \
    > var/logs/worker.log 2>&1 &
  disown
  recovered+=("worker")
fi
# Celery beat
if ! pgrep -f "celery -A app.workers.celery_app beat" >/dev/null; then
  echo "[$TS] WARN: celery beat missing — restarting"
  nohup bash -c 'set -a; source .env; set +a; cd backend && .venv/bin/celery -A app.workers.celery_app beat --loglevel=info' \
    > var/logs/beat.log 2>&1 &
  disown
  recovered+=("beat")
fi
# Frontend는 Caddy(:CADDY_HTTP_PORT)가 frontend/dist를 SPA 서빙합니다.
# Vite dev 서버는 자동복구 대상이 아닙니다 (필요 시 운영자가 make frontend).
# Caddy 라우트가 살아 있고 index.html을 반환하는지만 검증.
if ! curl -fsS "http://localhost:$CADDY_HTTP_PORT/" -o /dev/null 2>&1; then
  echo "[$TS] WARN: Caddy SPA root not responding on :$CADDY_HTTP_PORT"
  recovered+=("caddy-spa")
fi

# ── 4. 요약 ───────────────────────────────────────────────────
if [ "${#recovered[@]}" -gt 0 ]; then
  echo "[$TS] recovered: ${recovered[*]}"
else
  # 정상 — verbose 모드일 때만 한 줄
  if [ "${HEAXHUB_WATCHDOG_VERBOSE:-0}" = "1" ]; then
    echo "[$TS] OK — all components healthy"
  fi
fi
exit 0
