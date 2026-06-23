#!/usr/bin/env bash
# HEAXHub — 앱 빌드 base image 를 로컬 base SIF 로 받아둔다.
#
# config/base_images.yaml 의 docker ref → base_<key>.sif 매핑대로
# `apptainer pull <base_dir>/<sif> docker://<ref>` 를 수행한다.
# 빌더(integration_sif_builder._localize_base_images)가 로컬 base SIF 가 있으면
# 각 stack .def 의 `Bootstrap: docker/From: <ref>` 를 `localimage/<local sif>` 로
# 바꿔 빌드한다 — 없으면 docker:// 폴백(동작 변화 없음).
#
# 목적: 앱 빌드의 "토대(base 레이어)"를 Docker Hub 가용성과 무관하게 로컬에 두어,
#       일부 패키지/네트워크가 흔들려도 빌드 자체는 항상 되게 한다(앱 deps 만 egress).
#
# base 디렉터리: $HEAXHUB_BASE_IMAGE_DIR > ~/serviceApptainers
#   (빌더 base_image_dir() 과 동일 해석. 서비스 SIF 들과 같은 위치.)
#
# 사용:
#   bash deploy/apptainer/pull-base-images.sh             # 누락분만 pull
#   bash deploy/apptainer/pull-base-images.sh --force     # 전부 재-pull
#   bash deploy/apptainer/pull-base-images.sh --only python:3.12-slim
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env 2>/dev/null || true
export_proxy 2>/dev/null || true
require_apptainer

MAP="$ROOT_DIR/config/base_images.yaml"
[[ -f "$MAP" ]] || { err "base_images.yaml 없음: $MAP"; exit 1; }

BASE_DIR="${HEAXHUB_BASE_IMAGE_DIR:-$HOME/serviceApptainers}"
mkdir -p "$BASE_DIR"

FORCE=0
ONLY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --only)  ONLY="$2"; shift 2 ;;
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) err "unknown arg: $1"; exit 2 ;;
  esac
done

step "base image → 로컬 SIF  (dir: $BASE_DIR)"

OK_N=0; SKIP_N=0; FAIL_N=0
# base_images.yaml 의 `"ref":  sif` 매핑 라인만 파싱 (주석 # 라인은 제외).
while IFS=$'\t' read -r ref sif; do
  [[ -n "$ref" && -n "$sif" ]] || continue
  [[ -n "$ONLY" && "$ONLY" != "$ref" ]] && continue
  dst="$BASE_DIR/$sif"
  if [[ -f "$dst" && $FORCE -eq 0 ]]; then
    ok "skip $ref (이미 존재: $sif)"
    SKIP_N=$((SKIP_N+1)); continue
  fi
  note "pull docker://$ref → $sif"
  if apptainer pull --force "$dst" "docker://$ref" >>"$LOG_DIR/pull-base-images.log" 2>&1; then
    ok "$ref → $sif ($(du -h "$dst" 2>/dev/null | cut -f1))"
    OK_N=$((OK_N+1))
  elif drive_fetch "$sif" "$dst"; then
    # Docker Hub 막힘 → 서버가 닿는 Drive 에서 미리 올려둔 base SIF 폴백
    ok "$ref → $sif (Drive 폴백, $(du -h "$dst" 2>/dev/null | cut -f1))"
    OK_N=$((OK_N+1))
  else
    err "pull 실패: $ref (Docker Hub/Drive 모두 실패. 로그: $LOG_DIR/pull-base-images.log)"
    FAIL_N=$((FAIL_N+1))
  fi
done < <(awk -F'"' '/^"/ { ref=$2; rest=$3; sub(/^:[ \t]+/,"",rest); split(rest,a,/[ \t]+/); print ref"\t"a[1] }' "$MAP")

echo
ok "완료 — pulled=$OK_N skipped=$SKIP_N failed=$FAIL_N (dir: $BASE_DIR)"
[[ $FAIL_N -eq 0 ]]
