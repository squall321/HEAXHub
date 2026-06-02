#!/usr/bin/env bash
# HEAXHub — 재부팅 후 한 줄 복구.
#
# install_all.sh 가 새 머신 셋업이라면 boot.sh 는 "재부팅 후 복구" 용도다.
# 데이터(var/pg, var/redis)는 보존되므로:
#   - stale .pid / orphan apptainer state 정리
#   - PG/Redis/MailHog/Caddy 인스턴스 재기동 (없으면 새로)
#   - uvicorn / celery worker / celery beat 기동 (이미 살아 있으면 skip)
#   - /health 검증
#
# crontab @reboot 또는 systemd-user 가 호출하기에 알맞다.
#
# 사용:
#   bash deploy/apptainer/boot.sh
#   bash deploy/apptainer/boot.sh --force        # systemd 가 관리 중이어도 강제
set -euo pipefail

# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

FORCE=0
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) err "unknown arg: $a"; exit 2 ;;
  esac
done

load_env
require_apptainer
ensure_dirs

# systemd-user 가 관리 중이면 차단 (이중 기동 방지)
if [[ $FORCE -eq 0 ]]; then
  if systemctl --user is-active heaxhub.service >/dev/null 2>&1; then
    warn "systemd-user 가 heaxhub.service 를 이미 관리 중. 다음 중 하나:"
    warn "  systemctl --user restart heaxhub.service"
    warn "  bash boot.sh --force   (강제로 직접 실행)"
    exit 1
  fi
fi

echo "================================================================"
echo " HEAXHub — boot (재부팅 복구)"
echo " $(date '+%F %T %Z')"
echo "================================================================"

# ── 1) stale 정리 ─────────────────────────────────────────────────────
step "1/3  stale state 정리"

# apptainer instance state json (재부팅 후 orphan)
APPT_STATE="$HOME/.apptainer/instances"
for inst in heax-pg heax-redis heax-mailhog heax-caddy; do
  if ! instance_running "$inst" 2>/dev/null; then
    if [[ -d "$APPT_STATE" ]]; then
      find "$APPT_STATE" -name "${inst}.json" -exec rm -f {} \; 2>/dev/null \
        && note "stale state 제거: ${inst}.json" || true
    fi
  fi
done

# pid file
for f in "$LOG_DIR"/{backend,worker,beat}.pid; do
  [[ -f "$f" ]] || continue
  pid="$(cat "$f" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$f"; note "stale pid 제거: $f"
  fi
done

# ── 2) start.sh 호출 (모든 인스턴스/프로세스를 idempotent 하게 기동) ──
step "2/3  인스턴스/프로세스 기동"
bash "$APPT_DIR/start.sh"

# ── 3) 검증 ──────────────────────────────────────────────────────────
step "3/3  헬스 검증"
sleep 3
HOST_IP="$(detect_host_ip)"
OK=1
for i in $(seq 1 20); do
  if curl -sf "http://127.0.0.1:${APP_PORT:-4040}/health" >/dev/null; then
    ok "backend /health OK"; OK=1; break
  fi
  sleep 1
  OK=0
done
if [[ $OK -eq 0 ]]; then
  warn "backend /health 응답 없음. var/logs/backend.log 확인."
  exit 1
fi

curl -sf http://127.0.0.1:2019/config/ >/dev/null && ok "caddy admin OK" \
  || warn "caddy admin 응답 없음 — caddy 인스턴스 점검 필요"

echo
ok "boot 완료 — http://${HOST_IP}:${PUBLIC_PORT:-4180}/"
