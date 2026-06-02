#!/usr/bin/env bash
# HEAXHub — Host bootstrap (Ubuntu 24.04 LTS, online or offline).
#
# 이 스크립트는 HEAXHub 가 깔리기 위해 호스트에 필요한 시스템 의존성을 설치한다:
#   - 기본: git curl make build-essential ca-certificates gnupg lsb-release pipx
#   - python3.12 + python3.12-venv
#   - nodejs 20 + pnpm 9  (개발/빌드용. 오프라인 dist-only 운영은 생략 가능)
#   - apptainer 1.3.x  (deploy/apptainer/install-apptainer.sh 호출로 .tools/ 추출)
#
# 두 가지 모드:
#   ONLINE  — apt / NodeSource / npm / pip 직접 사용 (프록시 자동 적용)
#   OFFLINE — infra/packages/{deb,npm,pip}/ 에 사전 staging 한 자산 사용
#
# 사용:
#   sudo bash scripts/bootstrap-host.sh                 # auto-detect
#   sudo bash scripts/bootstrap-host.sh --offline       # 강제 오프라인
#   sudo bash scripts/bootstrap-host.sh --online        # 강제 온라인
#   sudo bash scripts/bootstrap-host.sh --dry-run       # 무엇이 실행될지만 출력
#   sudo -E bash scripts/bootstrap-host.sh              # 프록시 환경변수 전달
#   sudo bash scripts/bootstrap-host.sh --proxy http://proxy:8080
#
# 폴백 프록시: HEAXHUB_FALLBACK_PROXY (기본 비어있음. 설정 시 1차 실패 후 재시도)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG_DIR="$REPO_ROOT/infra/packages"
DEB_DIR="$PKG_DIR/deb"
NPM_DIR="$PKG_DIR/npm"
PIP_DIR="$PKG_DIR/pip"

if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_BLUE=$'\033[1;34m'; C_GREEN=$'\033[1;32m'
  C_YELLOW=$'\033[1;33m'; C_RED=$'\033[1;31m'; C_DIM=$'\033[2m'
else
  C_RESET=""; C_BLUE=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_DIM=""
fi
step() { printf "\n${C_BLUE}▶ %s${C_RESET}\n" "$1"; }
ok()   { printf "  ${C_GREEN}✓${C_RESET} %s\n" "$*"; }
warn() { printf "  ${C_YELLOW}!${C_RESET} %s\n" "$*"; }
fail() { printf "  ${C_RED}✗${C_RESET} %s\n" "$*"; exit 1; }
note() { printf "  ${C_DIM}%s${C_RESET}\n" "$*"; }

MODE="auto"
DRY_RUN=0
PROXY_ARG=""
SKIP_NODE=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --online)    MODE="online" ;;
    --offline)   MODE="offline" ;;
    --dry-run)   DRY_RUN=1 ;;
    --skip-node) SKIP_NODE=1 ;;
    --proxy)     [ -n "${2:-}" ] || fail "--proxy 인자가 빠졌습니다."; PROXY_ARG="$2"; shift ;;
    --proxy=*)   PROXY_ARG="${1#--proxy=}" ;;
    -h|--help)   sed -n '2,28p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *)           fail "unknown arg: $1 (use --help)" ;;
  esac
  shift
done

PROXY_URL="${PROXY_ARG:-${HTTPS_PROXY:-${HTTP_PROXY:-${https_proxy:-${http_proxy:-}}}}}"
NO_PROXY_VAL="${NO_PROXY:-${no_proxy:-localhost,127.0.0.1,::1}}"
if [ -n "$PROXY_URL" ]; then
  export HTTP_PROXY="$PROXY_URL" HTTPS_PROXY="$PROXY_URL"
  export http_proxy="$PROXY_URL" https_proxy="$PROXY_URL"
  export NO_PROXY="$NO_PROXY_VAL" no_proxy="$NO_PROXY_VAL"
  note "proxy: $PROXY_URL  (no_proxy: $NO_PROXY_VAL)"
fi
FALLBACK_PROXY="${HEAXHUB_FALLBACK_PROXY:-}"

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    note "[dry-run] $*"
  else
    "$@"
  fi
}

# apt/npm 프록시 설정 도우미.
apt_already_has_proxy() {
  apt-config dump 2>/dev/null \
    | grep -qE 'Acquire::https?::Proxy[[:space:]]+"[^"]+"'
}
configure_proxy_for_apt() {
  [ -z "$PROXY_URL" ] && return 0
  if apt_already_has_proxy; then
    note "apt 시스템 프록시 이미 설정됨 — 변경하지 않음"
    return 0
  fi
  local conf=/etc/apt/apt.conf.d/99proxy-heaxhub-bootstrap
  if [ "$DRY_RUN" -eq 1 ]; then
    note "[dry-run] write $conf"
  else
    cat > "$conf" <<EOF
Acquire::http::Proxy "$PROXY_URL";
Acquire::https::Proxy "$PROXY_URL";
EOF
    ok "apt proxy → $conf"
  fi
}
configure_proxy_for_apt

# curl with fallback proxy retry.
curl_with_proxy_fallback() {
  local out="$1" url="$2"; shift 2
  local common=(-fL --retry 6 --retry-delay 5 --retry-all-errors
                --connect-timeout 30 --max-time 600 "$@")
  if curl "${common[@]}" "$url" -o "$out" 2>/tmp/heaxhub-curl.err; then
    return 0
  fi
  if [ -n "$FALLBACK_PROXY" ]; then
    warn "1차 curl 실패. fallback proxy 재시도: $FALLBACK_PROXY"
    if curl "${common[@]}" --proxy "$FALLBACK_PROXY" "$url" -o "$out"; then
      return 0
    fi
  fi
  return 1
}

# .deb 캐시 검색.
find_cached_deb() {
  local prefix="$1"
  local dirs=("$DEB_DIR" "$REPO_ROOT/infra/deb" "$REPO_ROOT/infra/packages" "$REPO_ROOT" "${HEAXHUB_DEB_DIR:-}")
  shopt -s nullglob
  for dir in "${dirs[@]}"; do
    [ -n "$dir" ] && [ -d "$dir" ] || continue
    for f in "$dir/${prefix}"*.deb "$dir/${prefix^}"*.deb; do
      [ -e "$f" ] || continue
      local sz; sz="$(stat -c %s "$f" 2>/dev/null || echo 0)"
      [ "$sz" -ge 1000000 ] || continue
      echo "$f"
      shopt -u nullglob
      return 0
    done
  done
  shopt -u nullglob
  echo ""
}

# ── Sanity ──────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ] && [ "$DRY_RUN" -ne 1 ]; then
  fail "root 권한 필요. 다시: sudo $0"
fi
command -v apt-get >/dev/null || fail "apt-get 없음 — Ubuntu/Debian 전용 스크립트."

. /etc/os-release 2>/dev/null || true
DISTRO_ID="${ID:-unknown}"
DISTRO_VER="${VERSION_ID:-unknown}"
[ "$DISTRO_ID" = "ubuntu" ] || warn "Tested on Ubuntu 24.04. Detected: $DISTRO_ID $DISTRO_VER"

detect_online() {
  apt_already_has_proxy && return 0
  curl -sSf --max-time 5 --head https://archive.ubuntu.com/ubuntu/ >/dev/null 2>&1 && return 0
  curl -sSf --max-time 5 --head https://deb.nodesource.com/ >/dev/null 2>&1 && return 0
  return 1
}
if [ "$MODE" = "auto" ]; then
  if detect_online; then MODE="online"; note "auto: ONLINE"; else MODE="offline"; note "auto: OFFLINE"; fi
fi
[ "$MODE" = "offline" ] && [ ! -d "$DEB_DIR" ] && fail "offline 모드인데 $DEB_DIR 가 없습니다. infra/packages/deb/README.md 참고."

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH}"

have_version() {
  local cmd="$1" min="$2" bin=""
  if command -v "$cmd" >/dev/null 2>&1; then bin="$(command -v "$cmd")"
  else for cand in /usr/bin/$cmd /usr/local/bin/$cmd /opt/$cmd/bin/$cmd; do
    [ -x "$cand" ] && bin="$cand" && break
  done; fi
  [ -z "$bin" ] && return 1
  local major; major="$("$bin" --version 2>&1 | head -1 | grep -oE '[0-9]+' | head -1)"
  [ -n "$major" ] && [ "$major" -ge "$min" ]
}

# ── Step 1: base apt ────────────────────────────────────────────────────
step "Step 1 — base packages"
NEED_APT=()
for pkg in git curl make build-essential ca-certificates gnupg lsb-release \
           python3.12 python3.12-venv python3-pip software-properties-common \
           pipx fuse2fs uidmap dpkg postgresql-client; do
  if ! dpkg -s "$pkg" >/dev/null 2>&1; then
    NEED_APT+=("$pkg")
  fi
done
if [ "${#NEED_APT[@]}" -eq 0 ]; then
  ok "base packages 이미 모두 설치됨"
elif [ "$MODE" = "online" ]; then
  ok "apt install: ${NEED_APT[*]}"
  run timeout --foreground 120 apt-get update -y \
    || warn "apt-get update 타임아웃 — install 시도는 계속"
  run apt-get install -y --no-install-recommends "${NEED_APT[@]}"
else
  ok "오프라인 .deb 설치: $DEB_DIR"
  run apt-get install -y --no-install-recommends "$DEB_DIR"/*.deb || true
  run apt-get install -y -f
fi

# ── Step 2: Apptainer (.tools/ prefix, install-apptainer.sh 위임) ──────
step "Step 2 — apptainer (project-local prefix)"
if [ "$DRY_RUN" -eq 0 ]; then
  run bash "$REPO_ROOT/deploy/apptainer/install-apptainer.sh"
else
  note "[dry-run] bash deploy/apptainer/install-apptainer.sh"
fi

# ── Step 3: Node.js 20 ─────────────────────────────────────────────────
if [ "$SKIP_NODE" -eq 1 ]; then
  step "Step 3 — node (skipped)"
elif have_version node 20; then
  step "Step 3 — node"; ok "이미 설치됨: $(node --version)"
else
  step "Step 3 — node 20"
  cached_deb="$(find_cached_deb nodejs)"
  if [ -n "$cached_deb" ]; then
    ok "cached: $cached_deb"
    run apt-get install -y --no-install-recommends "$cached_deb"
  elif [ "$MODE" = "online" ]; then
    setup_script="$(mktemp --suffix=.sh)"
    if curl_with_proxy_fallback "$setup_script" "https://deb.nodesource.com/setup_20.x"; then
      run bash "$setup_script"; rm -f "$setup_script"
      run apt-get install -y --no-install-recommends nodejs
    else
      rm -f "$setup_script"
      warn "NodeSource 접근 실패 — nodejs.org 타르볼로 폴백"
      node_ver="20.18.1"
      tar_url="https://nodejs.org/dist/v${node_ver}/node-v${node_ver}-linux-x64.tar.xz"
      tar_file="$DEB_DIR/node-v${node_ver}-linux-x64.tar.xz"
      mkdir -p "$DEB_DIR"
      if curl_with_proxy_fallback "$tar_file" "$tar_url"; then
        run tar -xf "$tar_file" -C /usr/local --strip-components=1
      else
        fail "node 설치 실패. 수동으로 nodejs*.deb 또는 node-v*.tar.xz 를 $DEB_DIR 에 두세요."
      fi
    fi
  else
    fail "offline 모드인데 nodejs .deb / tarball 캐시 없음."
  fi
  ok "$(node --version)"
fi

# ── Step 4: pnpm 9 ─────────────────────────────────────────────────────
if [ "$SKIP_NODE" -eq 1 ]; then
  step "Step 4 — pnpm (skipped)"
elif have_version pnpm 9; then
  step "Step 4 — pnpm"; ok "이미 설치됨: $(pnpm --version)"
else
  step "Step 4 — pnpm 9"
  cached_pnpm=""
  shopt -s nullglob
  for dir in "$NPM_DIR" "$DEB_DIR" "$REPO_ROOT/infra/packages" "$REPO_ROOT"; do
    [ -d "$dir" ] || continue
    for f in "$dir"/pnpm-*.tgz; do [ -e "$f" ] && cached_pnpm="$f" && break 2; done
  done
  shopt -u nullglob
  if [ -n "$cached_pnpm" ]; then
    ok "cached: $cached_pnpm"
    run npm install -g "$cached_pnpm"
  elif [ "$MODE" = "online" ]; then
    run npm config set fetch-timeout 30000
    run npm config set fetch-retries 3
    [ -n "$PROXY_URL" ] && run npm config set proxy "$PROXY_URL" \
                       && run npm config set https-proxy "$PROXY_URL"
    if ! run timeout --foreground 90 npm install -g --no-audit --no-fund pnpm@9; then
      if [ -n "$FALLBACK_PROXY" ]; then
        run npm config set proxy "$FALLBACK_PROXY"
        run npm config set https-proxy "$FALLBACK_PROXY"
        run timeout --foreground 90 npm install -g --no-audit --no-fund pnpm@9 \
          || fail "pnpm install 실패 (fallback proxy 포함)."
      else
        fail "pnpm install 실패. HEAXHUB_FALLBACK_PROXY 를 설정하거나 pnpm-*.tgz 를 $NPM_DIR 에 두세요."
      fi
    fi
  else
    fail "offline 모드인데 pnpm-*.tgz 가 $NPM_DIR / 캐시에 없음."
  fi
  ok "$(pnpm --version)"
fi

echo
ok "Bootstrap 완료 (mode: $MODE)"
note "다음 단계: 일반 사용자 계정에서  cd $REPO_ROOT && bash deploy/apptainer/install_all.sh"
