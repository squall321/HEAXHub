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
# 로컬 격리 런타임 보장(.tools/): 시스템 apptainer/python 의존 제거. cache/ 에
# apptainer.deb / python tarball 이 있으면 즉시 추출(오프라인), 없으면 다운로드
# 시도, 이미 있으면 skip. 둘 다 실패해도 아래 require_* 가 명확히 안내한다.
bash "$APPT_DIR/install-apptainer.sh" >/dev/null 2>&1 || warn "apptainer .tools 설치 생략(이미 있거나 실패)"
bash "$APPT_DIR/install-python.sh"     >/dev/null 2>&1 || warn "python .tools 설치 생략(이미 있거나 실패)"
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
# JWT_SECRET 이 약하면(미설정/빈값/기본 placeholder) 강한 값으로 생성한다.
# SECRET_ENCRYPTION_KEY 는 *비어 있을 때만* 생성하고, 이미 있으면 절대 덮지 않는다
# (덮으면 DB 에 이미 암호화된 시크릿이 복호 불가가 됨). openssl 만 사용 — Step 1 은
# venv 생성 전이라 cryptography 에 의존하지 않게 한다.
step "Step 1 — 시크릿 점검 / 자동 생성"
_set_kv() {  # .env 의 KEY=VAL set-or-append (라인 없으면 추가)
  local f="$1" k="$2" v="$3"
  if grep -qE "^${k}=" "$f"; then
    sed -i "s|^${k}=.*|${k}=${v}|" "$f"
  else
    printf '%s=%s\n' "$k" "$v" >> "$f"
  fi
}
for f in "$ROOT_DIR/.env" "$BACKEND_DIR/.env"; do
  [[ -f "$f" ]] || continue
  _jwt="$(sed -n 's/^JWT_SECRET=//p' "$f" | tail -1)"
  case "$_jwt" in
    ""|"change-me-to-a-strong-random-secret"|"local-dev-secret-do-not-use-in-prod")
      _set_kv "$f" JWT_SECRET "$(openssl rand -hex 64)"
      ok "JWT_SECRET 생성 → $f"
      ;;
    *) note "JWT_SECRET 이미 설정됨 → 유지 ($f)" ;;
  esac
  if ! grep -qE "^SECRET_ENCRYPTION_KEY=.+" "$f"; then
    # Fernet 호환 키(32바이트 urlsafe-base64) — cryptography 불필요
    _set_kv "$f" SECRET_ENCRYPTION_KEY "$(openssl rand -base64 32 | tr '+/' '-_')"
    ok "SECRET_ENCRYPTION_KEY 생성 → $f"
  else
    note "SECRET_ENCRYPTION_KEY 이미 설정됨 → 유지(덮지 않음, 기존 암호화 시크릿 보존)"
  fi
done
ok "시크릿 점검 완료 (회전 절차: docs/SECRET_ROTATION.md)"

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
elif [[ "${HEAX_NO_BUILD:-0}" == "1" ]]; then
  # HEAX_NO_BUILD=1: 빌드 금지(명시). pre-built dist 를 받거나 오프라인 미러로 직접 빌드.
  err "dist 없음 + HEAX_NO_BUILD=1 — 빌드하지 않음. 둘 중 하나:"
  echo "    (A) pre-built dist 수령:  ./deploy/apptainer/dist-from-drive.sh"
  echo "    (B) 오프라인 직접 빌드:   ./deploy/apptainer/mirror-from-drive.sh \\"
  echo "                              && HEAX_BASE_PATH=/heax-hub/ ./deploy/apptainer/build-frontend.sh"
  exit 1
else
  # build-frontend.sh 가 알아서: vendored node+pnpm(.tools) + 오프라인 스토어 우선,
  # 없으면 시스템 pnpm 온라인. 툴체인이 아예 없으면 명확히 err. HEAX_BASE_PATH(포털
  # 서브패스)는 build-frontend.sh 가 .env 에서 읽어 VITE_BASE_PATH 로 주입.
  ok "frontend 빌드 → build-frontend.sh"
  bash "$ROOT_DIR/deploy/apptainer/build-frontend.sh"
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
  warn "backend /health 응답 없음 — 크래시 로그 마지막 40줄 ($LOG_DIR/backend.log)"
  echo "  ----------------------------------------------------------------" >&2
  tail -n 40 "$LOG_DIR/backend.log" >&2 2>/dev/null || true
  echo "  ----------------------------------------------------------------" >&2
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
