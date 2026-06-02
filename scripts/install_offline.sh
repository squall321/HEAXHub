#!/usr/bin/env bash
# scripts/install_offline.sh
#
# 오프라인 Ubuntu 24.04 타깃에서 heaxhub-bundle 을 풀어둔 디렉터리에서
# 실행하는 설치 스크립트.
#
# 다음을 수행:
#   1) 시스템 사전 패키지 확인 (postgresql-client, redis-tools, apptainer, python3.11+)
#   2) 백엔드 파이썬 venv 만들고 wheels/ 에서 오프라인 설치
#   3) frontend-dist 를 Caddy 가 서빙할 위치로 복사
#   4) HeaxAgent 바이너리 설치 + systemd unit 등록
#   5) sifs/ 심볼릭 링크 / 실파일을 ~/serviceApptainers (또는 SIF_DEST) 로 배치
#   6) alembic upgrade head
#   7) scripts/create_admin.py 실행
#   8) 다음 절차 안내 출력
#
# 옵션:
#   --target-root <dir>   설치 베이스 (기본: $HOME/heaxhub)
#   --sif-dest <dir>      SIF 설치 위치 (기본: $HOME/serviceApptainers)
#   --frontend-dest <dir> 정적 산출물 위치 (기본: <target-root>/web)
#   --skip-deps           apt 확인 생략
#   --skip-admin          관리자 계정 생성 생략
#   --skip-systemd        systemd 등록 생략
#
set -euo pipefail

# ─── 경로/옵션 ──────────────────────────────────────────────────────────────
BUNDLE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_ROOT="${TARGET_ROOT:-${HOME}/heaxhub}"
SIF_DEST="${SIF_DEST:-${HOME}/serviceApptainers}"
FRONTEND_DEST=""
SKIP_DEPS=0
SKIP_ADMIN=0
SKIP_SYSTEMD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target-root)    TARGET_ROOT="$2"; shift 2 ;;
    --sif-dest)       SIF_DEST="$2"; shift 2 ;;
    --frontend-dest)  FRONTEND_DEST="$2"; shift 2 ;;
    --skip-deps)      SKIP_DEPS=1; shift ;;
    --skip-admin)     SKIP_ADMIN=1; shift ;;
    --skip-systemd)   SKIP_SYSTEMD=1; shift ;;
    -h|--help)        sed -n '2,28p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

FRONTEND_DEST="${FRONTEND_DEST:-${TARGET_ROOT}/web}"

WHEELS_DIR="${BUNDLE_ROOT}/wheels"
SIFS_DIR="${BUNDLE_ROOT}/sifs"
AGENTS_DIR="${BUNDLE_ROOT}/agents"
FRONTEND_SRC="${BUNDLE_ROOT}/frontend-dist"
CONFIG_DIR="${BUNDLE_ROOT}/config"

log()  { echo "[install] $*"; }
warn() { echo "[install][WARN] $*" >&2; }
err()  { echo "[install][ERR] $*" >&2; }

# ─── 1) 사전 패키지 확인 ────────────────────────────────────────────────────
check_deps() {
  log "checking host prerequisites"
  local missing=()
  for cmd in python3 apptainer psql redis-cli; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      missing+=("$cmd")
    fi
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    err "missing: ${missing[*]}"
    err "사전에 (인터넷 가능 서버에서) 다음 apt 패키지가 설치되어야 함:"
    err "  postgresql-client redis-tools apptainer python3.11 python3.11-venv"
    exit 1
  fi

  # python 버전 체크
  local pyv
  pyv="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  case "$pyv" in
    3.1[0-9]) log "python3 = ${pyv} OK" ;;
    *) warn "python3 = ${pyv} (3.10+ 권장)" ;;
  esac
}

# ─── 2) 백엔드 오프라인 설치 ────────────────────────────────────────────────
install_backend() {
  log "installing backend offline → ${TARGET_ROOT}/backend"
  mkdir -p "${TARGET_ROOT}"
  if [[ ! -d "${TARGET_ROOT}/backend" ]]; then
    # 번들 안에 backend 소스가 없으면, 운영자는 별도 git checkout 한 위치를
    # --target-root 로 지정해야 함. 메시지로 가이드.
    if [[ -d "${BUNDLE_ROOT}/backend" ]]; then
      cp -r "${BUNDLE_ROOT}/backend" "${TARGET_ROOT}/backend"
    else
      warn "backend 소스 트리가 번들 밖에 있다고 가정 (${TARGET_ROOT}/backend 에 미리 둘 것)"
    fi
  fi

  local venv="${TARGET_ROOT}/backend/.venv"
  if [[ ! -d "$venv" ]]; then
    python3 -m venv "$venv"
  fi
  # 오프라인 install: --no-index --find-links wheels/
  "${venv}/bin/pip" install --no-index --find-links "${WHEELS_DIR}" \
      pip setuptools wheel
  "${venv}/bin/pip" install --no-index --find-links "${WHEELS_DIR}" \
      -e "${TARGET_ROOT}/backend"
  log "backend venv ready: ${venv}"
}

# ─── 3) frontend 배치 ───────────────────────────────────────────────────────
install_frontend() {
  log "installing frontend → ${FRONTEND_DEST}"
  mkdir -p "${FRONTEND_DEST}"
  if [[ -d "${FRONTEND_SRC}" ]]; then
    cp -r "${FRONTEND_SRC}/." "${FRONTEND_DEST}/"
  else
    warn "frontend-dist 가 번들에 없음 — 건너뜀"
  fi
}

# ─── 4) HeaxAgent 설치 + systemd ────────────────────────────────────────────
install_agent() {
  log "installing HeaxAgent (linux-x64)"
  local agent_dir="${TARGET_ROOT}/agent"
  mkdir -p "$agent_dir"
  if [[ -d "${AGENTS_DIR}/linux-x64" ]]; then
    cp -r "${AGENTS_DIR}/linux-x64/." "${agent_dir}/"
    chmod +x "${agent_dir}/HeaxAgent" 2>/dev/null || true
  else
    warn "linux-x64 agent 가 번들에 없음 — 건너뜀"
    return 0
  fi

  if [[ "$SKIP_SYSTEMD" -eq 1 ]]; then
    log "skip systemd registration (per flag)"
    return 0
  fi

  local unit_dir="${HOME}/.config/systemd/user"
  mkdir -p "$unit_dir"
  cat > "${unit_dir}/heaxhub-agent.service" <<EOF
[Unit]
Description=HEAXHub Agent (offline install)
After=network.target

[Service]
Type=simple
WorkingDirectory=${agent_dir}
ExecStart=${agent_dir}/HeaxAgent
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload || warn "systemctl --user not available (will need login session)"
  systemctl --user enable heaxhub-agent.service 2>/dev/null || true
  log "systemd unit installed: ${unit_dir}/heaxhub-agent.service"
}

# ─── 5) SIFs 배치 ───────────────────────────────────────────────────────────
install_sifs() {
  log "installing SIFs → ${SIF_DEST}"
  mkdir -p "${SIF_DEST}"
  if [[ ! -d "${SIFS_DIR}" ]]; then
    warn "sifs/ 디렉터리 없음 — 건너뜀"
    return 0
  fi
  # 번들의 sifs/ 는 심볼릭 링크일 수 있으니 -L 로 따라가서 실파일을 옮긴다.
  shopt -s nullglob
  for entry in "${SIFS_DIR}"/*.sif; do
    local base; base="$(basename "$entry")"
    local dst="${SIF_DEST}/${base}"
    if [[ -e "$dst" ]]; then
      log "  - keep existing ${dst}"
      continue
    fi
    if [[ -L "$entry" ]]; then
      cp -L "$entry" "$dst"
    else
      cp "$entry" "$dst"
    fi
    log "  + installed ${base}"
  done
  shopt -u nullglob
}

# ─── 6) DB 마이그레이션 ─────────────────────────────────────────────────────
run_migrations() {
  log "running alembic upgrade head"
  local venv="${TARGET_ROOT}/backend/.venv"
  if [[ ! -x "${venv}/bin/alembic" ]]; then
    warn "alembic 바이너리를 venv에서 찾지 못함 — 건너뜀"
    return 0
  fi
  ( cd "${TARGET_ROOT}/backend" && "${venv}/bin/alembic" upgrade head ) \
    || warn "alembic upgrade failed — DB 연결 / .env 확인 필요"
}

# ─── 7) 관리자 계정 생성 ────────────────────────────────────────────────────
create_admin() {
  if [[ "$SKIP_ADMIN" -eq 1 ]]; then
    log "skip admin creation (per flag)"
    return 0
  fi
  log "creating admin account"
  local venv="${TARGET_ROOT}/backend/.venv"
  if [[ -x "${venv}/bin/python" && -f "${TARGET_ROOT}/backend/scripts/create_admin.py" ]]; then
    ( cd "${TARGET_ROOT}/backend" && "${venv}/bin/python" scripts/create_admin.py ) \
      || warn "create_admin.py 실패 — 수동 실행 필요"
  else
    warn "create_admin.py 또는 venv가 없음 — 수동 실행 필요"
  fi
}

# ─── 8) 다음 단계 안내 ──────────────────────────────────────────────────────
next_steps() {
  cat <<EOF

================ install complete ================
다음 단계:
  1) ${TARGET_ROOT}/backend/.env 작성 (config/.env.template 참고)
       cp ${CONFIG_DIR}/.env.template ${TARGET_ROOT}/backend/.env
       vi ${TARGET_ROOT}/backend/.env
  2) Apptainer 인스턴스 기동:
       bash ${BUNDLE_ROOT}/scripts/build_apptainer_sif.sh   # 필요시
       cd ${TARGET_ROOT} && bash deploy/apptainer/start.sh  # 운영 시작
  3) 자동 기동 등록:
       bash ${BUNDLE_ROOT}/scripts/install_autostart.sh
  4) 헬스체크:
       curl -fsS http://localhost:8000/admin/system/health
==================================================
EOF
}

# ─── main ───────────────────────────────────────────────────────────────────
[[ "$SKIP_DEPS" -eq 1 ]] || check_deps
install_backend
install_frontend
install_agent
install_sifs
run_migrations
create_admin
next_steps
