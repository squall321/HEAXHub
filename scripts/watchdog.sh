#!/usr/bin/env bash
# HEAXHub watchdog — 매분 실행. 죽은 컴포넌트만 자동 복구.
#
# 검사 대상:
#   1. Apptainer instances: heax-pg / heax-redis / heax-mailhog / heax-caddy
#   2. 데몬 헬스: pg_isready / redis-cli ping / 포트 listen / HTTP /health
#   3. Backend / Worker / Beat
#
# 복구 전략:
#   - 인스턴스 자체가 죽었다 → deploy/apptainer/start.sh (멱등)
#   - 인스턴스는 있는데 내부 데몬 죽었다 → 해당 데몬만 graceful 재기동
#   - 백엔드/워커/비트만 죽었다 → 그 프로세스만 재기동
#
# 오탐 방지:
#   - PATH 를 명시 export + 모든 외부 바이너리는 절대경로로 resolve.
#     (systemd user 서비스의 최소 PATH 때문에 ss/apptainer 가 사라져
#      "살아있는데 죽었다"고 오판 → 매분 pkill 하던 버그를 차단)
#   - "죽었다" 1차 판정 후 즉시 복구하지 않고 N회 재검증(retry+sleep).
#   - 복구 후 성공 재검증 + 연속 복구 시 지수 백오프(불필요한 churn 억제).
#   - 로그는 크기 상한 초과 시 최근 N줄만 유지.
#
# 사용:
#   watchdog.sh            # 평소 실행 (복구 수행)
#   watchdog.sh --dry-run  # 진단만 — 절대 mutate 하지 않음, would-recover 출력
#
# 로그는 stderr/stdout 으로 — systemd 가 append 로 파일에 기록.
set -uo pipefail

# ── PATH 고정: systemd user 서비스의 빈/최소 PATH 가 오탐의 근본 원인이었음 ──
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ── 외부 바이너리를 절대경로로 resolve (없으면 빈 문자열) ──────────
_resolve() {
  local name="$1"; shift
  local p
  p="$(command -v "$name" 2>/dev/null || true)"
  if [ -n "$p" ]; then printf '%s' "$p"; return 0; fi
  for p in "$@"; do
    if [ -x "$p" ]; then printf '%s' "$p"; return 0; fi
  done
  printf ''
}
SS="$(_resolve ss /usr/sbin/ss /usr/bin/ss /sbin/ss /bin/ss)"
CURL="$(_resolve curl /usr/bin/curl /bin/curl)"
APPTAINER="$(_resolve apptainer /usr/local/bin/apptainer /usr/bin/apptainer)"
PG_ISREADY="$(_resolve pg_isready /usr/bin/pg_isready /usr/local/bin/pg_isready)"
REDIS_CLI="$(_resolve redis-cli /usr/bin/redis-cli /usr/local/bin/redis-cli)"

PG_PORT=5732
REDIS_PORT=6479
SMTP_PORT=8125
MAIL_UI_PORT=8126
CADDY_HTTP_PORT=4180
API_PORT=4040
WEB_PORT=4173

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

TS="$(date '+%Y-%m-%d %H:%M:%S')"
recovered=()

# ── 백오프 상태 디렉터리 ──────────────────────────────────────────
STATE_DIR="$ROOT/var/watchdog_state"
[ "$DRY_RUN" = "0" ] && mkdir -p "$STATE_DIR" 2>/dev/null || true
RETRIES=3            # "죽었다" 재검증 횟수
RETRY_SLEEP=2        # 재검증 간 sleep(초)
BACKOFF_BASE=60      # 백오프 기준(초): 연속 복구 1회=60s, 2회=120s, 3회=240s ... 상한 1h

log() { echo "[$TS] $*"; }

# 절대경로 바이너리가 진짜 있는지 한 번 점검 — 없으면 헬스체크 자체를 신뢰 불가로 보고
# 복구를 보류(오탐 방지의 마지막 안전장치).
TOOLS_OK=1
if [ -z "$SS" ] || [ -z "$APPTAINER" ]; then
  log "ERROR: required tool missing (ss='$SS' apptainer='$APPTAINER') — skipping recovery to avoid false positives"
  TOOLS_OK=0
fi

# ── 헬스 프로브 (모두 절대경로 사용) ──────────────────────────────
instance_running() {
  [ -n "$APPTAINER" ] || return 1
  "$APPTAINER" instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$1"
}

port_listening() {
  [ -n "$SS" ] || return 1
  "$SS" -tln 2>/dev/null | grep -q ":$1 "
}

pg_healthy() {
  if [ -n "$PG_ISREADY" ]; then
    "$PG_ISREADY" -h 127.0.0.1 -p "$PG_PORT" -t 2 >/dev/null 2>&1 && return 0
    return 1
  fi
  port_listening "$PG_PORT"   # pg_isready 없으면 포트로 폴백
}

redis_healthy() {
  if [ -n "$REDIS_CLI" ]; then
    [ "$("$REDIS_CLI" -h 127.0.0.1 -p "$REDIS_PORT" ping 2>/dev/null)" = "PONG" ] && return 0
    return 1
  fi
  port_listening "$REDIS_PORT"
}

http_ok() {
  [ -n "$CURL" ] || return 1
  "$CURL" -sf --max-time 3 "$1" >/dev/null 2>&1
}

# confirm_down <probe-fn> [args...] : 1차 실패 후 RETRIES 회 재검증.
# 한 번이라도 살아나면 0(=up) 리턴. 끝까지 죽어있으면 1(=down).
confirm_down() {
  local i
  for ((i=1; i<=RETRIES; i++)); do
    if "$@"; then return 1; fi   # up 으로 회복됨 → not down
    [ "$i" -lt "$RETRIES" ] && sleep "$RETRY_SLEEP"
  done
  return 0  # RETRIES 내내 down
}

# ── 백오프: 직전 복구로부터 충분히 지났을 때만 복구 허용 ──────────
# 반환: 0 = 복구 진행 가능, 1 = 백오프 윈도우 내 → skip
backoff_gate() {
  local key="$1"
  local now countf tsf count last window
  now="$(date +%s)"
  countf="$STATE_DIR/${key}.count"
  tsf="$STATE_DIR/${key}.last"
  count="$(cat "$countf" 2>/dev/null || echo 0)"
  last="$(cat "$tsf" 2>/dev/null || echo 0)"
  case "$count" in (*[!0-9]*|'') count=0;; esac
  case "$last"  in (*[!0-9]*|'') last=0;;  esac
  # window = BACKOFF_BASE * 2^(count-1), 상한 3600s
  if [ "$count" -le 0 ]; then
    window=0
  else
    window=$(( BACKOFF_BASE << (count-1) ))
    [ "$window" -gt 3600 ] && window=3600
  fi
  if [ "$count" -gt 0 ] && [ $((now - last)) -lt "$window" ]; then
    log "  backoff: '$key' recovered ${count}x; ${window}s window not elapsed ($((now-last))s) — skip"
    return 1
  fi
  return 0
}

# 복구 시도를 기록(연속 카운트 증가 + 타임스탬프).
backoff_mark() {
  local key="$1" now count countf tsf
  now="$(date +%s)"
  countf="$STATE_DIR/${key}.count"; tsf="$STATE_DIR/${key}.last"
  count="$(cat "$countf" 2>/dev/null || echo 0)"; case "$count" in (*[!0-9]*|'') count=0;; esac
  echo $((count+1)) > "$countf" 2>/dev/null || true
  echo "$now" > "$tsf" 2>/dev/null || true
}

# 복구 성공(헬스 회복) → 연속 카운트 리셋.
backoff_reset() {
  rm -f "$STATE_DIR/$1.count" "$STATE_DIR/$1.last" 2>/dev/null || true
}

# 정상일 때마다 호출 — 복구 안 했으면 카운트 리셋(다음 진짜 장애 시 백오프 0부터).
healthy_reset() { backoff_reset "$1"; }

# ──────────────────────────────────────────────────────────────────
# DRY-RUN 진단 경로: 절대 mutate 안 함. would-recover 만 집계.
# ──────────────────────────────────────────────────────────────────
if [ "$DRY_RUN" = "1" ]; then
  log "DRY-RUN — probe only, no recovery"
  log "  tools: ss='$SS' apptainer='$APPTAINER' pg_isready='$PG_ISREADY' redis-cli='$REDIS_CLI' curl='$CURL'"
  would=()
  for inst in heax-pg heax-redis heax-mailhog heax-caddy; do
    instance_running "$inst" || would+=("instance:$inst")
  done
  pg_healthy            || would+=("postgres")
  redis_healthy         || would+=("redis")
  port_listening "$SMTP_PORT"    || would+=("mailhog-smtp")
  port_listening "$MAIL_UI_PORT" || would+=("mailhog-ui")
  port_listening "$CADDY_HTTP_PORT" || would+=("caddy")
  http_ok "http://localhost:$API_PORT/health" || would+=("backend")
  pgrep -f "celery -A app.workers.celery_app worker" >/dev/null || would+=("worker")
  pgrep -f "celery -A app.workers.celery_app beat"   >/dev/null || would+=("beat")
  if [ "${#would[@]}" -gt 0 ]; then
    log "DRY-RUN would recover: ${would[*]}"
  else
    log "DRY-RUN OK — all components healthy (recovered 0)"
  fi
  exit 0
fi

# 도구가 없으면 복구 자체를 보류(오탐 방지). 로그만 남기고 종료.
if [ "$TOOLS_OK" = "0" ]; then
  exit 0
fi

# ── 1. Apptainer 인스턴스 4종 ─────────────────────────────────────
need_full_start=0
for inst in heax-pg heax-redis heax-mailhog heax-caddy; do
  if confirm_down instance_running "$inst"; then
    log "WARN: $inst instance missing (confirmed)"
    need_full_start=1
  fi
done
if [ "$need_full_start" = "1" ]; then
  if backoff_gate "apptainer-instances"; then
    log "→ run start.sh (idempotent)"
    bash deploy/apptainer/start.sh > var/logs/watchdog-start.log 2>&1 || true
    backoff_mark "apptainer-instances"
    sleep 5
    # 성공 검증
    ok=1
    for inst in heax-pg heax-redis heax-mailhog heax-caddy; do
      instance_running "$inst" || ok=0
    done
    if [ "$ok" = "1" ]; then
      log "  verify: instances back up"; backoff_reset "apptainer-instances"
    else
      log "  verify: instances STILL missing after start.sh"
    fi
    recovered+=("apptainer-instances")
  fi
else
  healthy_reset "apptainer-instances"
fi

# ── 2. 인스턴스는 있는데 내부 데몬이 죽은 경우 ────────────────────
# Postgres — pg_isready 로 실제 응답 확인, graceful 재기동.
if instance_running heax-pg; then
  if confirm_down pg_healthy; then
    if backoff_gate "postgres"; then
      log "WARN: pg up but not accepting connections on $PG_PORT (confirmed) — restarting daemon (graceful)"
      "$APPTAINER" exec instance://heax-pg sh -c "
        DATA=/var/lib/postgresql/data/pgdata
        if command -v pg_ctl >/dev/null 2>&1; then
          pg_ctl -D \"\$DATA\" -m fast -w -t 20 stop 2>/dev/null || pkill -TERM postgres 2>/dev/null || true
        else
          pkill -TERM postgres 2>/dev/null || true
        fi
        # graceful 종료 대기(최대 ~10s)
        for i in 1 2 3 4 5 6 7 8 9 10; do pgrep -x postgres >/dev/null 2>&1 || break; sleep 1; done
        pkill -KILL postgres 2>/dev/null || true
        sleep 1
        setsid nohup postgres -D \"\$DATA\" -p $PG_PORT -h 0.0.0.0 > /tmp/postgres.log 2>&1 < /dev/null &
      " > /dev/null 2>&1
      backoff_mark "postgres"
      sleep 3
      if pg_healthy; then
        log "  verify: postgres accepting connections"; backoff_reset "postgres"
      else
        log "  verify: postgres STILL down after restart"
      fi
      recovered+=("postgres")
    fi
  else
    healthy_reset "postgres"
  fi
fi

# Redis — redis-cli ping 으로 확인.
if instance_running heax-redis; then
  if confirm_down redis_healthy; then
    if backoff_gate "redis"; then
      log "WARN: redis up but not answering PING on $REDIS_PORT (confirmed) — restarting"
      "$APPTAINER" exec instance://heax-redis sh -c \
        "redis-cli -p $REDIS_PORT shutdown nosave 2>/dev/null || pkill -TERM redis-server 2>/dev/null || true; sleep 1; redis-server --bind 0.0.0.0 --port $REDIS_PORT --dir /data --daemonize yes" \
        > /dev/null 2>&1
      backoff_mark "redis"
      sleep 2
      if redis_healthy; then
        log "  verify: redis answering PING"; backoff_reset "redis"
      else
        log "  verify: redis STILL down after restart"
      fi
      recovered+=("redis")
    fi
  else
    healthy_reset "redis"
  fi
fi

# MailHog
if instance_running heax-mailhog; then
  if confirm_down sh -c '
      '"$SS"' -tln 2>/dev/null | grep -q ":'"$SMTP_PORT"' " && '"$SS"' -tln 2>/dev/null | grep -q ":'"$MAIL_UI_PORT"' "'; then
    if backoff_gate "mailhog"; then
      log "WARN: mailhog ports missing (confirmed) — restarting"
      "$APPTAINER" exec instance://heax-mailhog sh -c \
        "pkill -TERM MailHog 2>/dev/null; sleep 1; pkill -KILL MailHog 2>/dev/null; setsid nohup MailHog -smtp-bind-addr 0.0.0.0:$SMTP_PORT -ui-bind-addr 0.0.0.0:$MAIL_UI_PORT -api-bind-addr 0.0.0.0:$MAIL_UI_PORT > /tmp/mailhog.log 2>&1 < /dev/null &" \
        > /dev/null 2>&1
      backoff_mark "mailhog"
      sleep 2
      if port_listening "$SMTP_PORT" && port_listening "$MAIL_UI_PORT"; then
        log "  verify: mailhog ports back"; backoff_reset "mailhog"
      else
        log "  verify: mailhog STILL down after restart"
      fi
      recovered+=("mailhog")
    fi
  else
    healthy_reset "mailhog"
  fi
fi

# Caddy
if instance_running heax-caddy; then
  if confirm_down port_listening "$CADDY_HTTP_PORT"; then
    if backoff_gate "caddy"; then
      log "WARN: caddy up but not listening on $CADDY_HTTP_PORT (confirmed) — restarting"
      "$APPTAINER" exec instance://heax-caddy sh -c \
        "pkill -TERM caddy 2>/dev/null; sleep 1; pkill -KILL caddy 2>/dev/null; setsid nohup caddy run --config /etc/caddy/bootstrap.json > /tmp/caddy.log 2>&1 < /dev/null &" \
        > /dev/null 2>&1
      backoff_mark "caddy"
      sleep 2
      if port_listening "$CADDY_HTTP_PORT"; then
        log "  verify: caddy listening"; backoff_reset "caddy"
      else
        log "  verify: caddy STILL down after restart"
      fi
      recovered+=("caddy")
    fi
  else
    healthy_reset "caddy"
  fi
fi

# ── 3. Backend / Worker / Beat ────────────────────────────────────
# Backend
if confirm_down http_ok "http://localhost:$API_PORT/health"; then
  if backoff_gate "backend"; then
    log "WARN: backend health failed (confirmed) — restarting uvicorn"
    pkill -f "uvicorn app.main:app.*--port $API_PORT" 2>/dev/null || true
    sleep 1
    nohup bash -c 'set -a; source .env; set +a; cd backend && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port '"$API_PORT" \
      > var/logs/backend.log 2>&1 &
    disown
    backoff_mark "backend"
    sleep 3
    if http_ok "http://localhost:$API_PORT/health"; then
      log "  verify: backend healthy"; backoff_reset "backend"
    else
      log "  verify: backend STILL down after restart"
    fi
    recovered+=("backend")
  fi
else
  healthy_reset "backend"
fi
# Celery worker
if confirm_down sh -c 'pgrep -f "celery -A app.workers.celery_app worker" >/dev/null'; then
  if backoff_gate "worker"; then
    log "WARN: celery worker missing (confirmed) — restarting"
    nohup bash -c 'set -a; source .env; set +a; cd backend && .venv/bin/celery -A app.workers.celery_app worker --loglevel=info --concurrency=2' \
      > var/logs/worker.log 2>&1 &
    disown
    backoff_mark "worker"
    sleep 2
    if pgrep -f "celery -A app.workers.celery_app worker" >/dev/null; then
      log "  verify: worker running"; backoff_reset "worker"
    else
      log "  verify: worker STILL missing after restart"
    fi
    recovered+=("worker")
  fi
else
  healthy_reset "worker"
fi
# Celery beat
if confirm_down sh -c 'pgrep -f "celery -A app.workers.celery_app beat" >/dev/null'; then
  if backoff_gate "beat"; then
    log "WARN: celery beat missing (confirmed) — restarting"
    nohup bash -c 'set -a; source .env; set +a; cd backend && .venv/bin/celery -A app.workers.celery_app beat --loglevel=info' \
      > var/logs/beat.log 2>&1 &
    disown
    backoff_mark "beat"
    sleep 2
    if pgrep -f "celery -A app.workers.celery_app beat" >/dev/null; then
      log "  verify: beat running"; backoff_reset "beat"
    else
      log "  verify: beat STILL missing after restart"
    fi
    recovered+=("beat")
  fi
else
  healthy_reset "beat"
fi
# Frontend는 Caddy(:CADDY_HTTP_PORT)가 frontend/dist를 SPA 서빙합니다.
# Vite dev 서버는 자동복구 대상이 아닙니다 (필요 시 운영자가 make frontend).
# Caddy 라우트가 살아 있고 index.html을 반환하는지만 검증.
if [ -n "$CURL" ] && ! "$CURL" -fsS "http://localhost:$CADDY_HTTP_PORT/" -o /dev/null 2>&1; then
  log "WARN: Caddy SPA root not responding on :$CADDY_HTTP_PORT"
  recovered+=("caddy-spa")
fi

# ── 4. 요약 ───────────────────────────────────────────────────────
if [ "${#recovered[@]}" -gt 0 ]; then
  log "recovered: ${recovered[*]}"
else
  if [ "${HEAXHUB_WATCHDOG_VERBOSE:-0}" = "1" ]; then
    log "OK — all components healthy"
  fi
fi

# ── 5. 로그 회전 — watchdog.log 가 커지면 최근 N줄만 유지 ─────────
LOGFILE="$ROOT/var/logs/watchdog.log"
MAX_BYTES=$((1024*1024))   # 1MB
KEEP_LINES=2000
if [ -f "$LOGFILE" ]; then
  sz=$(wc -c < "$LOGFILE" 2>/dev/null || echo 0)
  if [ "$sz" -gt "$MAX_BYTES" ]; then
    tmp="$LOGFILE.rot.$$"
    if tail -n "$KEEP_LINES" "$LOGFILE" > "$tmp" 2>/dev/null; then
      mv "$tmp" "$LOGFILE" 2>/dev/null || rm -f "$tmp" 2>/dev/null
      log "log rotated: kept last $KEEP_LINES lines (was ${sz}B)"
    else
      rm -f "$tmp" 2>/dev/null || true
    fi
  fi
fi

exit 0
