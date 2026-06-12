#!/usr/bin/env bash
#
# register-from-csv.sh — CSV 한 장을 읽어 HEAXHub 카탈로그에 URL 항목을 일괄 등록.
#
# CSV 포맷 (첫 줄은 헤더이므로 무시, 둘째 줄부터 읽음):
#   그룹,파트명,Agent이름,프로그램이름,URL
#
# 행마다:
#   - Agent이름 채워짐    → tags: [agent]   분류로 등록
#   - 프로그램이름 채워짐  → tags: [program] 분류로 등록
#   (둘 중 하나에만 이름이 있다고 가정. 둘 다 비면 그 행은 건너뜀)
#   - 마지막 열 URL       → launch.mode=url (새 탭 링크)
#   - 그룹/파트명         → tags 에 추가 + description 에 "소속: <그룹> / <파트>" 자동 기입
#
# 모든 항목은 빌드 없이 external_link 로 등록된다(클릭 시 새 탭).
#
# 사용법:
#   scripts/register-from-csv.sh <csv파일> [--scan] [--dry-run]
#     --scan     : 등록 후 스캔 1회 트리거(즉시 카탈로그 반영). 생략하면 5분 주기 대기.
#     --dry-run  : 매니페스트를 만들지 않고 무엇을 등록할지 미리보기만.
#
# 예:
#   scripts/register-from-csv.sh agents.csv --scan
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INTEGRATIONS_DIR="$REPO_ROOT/integrations"

CSV=""
DO_SCAN=0
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --scan)    DO_SCAN=1 ;;
    --dry-run) DRY_RUN=1 ;;
    *)         CSV="$arg" ;;
  esac
done

if [[ -z "$CSV" ]]; then
  read -rp "CSV 파일 경로: " CSV
fi
if [[ ! -f "$CSV" ]]; then
  echo "오류: CSV 파일을 찾을 수 없습니다: $CSV" >&2
  exit 1
fi

# ── 헬퍼: 문자열 → 안전한 slug (영문 소문자/숫자/하이픈) ──────────────────────
# 한글/공백/특수문자는 모두 하이픈으로 치환하고, 한글이 남으면 ascii 가 안 되므로
# 해당 글자는 제거한다. 결과가 비면 인덱스로 대체한다.
slugify() {
  local s="$1"
  s="$(echo "$s" | tr '[:upper:]' '[:lower:]')"
  # 영문/숫자만 남기고 나머지는 하이픈
  s="$(echo "$s" | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//')"
  echo "$s"
}

# ── 헬퍼: YAML 스칼라 안전 인용 (큰따옴표 escape) ────────────────────────────
yq() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }

CREATED=0
SKIPPED=0
ROWNUM=0

# CSV 파싱: 따옴표 없는 단순 CSV 가정(쉼표가 값에 없음).
# 첫 줄(헤더) 무시. \r 제거(윈도우 CSV 대비).
{
  read -r _header || true   # 헤더 버림
  while IFS=',' read -r GROUP PART AGENT_NAME PROG_NAME URL _rest || [[ -n "${GROUP:-}" ]]; do
    ROWNUM=$((ROWNUM + 1))
    # 각 필드 trim + CR 제거
    GROUP="$(echo "${GROUP:-}" | tr -d '\r' | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
    PART="$(echo "${PART:-}" | tr -d '\r' | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
    AGENT_NAME="$(echo "${AGENT_NAME:-}" | tr -d '\r' | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
    PROG_NAME="$(echo "${PROG_NAME:-}" | tr -d '\r' | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
    URL="$(echo "${URL:-}" | tr -d '\r' | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"

    # 완전 빈 줄 건너뜀
    if [[ -z "$GROUP$PART$AGENT_NAME$PROG_NAME$URL" ]]; then
      continue
    fi

    # 이름 결정: agent vs program (둘 중 하나)
    KIND=""; DISPLAY=""
    if [[ -n "$AGENT_NAME" ]]; then
      KIND="agent"; DISPLAY="$AGENT_NAME"
    elif [[ -n "$PROG_NAME" ]]; then
      KIND="program"; DISPLAY="$PROG_NAME"
    else
      echo "  [행 $ROWNUM] 건너뜀: Agent이름/프로그램이름 둘 다 비어 있음"
      SKIPPED=$((SKIPPED + 1))
      continue
    fi

    # URL 검증
    if [[ -z "$URL" ]]; then
      echo "  [행 $ROWNUM] 건너뜀: URL 없음 ($DISPLAY)"
      SKIPPED=$((SKIPPED + 1))
      continue
    fi
    if [[ ! "$URL" =~ ^https?:// ]]; then
      URL="http://$URL"
    fi

    # slug: <kind>-<group>-<display>. 한글이 ascii 변환에서 사라지면 행번호로 보강.
    NAME_SLUG="$(slugify "$DISPLAY")"
    GROUP_SLUG="$(slugify "$GROUP")"
    BASE_SLUG="$KIND"
    [[ -n "$GROUP_SLUG" ]] && BASE_SLUG="$BASE_SLUG-$GROUP_SLUG"
    [[ -n "$NAME_SLUG" ]] && BASE_SLUG="$BASE_SLUG-$NAME_SLUG"
    # 한글뿐이라 slug 가 너무 짧으면 행번호 부여
    if [[ "$BASE_SLUG" == "$KIND" || "$BASE_SLUG" == "$KIND-" ]]; then
      BASE_SLUG="$KIND-row$ROWNUM"
    fi
    SLUG="$BASE_SLUG"
    APP_ID="$(echo "$SLUG" | tr '-' '_')"

    # 중복이면 -2, -3 ... 부여
    n=2
    while [[ -e "$INTEGRATIONS_DIR/$SLUG/.portal/manifest.yaml" ]]; do
      SLUG="$BASE_SLUG-$n"
      APP_ID="$(echo "$SLUG" | tr '-' '_')"
      n=$((n + 1))
    done

    # 소속 설명 자동 생성
    OWNER_DESC=""
    if [[ -n "$GROUP" && -n "$PART" ]]; then
      OWNER_DESC="소속: $GROUP / $PART"
    elif [[ -n "$GROUP" ]]; then
      OWNER_DESC="소속: $GROUP"
    elif [[ -n "$PART" ]]; then
      OWNER_DESC="소속: $PART"
    fi

    # tags: kind + group + part (빈 값 제외)
    TAGS="\"$KIND\""
    [[ -n "$GROUP" ]] && TAGS="$TAGS, \"$(yq "$GROUP")\""
    [[ -n "$PART"  ]] && TAGS="$TAGS, \"$(yq "$PART")\""

    if [[ "$DRY_RUN" == "1" ]]; then
      printf "  [행 %s] %-7s id=%-28s name=%-20s url=%s\n" \
        "$ROWNUM" "$KIND" "$APP_ID" "$DISPLAY" "$URL"
      CREATED=$((CREATED + 1))
      continue
    fi

    DEST="$INTEGRATIONS_DIR/$SLUG/.portal"
    mkdir -p "$DEST"
    cat > "$DEST/manifest.yaml" <<YAML
schema_version: 2
id: $APP_ID
name: "$(yq "$DISPLAY")"
owner: ""                # TODO: 담당자
status: stable
app_type: external_link
execution_target: external_url
description: "$(yq "$OWNER_DESC")"   # TODO: 상세 설명 추가 가능
build:
  stack: external_link
launch:
  mode: url
  url: $URL
  open_in: new_tab
tags: [$TAGS]
permissions:
  visibility: company
YAML
    printf "  [행 %s] 등록 %-7s id=%-28s name=%s\n" "$ROWNUM" "$KIND" "$APP_ID" "$DISPLAY"
    CREATED=$((CREATED + 1))
  done
} < "$CSV"

echo
if [[ "$DRY_RUN" == "1" ]]; then
  echo "■ dry-run: $CREATED 건이 등록될 예정 (건너뜀 $SKIPPED). 실제 등록하려면 --dry-run 빼고 실행."
  exit 0
fi
echo "■ 등록 완료: $CREATED 건 (건너뜀 $SKIPPED)"

if [[ "$DO_SCAN" == "1" ]]; then
  echo "▶ 스캔 트리거 중 (카탈로그 즉시 반영)..."
  ( cd "$REPO_ROOT/backend" && \
    set -a && . "$REPO_ROOT/.env" && set +a && \
    .venv/bin/python -c 'from app.workers.integration_tasks import scan_integrations_periodic as s; print("  스캔 결과:", s()["by_action"])' )
else
  echo "▶ 5분 주기 스캔을 기다리거나 즉시 반영하려면:"
  echo "     cd $REPO_ROOT/backend && .venv/bin/python -c \\"
  echo "       'from app.workers.integration_tasks import scan_integrations_periodic as s; print(s()[\"by_action\"])'"
fi
echo "  (이름/설명/담당자는 integrations/<slug>/.portal/manifest.yaml 에서 나중에 수정 가능)"
