#!/usr/bin/env bash
# HEAXHub — 한방 셋업.
#
# 가정:
#   - scripts/bootstrap-host.sh 가 이미 한 번 돌아서 시스템 의존성 설치 완료
#   - SIF 4개(postgres/redis/mailhog/caddy) 가 ./deploy/apptainer/build.sh 로
#     빌드되었거나 ~/serviceApptainers 또는 deploy/apptainer/*.sif 에 존재
#
# 흐름:
#   1) .env 보장
#   2) 시크릿 자동 생성 (JWT_SECRET / SECRET_ENCRYPTION_KEY 가 placeholder 면)
#   3) backend venv + alembic upgrade head + admin seed
#   4) frontend dist 보장 (있으면 skip, 없으면 pnpm build)
#   5) deploy/apptainer/start.sh 호출 — 인스턴스/프로세스 기동
#   6) /health + Caddy admin 검증
#
# 사용:
#   bash deploy/apptainer/install_all.sh
#   bash deploy/apptainer/install_all.sh --skip-build-frontend
#   bash deploy/apptainer/install_all.sh --reset    # 기존 인스턴스/프로세스 정리 후 새로
set -euo pipefail

# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

SKIP_FE=0
RESET=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build-frontend) SKIP_FE=1; shift ;;
    --reset)               RESET=1; shift ;;
    -h|--help)             sed -n '2,22p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) err "unknown arg: $1"; exit 2 ;;
  esac
done

load_env
export_proxy
require_apptainer
ensure_dirs
require_disk 3

HOST_IP="$(detect_host_ip)"
echo "================================================================"
echo " HEAXHub — Apptainer one-shot setup"
echo "  host IP   : $HOST_IP"
echo "  app port  : ${APP_PORT:-4040}"
echo "  caddy     : ${PUBLIC_PORT:-4180}"
echo "================================================================"

# ── 1) 시크릿 자동 생성 ────────────────────────────────────────────────
step "Step 1 — 시크릿 점검 / 자동 생성"
ROTATE=0
for f in "$ROOT_DIR/.env" "$BACKEND_DIR/.env"; do
  [[ -f "$f" ]] || continue
  if grep -q "^JWT_SECRET=local-dev-secret-do-not-use-in-prod" "$f"; then
    ROTATE=1
  fi
  if ! grep -qE "^SECRET_ENCRYPTION_KEY=." "$f"; then
    ROTATE=1
  fi
done
if [[ $ROTATE -eq 1 ]]; then
  ok "시크릿 회전 진행 (JWT_SECRET / SECRET_ENCRYPTION_KEY)"
  local_jwt="$(openssl rand -hex 64)"
  local_fernet="$( "$BACKEND_DIR/.venv/bin/python" -c \
    "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null \
    || python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null \
    || true)"
  if [[ -z "$local_fernet" ]]; then
    warn "cryptography 모듈 없음 — venv 생성 후 다시 시도"
  else
    for f in "$ROOT_DIR/.env" "$BACKEND_DIR/.env"; do
      [[ -f "$f" ]] || continue
      sed -i "s|^JWT_SECRET=.*|JWT_SECRET=${local_jwt}|" "$f"
      if grep -q "^SECRET_ENCRYPTION_KEY=" "$f"; then
        sed -i "s|^SECRET_ENCRYPTION_KEY=.*|SECRET_ENCRYPTION_KEY=${local_fernet}|" "$f"
      else
        echo "SECRET_ENCRYPTION_KEY=${local_fernet}" >> "$f"
      fi
    done
    ok "회전 완료. 자세한 절차는 docs/SECRET_ROTATION.md."
  fi
else
  ok "이미 강한 시크릿 (placeholder 아님)"
fi

# ── 2) backend venv + alembic + admin seed ────────────────────────────
step "Step 2 — 백엔드 venv / 마이그레이션 / 초기 admin"
require_python_venv

# Postgres 가 떠 있어야 alembic 이 동작 — start.sh 로 PG 먼저 띄움
if ! instance_running heax-pg; then
  ok "Postgres 인스턴스 기동 (heax-pg)"
  bash "$APPT_DIR/start.sh" 2>&1 | sed -n '/heax-pg\|postgres on/p' || true
fi

# Postgres readiness wait
for i in $(seq 1 30); do
  if pg_isready -h 127.0.0.1 -p "${POSTGRES_PORT:-5732}" -U "${POSTGRES_USER:-heaxhub}" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

ok "alembic upgrade head"
( cd "$BACKEND_DIR" && set -a && . "$ROOT_DIR/.env" && set +a \
  && "$BACKEND_DIR/.venv/bin/alembic" upgrade head )

ok "admin seed (idempotent)"
( cd "$BACKEND_DIR" && set -a && . "$ROOT_DIR/.env" && set +a \
  && "$BACKEND_DIR/.venv/bin/python" -m scripts.create_admin ) \
  || warn "admin seed 실패 — 이미 존재할 수 있음"

# ── 3) frontend dist 보장 ─────────────────────────────────────────────
step "Step 3 — frontend dist"
if [[ -f "$FRONTEND_DIR/dist/index.html" ]]; then
  ok "기존 dist 사용: $FRONTEND_DIR/dist/index.html"
elif [[ $SKIP_FE -eq 1 ]]; then
  warn "--skip-build-frontend — dist 없는 상태로 진행 (Caddy 가 빈 SPA 응답)"
elif command -v pnpm >/dev/null 2>&1; then
  # HEAX_BASE_PATH=/heax-hub/ → build the SPA for the HWAX portal sub-path (assets/router/api/ws
  # under the prefix). Empty → root (standalone), unchanged.
  # Read HEAX_BASE_PATH from .env (set HEAX_BASE_PATH=/heax-hub/ to build for the portal sub-path).
  [ -f "$ROOT_DIR/.env" ] && { set -a; . "$ROOT_DIR/.env"; set +a; }
  ok "pnpm install + build${HEAX_BASE_PATH:+ (base ${HEAX_BASE_PATH})}"
  ( cd "$FRONTEND_DIR" && pnpm install --frozen-lockfile && VITE_BASE_PATH="${HEAX_BASE_PATH:-/}" pnpm build )
else
  warn "pnpm 없음 — bundle 에 포함된 dist 가 있어야 합니다."
fi

# ── 4) reset 모드면 기존 stop ─────────────────────────────────────────
if [[ $RESET -eq 1 ]]; then
  step "Step 4a — 기존 인스턴스/프로세스 정리"
  bash "$APPT_DIR/stop.sh" 2>&1 | sed -n 's/^/  /p' || true
fi

# ── 5) start.sh ──────────────────────────────────────────────────────
step "Step 4 — 인스턴스 / 프로세스 기동"
bash "$APPT_DIR/start.sh"

# ── 6) 검증 ──────────────────────────────────────────────────────────
step "Step 5 — 라이브 점검"
sleep 3
HEALTH_OK=0
CADDY_OK=0
if curl -sf "http://127.0.0.1:${APP_PORT:-4040}/health" >/dev/null; then
  ok "backend /health OK"
  HEALTH_OK=1
else
  warn "backend /health 응답 없음 — var/logs/backend.log 확인"
fi
if curl -sf "http://127.0.0.1:${CADDY_ADMIN_URL##*:}/config/" >/dev/null 2>&1 \
   || curl -sf "http://127.0.0.1:2019/config/" >/dev/null 2>&1; then
  ok "caddy admin OK"
  CADDY_OK=1
else
  warn "caddy admin 응답 없음"
fi

if [[ $HEALTH_OK -eq 1 && $CADDY_OK -eq 1 ]]; then
  echo
  ok "셋업 성공 — 브라우저로 접속하세요"
  echo "  Caddy(SPA + API proxy) : http://${HOST_IP}:${PUBLIC_PORT:-4180}/"
  echo "  Backend OpenAPI docs   : http://${HOST_IP}:${APP_PORT:-4040}/docs"
  echo "  MailHog UI             : http://${HOST_IP}:${SMTP_UI_PORT:-8126}/"
  echo "  초기 로그인            : ${SEED_ADMIN_EMAIL:-admin@example.com} / ${SEED_ADMIN_PASSWORD:-ChangeMe-On-First-Login!}"
  exit 0
else
  echo
  warn "셋업 검증 실패 — 로그를 확인하세요: tail -f $LOG_DIR/{backend,worker,beat}.log"
  exit 1
fi
