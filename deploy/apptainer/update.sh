#!/usr/bin/env bash
# update.sh — 새 버전 적용 + 재시작 한 방.
#
# 흐름: (옵션)git pull → dist-from-drive(Drive 아티팩트 수령) → 재시작 → verify-deploy.
# "새 버전 자동 적용+재시작"을 한 명령으로 묶는다. systemd timer/webhook 으로
# 주기 실행하면 무인 업데이트가 된다.
#
# 사용:
#   bash deploy/apptainer/update.sh                # Drive 수령 → 재시작 → 검증
#   bash deploy/apptainer/update.sh --pull         # git pull 까지
#   bash deploy/apptainer/update.sh --base https://hwax.sec.samsung.net/heax-hub
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
APPT_DIR="$ROOT_DIR/deploy/apptainer"
# shellcheck source=/dev/null
source "$APPT_DIR/_common.sh"
load_env 2>/dev/null || true

DO_PULL=0
BASE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --pull) DO_PULL=1; shift ;;
    --base) BASE="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) err "unknown arg: $1"; exit 2 ;;
  esac
done

step "1) 소스/아티팩트 갱신"
if [[ $DO_PULL -eq 1 ]]; then
  git pull --ff-only 2>&1 | tail -2 || warn "git pull 실패 — 무시하고 진행(로컬 코드 사용)"
fi
if [[ -n "${HEAX_DRIVE_REMOTE:-}" ]] && command -v rclone >/dev/null 2>&1; then
  bash "$APPT_DIR/dist-from-drive.sh" || warn "dist-from-drive 실패 — 기존 dist 유지"
else
  note "HEAX_DRIVE_REMOTE/rclone 없음 — Drive 수령 생략(로컬 dist 사용)"
fi

step "2) 재시작"
if systemctl --user list-unit-files heaxhub.service >/dev/null 2>&1; then
  systemctl --user restart heaxhub.service && ok "systemctl --user restart heaxhub.service"
else
  note "heaxhub.service 미설치 — stop.sh + start.sh"
  bash "$APPT_DIR/stop.sh" || true
  bash "$APPT_DIR/start.sh"
fi

step "3) 배포 검증 (verify-deploy)"
sleep 3
if [[ -n "$BASE" ]]; then
  bash "$APPT_DIR/verify-deploy.sh" "$BASE"
else
  bash "$APPT_DIR/verify-deploy.sh"
fi
