#!/usr/bin/env bash
# HEAXHub — apptainer 공용 헬퍼.
#
# 모든 deploy/apptainer/*.sh 가 source 해서 쓰는 공통 유틸:
#   resolve_apptainer / require_apptainer  — 핀 버전(.tools/apptainer-*) 우선,
#                                             없으면 시스템 apptainer 사용.
#   apptainer()                            — 함수로 가로채서 위 경로로 자동 라우팅
#                                             (alias 가 비대화 스크립트에서 안 먹어서).
#   load_env                               — deploy/apptainer/.env → 환경변수 export.
#                                             없으면 .env.example 복사.
#   export_proxy                           — HTTPS/HTTP_PROXY, BUILD_PROXY_*,
#                                             DEFAULT_FALLBACK_PROXY 순서로 결정.
#   detect_host_ip                         — public → LAN → 127.0.0.1 폴백.
#   ensure_dirs                            — var/{pg,redis,mailhog,caddy,logs} 생성.
#   require_python_venv                    — backend/.venv 보장 (오프라인 wheels 우선).
#   require_disk N_GB                      — 여유 디스크 검사.
#   require_port_free PORT                 — 포트 점유 여부 검사.
#
# 디자인 원칙: AIDataHub/_common.sh 의 패턴을 거의 그대로 차용 — 검증된 손상 없는
# 한정-범위(.tools/) 설치, 시스템 apt 무손상, 함수 가로채기.
set -uo pipefail

# ── 경로 ────────────────────────────────────────────────────────────────────
APPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$APPT_DIR/../.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
VAR_DIR="$ROOT_DIR/var"
LOG_DIR="$VAR_DIR/logs"
TOOLS_DIR="$APPT_DIR/.tools"
CACHE_DIR="$APPT_DIR/cache"
SIF_DIR="${SIF_DIR:-$APPT_DIR}"

# ── 색상 ────────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  _C_RESET=$'\033[0m'; _C_BLUE=$'\033[1;34m'; _C_GREEN=$'\033[1;32m'
  _C_YELLOW=$'\033[1;33m'; _C_RED=$'\033[1;31m'; _C_DIM=$'\033[2m'
else
  _C_RESET=""; _C_BLUE=""; _C_GREEN=""; _C_YELLOW=""; _C_RED=""; _C_DIM=""
fi
step()  { printf "\n${_C_BLUE}▶ %s${_C_RESET}\n" "$*"; }
ok()    { printf "  ${_C_GREEN}✓${_C_RESET} %s\n" "$*"; }
warn()  { printf "  ${_C_YELLOW}!${_C_RESET} %s\n" "$*" >&2; }
err()   { printf "  ${_C_RED}✗${_C_RESET} %s\n" "$*" >&2; }
note()  { printf "  ${_C_DIM}%s${_C_RESET}\n" "$*"; }

# ── apptainer 바이너리 결정 ────────────────────────────────────────────────
# 우선순위:
#   1) HEAXHUB_APPT_BIN 환경변수 (운영자 override)
#   2) .tools/apptainer-<VER>/usr/bin/apptainer (install-apptainer.sh 결과)
#   3) /usr/local/bin/apptainer
#   4) PATH 의 apptainer
resolve_apptainer() {
  if [[ -n "${HEAXHUB_APPT_BIN:-}" && -x "${HEAXHUB_APPT_BIN}" ]]; then
    _HEAX_APPT="${HEAXHUB_APPT_BIN}"
    return 0
  fi
  # .tools/apptainer-*/usr/bin/apptainer 중 가장 최신 버전 디렉터리
  if [[ -d "$TOOLS_DIR" ]]; then
    local newest
    newest=$(find "$TOOLS_DIR" -maxdepth 4 -type f -name apptainer -path "*/usr/bin/apptainer" 2>/dev/null \
              | sort -V | tail -1)
    if [[ -n "$newest" && -x "$newest" ]]; then
      _HEAX_APPT="$newest"
      return 0
    fi
  fi
  if [[ -x /usr/local/bin/apptainer ]]; then
    _HEAX_APPT="/usr/local/bin/apptainer"
    return 0
  fi
  if command -v apptainer >/dev/null 2>&1; then
    _HEAX_APPT="$(command -v apptainer)"
    return 0
  fi
  _HEAX_APPT=""
  return 1
}

resolve_apptainer || true
# 모든 후속 `apptainer ...` 호출을 핀버전으로 라우팅. export -f 는 sub-shell 까지.
apptainer() { command "${_HEAX_APPT:-apptainer}" "$@"; }
export -f apptainer 2>/dev/null || true

# 벤더 apptainer(.tools)의 apptainer.conf 가 없으면 confgen 으로 생성한다.
# 드라이브 미러링/부분 추출로 usr/etc/ 가 빠지면 이 파일이 없어, 모든 exec/instance 가
# "couldn't parse configuration file .../apptainer.conf: no such file" 로 즉사한다(→ pg/redis/caddy 전멸).
ensure_apptainer_conf() {
  local bin="${_HEAX_APPT:-}" usr conf
  [[ -n "$bin" ]] || return 0
  case "$bin" in "$TOOLS_DIR"/*) : ;; *) return 0 ;; esac   # 벤더 설치에만 적용(시스템 apptainer 무손상)
  usr="$(cd "$(dirname "$bin")/.." 2>/dev/null && pwd)" || return 0   # .../apptainer-<ver>/usr
  conf="$usr/etc/apptainer/apptainer.conf"
  [[ -f "$conf" ]] && return 0
  mkdir -p "$(dirname "$conf")"
  if "$bin" confgen "$conf" >/dev/null 2>&1 && [[ -s "$conf" ]]; then
    note "apptainer.conf 누락 → confgen 으로 자동 생성: $conf"
  else
    err "apptainer.conf 자동 생성 실패: $conf (bash deploy/apptainer/install-apptainer.sh --force 로 재설치 필요)"
    return 1
  fi
}

require_apptainer() {
  resolve_apptainer || true
  if [[ -z "${_HEAX_APPT:-}" || ! -x "$_HEAX_APPT" ]]; then
    err "apptainer 바이너리를 찾을 수 없습니다."
    err "  → bash deploy/apptainer/install-apptainer.sh 로 .tools/ 에 핀버전 설치하거나"
    err "    호스트에 apptainer 1.3.x 를 설치하세요."
    exit 1
  fi
  ensure_apptainer_conf || true   # conf 누락 시 self-heal (usr/etc 가 빠진 미러링 서버 대비)
  local v
  v="$("$_HEAX_APPT" --version 2>&1 | head -1)"
  note "apptainer: $_HEAX_APPT ($v)"
}

# ── python 바이너리 결정 (apptainer 와 동일한 .tools 우선 패턴) ───────────────
# 우선순위:
#   1) HEAXHUB_PYTHON_BIN 환경변수 (운영자 override)
#   2) .tools/python-<VER>/bin/python3 (install-python.sh 가 푼 vendored standalone)
#   3) $PYTHON_BIN (예: python3.12) on PATH
#   4) python3 on PATH
# 호스트 python 의존을 없애려면 (2) 가 잡히게 install-python.sh 를 먼저 돌린다.
resolve_python() {
  if [[ -n "${HEAXHUB_PYTHON_BIN:-}" && -x "${HEAXHUB_PYTHON_BIN}" ]]; then
    _HEAX_PY="${HEAXHUB_PYTHON_BIN}"; return 0
  fi
  if [[ -d "$TOOLS_DIR" ]]; then
    local newest
    newest=$(find "$TOOLS_DIR" -maxdepth 3 -type f -path "*/python-*/bin/python3" 2>/dev/null \
              | sort -V | tail -1)
    if [[ -n "$newest" && -x "$newest" ]]; then
      _HEAX_PY="$newest"; return 0
    fi
  fi
  if [[ -n "${PYTHON_BIN:-}" ]] && command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    _HEAX_PY="$(command -v "$PYTHON_BIN")"; return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    _HEAX_PY="$(command -v python3)"; return 0
  fi
  _HEAX_PY=""; return 1
}

# ── .env 로드 ──────────────────────────────────────────────────────────────
# 우선순위: deploy/apptainer/.env > 프로젝트 루트 .env.
# 없으면 .env.example 자동 복사 (재실행 시 운영자가 편집할 수 있게).
load_env() {
  local env_file=""
  if [[ -f "$APPT_DIR/.env" ]]; then
    env_file="$APPT_DIR/.env"
  elif [[ -f "$ROOT_DIR/.env" ]]; then
    env_file="$ROOT_DIR/.env"
  elif [[ -f "$ROOT_DIR/.env.example" ]]; then
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
    env_file="$ROOT_DIR/.env"
    warn ".env 자동 생성 (.env.example 복사). JWT_SECRET 등 시크릿 회전 권장."
  else
    err ".env / .env.example 둘 다 없습니다."
    exit 1
  fi
  set -a
  # shellcheck disable=SC1090
  . "$env_file"
  set +a
}

# ── 프록시 ─────────────────────────────────────────────────────────────────
# 사내망 폴백 프록시 (필요 시 환경별로 .env 의 HEAXHUB_FALLBACK_PROXY 로 override).
DEFAULT_FALLBACK_PROXY="${HEAXHUB_FALLBACK_PROXY:-}"

export_proxy() {
  local hp="${HTTPS_PROXY:-${https_proxy:-}}"
  local hpp="${HTTP_PROXY:-${http_proxy:-}}"
  if [[ -z "$hp" && -n "${BUILD_PROXY_HTTPS:-}" && "${BUILD_PROXY_HTTPS}" != "off" ]]; then
    hp="$BUILD_PROXY_HTTPS"
  fi
  if [[ -z "$hpp" && -n "${BUILD_PROXY_HTTP:-${BUILD_PROXY_HTTPS:-}}" ]]; then
    local cand="${BUILD_PROXY_HTTP:-${BUILD_PROXY_HTTPS:-}}"
    [[ "$cand" != "off" ]] && hpp="$cand"
  fi
  if [[ -z "$hp" && -n "$DEFAULT_FALLBACK_PROXY" ]]; then
    hp="$DEFAULT_FALLBACK_PROXY"
  fi
  if [[ -n "$hp" ]]; then
    export HTTPS_PROXY="$hp" https_proxy="$hp"
  fi
  if [[ -n "$hpp" ]]; then
    export HTTP_PROXY="$hpp" http_proxy="$hpp"
  fi
  local np="${NO_PROXY:-localhost,127.0.0.1,::1}"
  case ",$np," in
    *",localhost,"*) ;;
    *) np="localhost,$np" ;;
  esac
  case ",$np," in
    *",127.0.0.1,"*) ;;
    *) np="127.0.0.1,$np" ;;
  esac
  export NO_PROXY="$np" no_proxy="$np"
}

# ── Drive 폴백 ─────────────────────────────────────────────────────────────
# 1차 소스(Docker Hub/PyPI/GitHub)가 막혀도, 서버가 닿는 Google Drive(rclone)에서
# 아티팩트를 받아 "어쨌거나" 돌아가게 한다. $HEAX_DRIVE_REMOTE/latest/<fn> → dest.
# rclone 미설치 / 리모트 미설정 / 파일 없음 → 조용히 실패(return 1)로 다음 폴백 진행.
drive_fetch() {
  local fn="$1" dest="$2"
  command -v rclone >/dev/null 2>&1 || return 1
  local remote="${HEAX_DRIVE_REMOTE:-}"
  [[ -n "$remote" ]] || return 1
  remote="${remote%/}"
  mkdir -p "$(dirname "$dest")"
  rclone copyto "${remote}/latest/${fn}" "$dest" >/dev/null 2>&1 && [[ -s "$dest" ]]
}

# ── 호스트 IP 감지 ─────────────────────────────────────────────────────────
detect_host_ip() {
  local ip
  ip="$(timeout 2 hostname -I 2>/dev/null | awk '{print $1}')"
  if [[ -z "$ip" || ! "$ip" =~ ^[0-9.]+$ ]]; then
    ip="127.0.0.1"
  fi
  echo "$ip"
}

# ── 디렉터리 보장 ──────────────────────────────────────────────────────────
ensure_dirs() {
  mkdir -p "$VAR_DIR" "$LOG_DIR" \
           "$VAR_DIR/pg" "$VAR_DIR/pg_run" \
           "$VAR_DIR/redis" "$VAR_DIR/mailhog" \
           "$VAR_DIR/caddy"
}

# ── Python venv 보장 ──────────────────────────────────────────────────────
# 오프라인 휠 디렉터리(WHEELS_DIR)가 있으면 거기서 설치, 아니면 PyPI.
require_python_venv() {
  local venv="$BACKEND_DIR/.venv"
  if [[ -x "$venv/bin/uvicorn" ]]; then
    note "backend venv 준비 완료 ($venv)"
    return 0
  fi
  step "백엔드 venv 생성"
  resolve_python || true
  # vendored standalone python(.tools) 이 아직 없으면 설치 시도 — cache 타르볼이
  # 있으면 즉시 추출, 없고 uv/네트워크가 있으면 받아서. 모두 실패하면 시스템
  # python 으로 폴백(dev 박스 호환). 성공하면 호스트 python 의존이 사라진다.
  if [[ -z "${_HEAX_PY:-}" || "${_HEAX_PY}" != "$TOOLS_DIR"/* ]]; then
    if [[ -x "$APPT_DIR/install-python.sh" ]]; then
      bash "$APPT_DIR/install-python.sh" >/dev/null 2>&1 && resolve_python || true
    fi
  fi
  [[ -n "${_HEAX_PY:-}" ]] || { err "python 바이너리 미발견 — bash deploy/apptainer/install-python.sh"; exit 1; }
  local py="$_HEAX_PY"
  note "venv base python: $py ($("$py" --version 2>&1))"
  "$py" -m venv "$venv"
  local pip="$venv/bin/pip"
  "$pip" install --upgrade pip wheel setuptools >/dev/null
  if [[ -n "${WHEELS_DIR:-}" && -d "$WHEELS_DIR" ]]; then
    ok "오프라인 휠 사용: $WHEELS_DIR"
    "$pip" install --no-index --find-links "$WHEELS_DIR" -e "$BACKEND_DIR[dev]" \
      || "$pip" install --no-index --find-links "$WHEELS_DIR" -e "$BACKEND_DIR"
  else
    # prod 는 [dev] 불필요 — dev 의존성 설치 실패가 전체 셋업을 막지 않게 폴백.
    "$pip" install -e "$BACKEND_DIR[dev]" || "$pip" install -e "$BACKEND_DIR"
  fi
}

require_disk() {
  local need_gb="${1:-3}"
  local avail_gb
  avail_gb=$(df -BG --output=avail "$ROOT_DIR" 2>/dev/null | tail -1 | tr -dc '0-9')
  if [[ -n "$avail_gb" && "$avail_gb" -lt "$need_gb" ]]; then
    err "디스크 부족: $avail_gb GB 가용 < $need_gb GB 필요"
    exit 1
  fi
  note "디스크 가용: ${avail_gb}G (>= ${need_gb}G)"
}

require_port_free() {
  local port="$1" purpose="${2:-service}"
  if ss -tln 2>/dev/null | awk '{print $4}' | grep -qE ":${port}\$"; then
    err "포트 $port 가 이미 점유됨 ($purpose). 다른 프로세스를 종료하거나 포트를 바꾸세요."
    return 1
  fi
  return 0
}

# ── 인스턴스 헬퍼 ──────────────────────────────────────────────────────────
instance_running() {
  local name="$1"
  apptainer instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$name"
}
