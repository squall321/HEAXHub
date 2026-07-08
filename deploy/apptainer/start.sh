#!/usr/bin/env bash
# Start HEAXHub local dev stack with Apptainer instances + backend/worker/frontend.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Rootless apptainer derives the cgroup/instance owner from XDG_RUNTIME_DIR; on bare SSH sessions
# it can be unset → "could not detect the OwnerUID" on instance start. Provide a sane default.
if [ -z "${XDG_RUNTIME_DIR:-}" ] || [ ! -d "${XDG_RUNTIME_DIR:-/nonexistent}" ]; then
  if [ -d "/run/user/$(id -u)" ]; then export XDG_RUNTIME_DIR="/run/user/$(id -u)"
  else export XDG_RUNTIME_DIR="${TMPDIR:-/tmp}/xdg-$(id -u)"; mkdir -p "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"; fi
fi

# Prefer a LOCAL (extracted) apptainer over the system one. The system apptainer (e.g. 1.5.0) forces
# a rootless cgroup manager via a user D-Bus session ("failed to connect to dbus") and its root-owned
# conf can't be relaxed without sudo. An extracted apptainer's conf has `systemd cgroups = no`, so
# instances start with no D-Bus. Resolution order:
#   $HEAX_APPTAINER / $HEAXHUB_APPT_BIN  →  HEAXHub's own deploy/apptainer/.tools/apptainer-*/
#   (install-apptainer.sh result)  →  infra/apptainer/bin-*/  →  the HWAX portal's extracted one
#   (siblings / ~/Projects / ~/claude)  →  system `apptainer`.
APPTAINER="${HEAX_APPTAINER:-${HEAXHUB_APPT_BIN:-}}"
if [ -z "$APPTAINER" ]; then
  for c in "$ROOT"/deploy/apptainer/.tools/apptainer-*/usr/bin/apptainer \
           "$ROOT"/infra/apptainer/bin-*/usr/bin/apptainer \
           "$ROOT"/../HWAXPortal/infra/apptainer/bin-*/usr/bin/apptainer \
           "$HOME"/Projects/HWAXPortal/infra/apptainer/bin-*/usr/bin/apptainer \
           "$HOME"/claude/HWAXPortal/infra/apptainer/bin-*/usr/bin/apptainer; do
    [ -x "$c" ] && { APPTAINER="$c"; break; }
  done
fi
: "${APPTAINER:=apptainer}"
# Make sure THIS apptainer's conf disables systemd cgroups (idempotent; only if user-owned).
_conf="$(dirname "$(dirname "$(dirname "$APPTAINER")")")/etc/apptainer/apptainer.conf"
[ -w "$_conf" ] && grep -qiE '^systemd cgroups = yes' "$_conf" 2>/dev/null \
  && sed -i 's/^systemd cgroups = yes/systemd cgroups = no/' "$_conf" 2>/dev/null || true
echo "ℹ apptainer: $APPTAINER ($("$APPTAINER" --version 2>/dev/null | head -1))"

SIF_DIR="${HOME}/serviceApptainers"
PG_SIF="${SIF_DIR}/heaxhub_postgres.sif"
REDIS_SIF="${SIF_DIR}/heaxhub_redis.sif"
MAIL_SIF="${SIF_DIR}/heaxhub_mailhog.sif"
CADDY_SIF="${SIF_DIR}/heaxhub_caddy.sif"

PG_PORT=5732
REDIS_PORT=6479
SMTP_PORT=8125
MAIL_UI_PORT=8126
API_PORT=4040
WEB_PORT=4173
CADDY_ADMIN_PORT=2019
CADDY_HTTP_PORT=4180

mkdir -p var/{pg,redis,mailhog,logs,pg_run,caddy}
# Caddy 가 /pkgs/* 로 서빙할 사내 패키지 미러 루트(dist-from-drive.sh 가 latest/pip/ 를 채움).
mkdir -p var/pkg-mirror/pip

# ── 1. Postgres ───────────────────────────────────────────────
if ! "$APPTAINER" instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx heax-pg; then
  echo "→ start heax-pg"
  # (a) preflight: SIF 없으면 조용한 hang 대신 즉시 명확히 실패.
  if [ ! -f "$PG_SIF" ]; then
    echo "  ✗ postgres SIF 없음: $PG_SIF" >&2
    echo "    → 온라인 박스에서 빌드해 Drive/scp 로 서버에 전달하세요." >&2
    exit 1
  fi
  # (b) 스테일 lock 정리: 지금 heax-pg 인스턴스가 안 떠 있으므로 pgdata 의
  #     postmaster.pid 는 비정상 종료 잔재다. 남아 있으면 postgres 가 기동을 거부해
  #     아래 준비-대기가 60s 헛돌다 실패한다("start heax-pg 에서 멈춤"의 전형). 제거.
  rm -f var/pg/pgdata/postmaster.pid 2>/dev/null || true
  # (c) instance start — 실패하면 조용히 죽지 말고 로그를 띄우고 원인 힌트 후 종료.
  if ! "$APPTAINER" instance start \
        --bind "$PWD/var/pg:/var/lib/postgresql/data" \
        --bind "$PWD/var/pg_run:/var/run/postgresql" \
        "$PG_SIF" heax-pg >> var/logs/postgres-start.log 2>&1; then
    echo "  ✗ apptainer instance start heax-pg 실패 — var/logs/postgres-start.log 끝:" >&2
    tail -n 15 var/logs/postgres-start.log >&2
    if grep -qiE "namespace|userns|not permitted|clone" var/logs/postgres-start.log 2>/dev/null; then
      echo "  → 커널 unprivileged user namespaces 문제로 보입니다:" >&2
      echo "     cat /proc/sys/kernel/unprivileged_userns_clone   # 1 이어야 함" >&2
    fi
    exit 1
  fi
  # First-time init if empty
  if [ ! -d var/pg/pgdata ]; then
    if ! "$APPTAINER" exec instance://heax-pg sh -c '
          echo heaxhub > /tmp/pw
          initdb -D /var/lib/postgresql/data/pgdata -U heaxhub --pwfile=/tmp/pw -A scram-sha-256 -E UTF8
        '; then
      echo "  ✗ initdb 실패 — var/pg 권한/디스크 확인" >&2
      exit 1
    fi
  fi
  # setsid + nohup so postgres detaches from the exec parent. Without this,
  # the "$APPTAINER" exec wrapper returning makes the child get SIGHUP a few
  # seconds later and the daemon dies. Watchdog also picks it up but starting
  # cleanly avoids the flap.
  "$APPTAINER" exec instance://heax-pg sh -c "
    setsid nohup postgres -D /var/lib/postgresql/data/pgdata -p $PG_PORT -h 0.0.0.0 > /tmp/postgres.log 2>&1 < /dev/null &
  " > /dev/null 2>&1
  sleep 1
  # (d) 준비-대기 — 안 뜨면 60s 헛돌다 조용히 넘어가지 말고, 데몬 로그를 띄우고 종료.
  pg_ready=0
  for i in $(seq 1 60); do
    if "$APPTAINER" exec instance://heax-pg pg_isready -h 127.0.0.1 -p $PG_PORT -U heaxhub >/dev/null 2>&1; then
      pg_ready=1; break
    fi
    sleep 1
  done
  if [ "$pg_ready" != 1 ]; then
    echo "  ✗ postgres 가 :$PG_PORT 에서 60s 내 준비되지 않음 — 데몬 로그(/tmp/postgres.log):" >&2
    "$APPTAINER" exec instance://heax-pg cat /tmp/postgres.log 2>&1 | tail -n 20 >&2
    echo "  (흔한 원인: 스테일 lock·권한·포트충돌 — 위 로그 참고)" >&2
    exit 1
  fi
  # create db if missing
  if ! "$APPTAINER" exec instance://heax-pg env PGPASSWORD=heaxhub \
        psql -h 127.0.0.1 -p $PG_PORT -U heaxhub -d heaxhub -tAc "SELECT 1" >/dev/null 2>&1; then
    "$APPTAINER" exec instance://heax-pg env PGPASSWORD=heaxhub \
      psql -h 127.0.0.1 -p $PG_PORT -U heaxhub -d postgres -tAc 'CREATE DATABASE heaxhub OWNER heaxhub;'
  fi
  echo "  ✓ postgres on $PG_PORT"
else
  echo "✓ heax-pg already running"
fi

# ── 2. Redis ──────────────────────────────────────────────────
if ! "$APPTAINER" instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx heax-redis; then
  echo "→ start heax-redis"
  "$APPTAINER" instance start --writable-tmpfs \
    --bind "$PWD/var/redis:/data" \
    "$REDIS_SIF" heax-redis
  # redis 자체가 --daemonize yes 로 detach 됨. 추가 setsid 불필요.
  "$APPTAINER" exec instance://heax-redis sh -c \
    "redis-server --bind 0.0.0.0 --port $REDIS_PORT --dir /data --daemonize yes"
  sleep 1
  echo "  ✓ redis on $REDIS_PORT"
else
  echo "✓ heax-redis already running"
fi

# ── 3. MailHog ────────────────────────────────────────────────
if ! "$APPTAINER" instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx heax-mailhog; then
  echo "→ start heax-mailhog"
  "$APPTAINER" instance start --writable-tmpfs "$MAIL_SIF" heax-mailhog
  "$APPTAINER" exec instance://heax-mailhog sh -c \
    "setsid nohup MailHog -smtp-bind-addr 0.0.0.0:$SMTP_PORT -ui-bind-addr 0.0.0.0:$MAIL_UI_PORT -api-bind-addr 0.0.0.0:$MAIL_UI_PORT > /tmp/mailhog.log 2>&1 < /dev/null &" \
    > /dev/null 2>&1
  sleep 1
  echo "  ✓ mailhog smtp=$SMTP_PORT ui=$MAIL_UI_PORT"
else
  echo "✓ heax-mailhog already running"
fi

# ── 4. Caddy reverse proxy ────────────────────────────────────
# Pull caddy:2-alpine into a SIF if missing.
if [ ! -f "$CADDY_SIF" ]; then
  echo "→ pull caddy SIF (docker://caddy:2-alpine)"
  mkdir -p "$SIF_DIR"
  "$APPTAINER" pull --force "$CADDY_SIF" docker://caddy:2-alpine \
    >> var/logs/caddy-pull.log 2>&1
fi

BOOTSTRAP_SRC="$PWD/deploy/apptainer/caddy_bootstrap.json"
BOOTSTRAP_DST="$PWD/var/caddy/bootstrap.json"
cp -f "$BOOTSTRAP_SRC" "$BOOTSTRAP_DST"

FRONTEND_DIST="$PWD/frontend/dist"
if [ ! -f "$FRONTEND_DIST/index.html" ]; then
  echo "  ! frontend/dist/index.html 없음 — pnpm build 먼저 실행해야 :$CADDY_HTTP_PORT 가 UI 를 보냅니다"
fi

if ! "$APPTAINER" instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx heax-caddy; then
  echo "→ start heax-caddy"
  "$APPTAINER" instance start \
    --bind "$BOOTSTRAP_DST:/etc/caddy/bootstrap.json:ro" \
    --bind "$FRONTEND_DIST:/srv/web:ro" \
    --bind "$PWD/var/pkg-mirror:/srv/pkgs:ro" \
    --bind "$PWD/var/caddy:/data" \
    "$CADDY_SIF" heax-caddy >> var/logs/caddy-start.log 2>&1
  # Caddy in the instance: run with our bootstrap config, fully detached.
  # Caddy 2.x defaults to JSON for *.json — don't pass --adapter json (invalid).
  "$APPTAINER" exec instance://heax-caddy sh -c "
    setsid nohup caddy run --config /etc/caddy/bootstrap.json \
      > /data/caddy.log 2>&1 < /dev/null &
  " > /dev/null 2>&1
  # wait for admin API readiness — 30s total (60 × 0.5s)
  for i in $(seq 1 60); do
    if curl -sf "http://127.0.0.1:${CADDY_ADMIN_PORT}/config/" >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done
  if curl -sf "http://127.0.0.1:${CADDY_ADMIN_PORT}/config/" >/dev/null 2>&1; then
    echo "  ✓ caddy admin=$CADDY_ADMIN_PORT  http=$CADDY_HTTP_PORT"
  else
    echo "  ! caddy admin not reachable — check var/logs/caddy-start.log and var/caddy/caddy.log"
  fi
else
  echo "✓ heax-caddy already running"
fi

# ── 5. Backend + Worker + Frontend ────────────────────────────
if ! curl -sf "http://localhost:$API_PORT/health" >/dev/null 2>&1; then
  echo "→ start backend (uvicorn :$API_PORT)"
  nohup bash -c 'set -a; source .env; set +a; cd backend && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port '"$API_PORT" \
    > var/logs/backend.log 2>&1 &
  disown
  # readiness 대기 — 백엔드가 부팅 중 죽으면(시크릿 가드/임포트 등) 조용한 502 대신
  # 즉시 크래시 로그를 보여준다(가이드라인 10: 추측 말고 실제 로그).
  for _i in $(seq 1 25); do
    curl -sf "http://localhost:$API_PORT/health" >/dev/null 2>&1 && break
    sleep 1
  done
  if curl -sf "http://localhost:$API_PORT/health" >/dev/null 2>&1; then
    echo "  ✓ backend up (:$API_PORT)"
  else
    echo "  ✗ backend /health 미응답 — 크래시 로그 마지막 40줄 (var/logs/backend.log)" >&2
    echo "  ----------------------------------------------------------------" >&2
    tail -n 40 var/logs/backend.log >&2 2>/dev/null || true
    echo "  ----------------------------------------------------------------" >&2
  fi
fi

if ! pgrep -f "celery -A app.workers.celery_app worker" >/dev/null; then
  echo "→ start celery worker"
  nohup bash -c 'set -a; source .env; set +a; cd backend && .venv/bin/celery -A app.workers.celery_app worker --loglevel=info --concurrency=2' \
    > var/logs/worker.log 2>&1 &
  disown
fi

if ! pgrep -f "celery -A app.workers.celery_app beat" >/dev/null; then
  echo "→ start celery beat"
  nohup bash -c 'set -a; source .env; set +a; cd backend && .venv/bin/celery -A app.workers.celery_app beat --loglevel=info --schedule=../var/celerybeat-schedule' \
    > var/logs/beat.log 2>&1 &
  disown
fi

# Vite dev 서버(:WEB_PORT)는 운영 경로에서 자동 기동하지 않습니다.
# Caddy(:$CADDY_HTTP_PORT)가 frontend/dist를 SPA로 직접 서빙합니다.
# 개발 중 hot-reload가 필요하면 별도로 `make frontend` 실행하세요.

sleep 5
echo
echo "─────────────────────────────────────────────────────"
echo " HEAXHub local stack is up"
echo "─────────────────────────────────────────────────────"
echo "  Web        : http://localhost:$WEB_PORT/"
echo "  API        : http://localhost:$API_PORT/  (docs: /docs)"
echo "  MailHog UI : http://localhost:$MAIL_UI_PORT/"
echo "  Postgres   : 127.0.0.1:$PG_PORT  (heaxhub / heaxhub / heaxhub)"
echo "  Redis      : 127.0.0.1:$REDIS_PORT"
echo "  Caddy      : http://localhost:$CADDY_HTTP_PORT/  (admin: 127.0.0.1:$CADDY_ADMIN_PORT)"
echo "               호스팅 앱 진입점: http://localhost:$CADDY_HTTP_PORT/apps/{app_id}/"
echo
echo "  로그: tail -f var/logs/{backend,worker,beat,frontend}.log"
echo "  종료: bash deploy/apptainer/stop.sh"
