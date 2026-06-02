#!/usr/bin/env bash
# 부팅 후 HEAXHub 스택을 자동 기동하는 user-level systemd 유닛을 설치.
#
# root 권한 불필요. ~/.config/systemd/user/ 에 두 유닛이 깔린다:
#   - heaxhub.service  : 부팅(또는 사용자 로그인) 시 deploy/apptainer/start.sh 실행
#   - heaxhub-watchdog.timer / .service : 1분마다 헬스체크 + 다운 시 자동 복구
#
# loginctl enable-linger로 user systemd가 부팅 직후부터 동작하도록 활성화.
#
# 사용법:
#   bash install_autostart.sh        # 설치
#   bash install_autostart.sh status # 상태 확인
#   bash install_autostart.sh logs   # 로그 보기
#   bash install_autostart.sh uninstall
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_NAME="$(id -un)"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
ACTION="${1:-install}"

write_unit() {
  local name="$1"; shift
  mkdir -p "$SYSTEMD_USER_DIR"
  cat > "${SYSTEMD_USER_DIR}/${name}" <<EOF
$(cat)
EOF
  echo "  ✓ wrote ${SYSTEMD_USER_DIR}/${name}"
}

user_systemd_ok() {
  # systemctl --user 가 실제로 동작하는지 확인. SSH 세션 + 일부 머신에서는
  # dbus / XDG_RUNTIME_DIR 가 없어서 실패한다.
  systemctl --user --no-pager daemon-reload >/dev/null 2>&1
}

cmd_install_cron() {
  echo "→ user systemd 가 안 되는 환경 — crontab fallback 사용"
  local cron_file
  cron_file="$(mktemp)"
  (crontab -l 2>/dev/null | grep -v "# HEAXHub-autostart" || true) > "$cron_file"
  cat >> "$cron_file" <<EOF
@reboot cd ${ROOT} && /usr/bin/env bash ${ROOT}/deploy/apptainer/start.sh >> ${ROOT}/var/logs/cron-start.log 2>&1   # HEAXHub-autostart
* * * * * cd ${ROOT} && /usr/bin/env bash ${ROOT}/scripts/watchdog.sh >> ${ROOT}/var/logs/watchdog.log 2>&1   # HEAXHub-autostart
EOF
  crontab "$cron_file"
  rm -f "$cron_file"
  echo "  ✓ crontab 설치됨 (현재 사용자: $USER_NAME)"
  echo "    @reboot              → start.sh"
  echo "    * * * * *  (매분)    → scripts/watchdog.sh"
  echo
  echo "  주의: 부팅 직후 자동 시작은 cron daemon이 살아 있을 때만 동작합니다."
  echo "        대부분의 리눅스 배포에서는 기본 설정으로 OK."
  echo
  echo "  현재 crontab 확인: crontab -l | grep HEAXHub-autostart"
}

cmd_install() {
  echo "→ HEAXHub autostart 설치"
  echo "  project root: $ROOT"
  echo "  user        : $USER_NAME"

  if ! user_systemd_ok; then
    cmd_install_cron
    echo
    echo "─────────────────────────────────────────────"
    echo " 설치 완료 (cron 모드)"
    echo "─────────────────────────────────────────────"
    return 0
  fi

  # 1. 메인 service — 부팅 시 stack start
  write_unit heaxhub.service <<EOF
[Unit]
Description=HEAXHub local stack (postgres + redis + mailhog + caddy + backend + worker + beat + frontend)
After=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${ROOT}
ExecStart=/usr/bin/env bash ${ROOT}/deploy/apptainer/start.sh
ExecStop=/usr/bin/env bash ${ROOT}/deploy/apptainer/stop.sh
TimeoutStartSec=300
StandardOutput=append:${ROOT}/var/logs/systemd-start.log
StandardError=append:${ROOT}/var/logs/systemd-start.log

[Install]
WantedBy=default.target
EOF

  # 2. Watchdog service — 헬스체크 + 다운 시 복구
  write_unit heaxhub-watchdog.service <<EOF
[Unit]
Description=HEAXHub watchdog — health probe + auto-recover
After=heaxhub.service

[Service]
Type=oneshot
WorkingDirectory=${ROOT}
ExecStart=/usr/bin/env bash ${ROOT}/scripts/watchdog.sh
StandardOutput=append:${ROOT}/var/logs/watchdog.log
StandardError=append:${ROOT}/var/logs/watchdog.log
EOF

  # 3. Watchdog timer — 1분마다
  write_unit heaxhub-watchdog.timer <<EOF
[Unit]
Description=Run HEAXHub watchdog every minute

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
Unit=heaxhub-watchdog.service

[Install]
WantedBy=timers.target
EOF

  echo
  echo "→ daemon-reload"
  systemctl --user daemon-reload

  echo "→ enable + start heaxhub.service"
  systemctl --user enable --now heaxhub.service

  echo "→ enable + start watchdog timer"
  systemctl --user enable --now heaxhub-watchdog.timer

  # loginctl linger 활성화 (사용자가 로그아웃해도 systemd 살아있게)
  if ! loginctl show-user "$USER_NAME" 2>/dev/null | grep -q "Linger=yes"; then
    if sudo -n true 2>/dev/null; then
      echo "→ enable lingering (sudo 자동)"
      sudo loginctl enable-linger "$USER_NAME"
    else
      echo
      echo "  ⚠ user lingering 이 비활성. 부팅 직후 자동 시작이 필요하면 다음을 1회 실행:"
      echo "      sudo loginctl enable-linger $USER_NAME"
      echo "    안 해도 사용자 로그인 시점에는 자동 시작됩니다."
    fi
  else
    echo "  ✓ lingering already enabled"
  fi

  echo
  echo "─────────────────────────────────────────────"
  echo " 설치 완료"
  echo "─────────────────────────────────────────────"
  cmd_status
}

cmd_status() {
  if user_systemd_ok; then
    echo "→ systemctl --user status heaxhub.service"
    systemctl --user status heaxhub.service --no-pager 2>&1 | head -10 || true
    echo
    echo "→ systemctl --user status heaxhub-watchdog.timer"
    systemctl --user status heaxhub-watchdog.timer --no-pager 2>&1 | head -10 || true
  else
    echo "→ cron 항목"
    crontab -l 2>/dev/null | grep "HEAXHub-autostart" || echo "  (cron entries not found)"
    echo
    echo "→ watchdog 로그 (최근 5줄)"
    tail -5 var/logs/watchdog.log 2>&1 || echo "  (no log yet)"
  fi
  echo
  echo "→ 컴포넌트 LISTEN 상태"
  for p in 4040 4173 5732 6479 8125 8126 4180; do
    if ss -tln 2>/dev/null | grep -q ":$p "; then echo "  $p OK"; else echo "  $p DOWN"; fi
  done
}

cmd_logs() {
  if user_systemd_ok; then
    echo "→ heaxhub.service 로그"
    journalctl --user -u heaxhub.service -n 30 --no-pager 2>&1 | head -30 || \
      tail -30 var/logs/systemd-start.log 2>&1 || echo "  (no log yet)"
    echo
    echo "→ watchdog 로그"
    journalctl --user -u heaxhub-watchdog.service -n 30 --no-pager 2>&1 | head -30 || \
      tail -30 var/logs/watchdog.log 2>&1 || echo "  (no log yet)"
  else
    echo "→ cron-start.log (최근 30줄)"
    tail -30 var/logs/cron-start.log 2>&1 || echo "  (no log yet)"
    echo
    echo "→ watchdog.log (최근 30줄)"
    tail -30 var/logs/watchdog.log 2>&1 || echo "  (no log yet)"
  fi
}

cmd_uninstall() {
  echo "→ 자동시작 해제"
  # systemd unit
  if user_systemd_ok; then
    systemctl --user disable --now heaxhub-watchdog.timer 2>/dev/null || true
    systemctl --user disable --now heaxhub.service 2>/dev/null || true
    systemctl --user daemon-reload 2>/dev/null || true
  fi
  rm -f "${SYSTEMD_USER_DIR}/heaxhub.service" \
        "${SYSTEMD_USER_DIR}/heaxhub-watchdog.service" \
        "${SYSTEMD_USER_DIR}/heaxhub-watchdog.timer" 2>/dev/null || true
  # cron
  if crontab -l 2>/dev/null | grep -q "# HEAXHub-autostart"; then
    crontab -l 2>/dev/null | grep -v "# HEAXHub-autostart" | crontab -
    echo "  ✓ crontab 항목 제거"
  fi
  echo "  ✓ 제거 완료. 인프라(Apptainer 인스턴스)는 그대로 동작합니다."
  echo "    완전히 끄려면: bash deploy/apptainer/stop.sh"
}

case "$ACTION" in
  install)   cmd_install ;;
  status)    cmd_status ;;
  logs)      cmd_logs ;;
  uninstall) cmd_uninstall ;;
  *)
    echo "Usage: bash install_autostart.sh [install|status|logs|uninstall]"
    exit 1 ;;
esac
