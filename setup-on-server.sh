#!/usr/bin/env bash
# setup-on-server.sh — 실 서버(포털 호스트) 한방 셋업.
#
# 전제(OS 레벨 딱 2가지):
#   1) unprivileged user namespaces 가 켜진 리눅스 커널
#      (sysctl kernel.unprivileged_userns_clone = 1; Ubuntu 24.04 기본 ON)
#   2) rclone 설치 + Drive 리모트 설정, 그리고 .env 의 HEAX_DRIVE_REMOTE
#      (Docker Hub/PyPI/GitHub 가 막혀도 Drive 폴백으로 돌아가게 하는 핵심)
#
# 흐름:
#   0) 사전 점검(커널 userns / dpkg-deb / .env / rclone+리모트)
#   1) Drive 에서 아티팩트 수령 — dist-from-drive.sh
#        런타임(apptainer.deb/python.tar.gz)→cache/, 서비스 SIF+base SIF→~/serviceApptainers,
#        frontend dist→frontend/dist
#   2) 한방 셋업 + 기동 — install_all.sh
#        .tools 로 apptainer·python 추출(cache 없으면 Drive 폴백) → venv(vendored python)
#        → alembic → admin seed → 인스턴스/백엔드 기동
#   3) 검증(/health)
#
# 사용:
#   bash setup-on-server.sh                # 기본(Drive 수령 → 셋업 → 검증)
#   bash setup-on-server.sh --skip-drive   # 아티팩트가 이미 로컬이면 Drive 수령 생략
#   bash setup-on-server.sh --skip-build-frontend  # frontend dist 없이 진행(install_all 로 전달)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
APPT_DIR="$ROOT_DIR/deploy/apptainer"
# shellcheck source=/dev/null
source "$APPT_DIR/_common.sh"
load_env 2>/dev/null || true

SKIP_DRIVE=0
PASSTHRU=()
for a in "$@"; do
  case "$a" in
    --skip-drive)           SKIP_DRIVE=1 ;;
    --skip-build-frontend)  PASSTHRU+=("--skip-build-frontend") ;;
    -h|--help)              sed -n '2,30p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *)                      err "unknown arg: $a"; exit 2 ;;
  esac
done

echo "================================================================"
echo " HEAXHub — 실 서버 셋업"
echo "  repo  : $ROOT_DIR"
echo "  drive : ${HEAX_DRIVE_REMOTE:-<unset>}"
echo "================================================================"

# ── 0) 사전 점검 ─────────────────────────────────────────────────────────────
step "0) 사전 점검"
USERNS="$(cat /proc/sys/kernel/unprivileged_userns_clone 2>/dev/null || echo 1)"
if [[ "$USERNS" == "0" ]]; then
  warn "kernel.unprivileged_userns_clone=0 — 비특권 apptainer 인스턴스 기동 불가."
  warn "  관리자에게 'sysctl -w kernel.unprivileged_userns_clone=1' 요청(또는 setuid apptainer)."
else
  ok "userns OK (unprivileged_userns_clone=$USERNS)"
fi
command -v dpkg-deb >/dev/null 2>&1 && ok "dpkg-deb OK" \
  || warn "dpkg-deb 없음 — apptainer .deb 추출 불가(보통 base 시스템에 포함)"

if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    cp .env.example .env
    warn ".env 자동생성(.env.example 복사) — JWT_SECRET/SECRET_ENCRYPTION_KEY 는 install_all 가 회전"
    load_env 2>/dev/null || true
  else
    err ".env / .env.example 둘 다 없음"; exit 1
  fi
fi

if [[ $SKIP_DRIVE -eq 0 ]]; then
  command -v rclone >/dev/null 2>&1 \
    || { err "rclone 없음 — https://rclone.org/install/ 설치 후 'rclone config' 로 리모트 설정, 또는 --skip-drive"; exit 1; }
  [[ -n "${HEAX_DRIVE_REMOTE:-}" ]] \
    || { err "HEAX_DRIVE_REMOTE 미설정(.env). 예: HEAX_DRIVE_REMOTE=ApptainerImages:HEAXHub/dist (또는 --skip-drive)"; exit 1; }
  ok "rclone + HEAX_DRIVE_REMOTE=$HEAX_DRIVE_REMOTE"
fi

# ── 1) Drive 수령 ────────────────────────────────────────────────────────────
if [[ $SKIP_DRIVE -eq 0 ]]; then
  step "1) Drive 에서 아티팩트 수령 (dist-from-drive)"
  bash "$APPT_DIR/dist-from-drive.sh"
else
  note "1) --skip-drive — Drive 수령 생략(아티팩트가 이미 로컬이라고 가정)"
fi

# ── 2) 한방 셋업 + 기동 ──────────────────────────────────────────────────────
step "2) install_all (런타임 추출 → venv → migrate → 기동)"
bash "$APPT_DIR/install_all.sh" "${PASSTHRU[@]}"

# ── 3) 검증 ──────────────────────────────────────────────────────────────────
step "3) 검증"
sleep 2
if curl -fsS "http://127.0.0.1:${APP_PORT:-4040}/health" >/dev/null 2>&1; then
  ok "backend /health OK"
else
  warn "/health 응답 없음 — tail -f $LOG_DIR/{backend,worker,beat}.log"
fi

HOST_IP="$(detect_host_ip)"
echo
ok "실 서버 셋업 완료"
echo "  Web : http://${HOST_IP}:${PUBLIC_PORT:-4180}/"
echo "  API : http://${HOST_IP}:${APP_PORT:-4040}/docs"
echo
note "앱 빌드: 제출 프로젝트는 서버에서 SIF 로 빌드된다. base 레이어는 로컬 base_*.sif(Drive 수령분)"
note "  이라 Docker Hub 무관. 단, 앱 자체 deps(pip/npm)는 빌드 시점 레지스트리가 필요(egress 또는 사내 미러)."
