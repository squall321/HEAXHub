#!/usr/bin/env bash
#
# register-url.sh — 빌드 없이 외부 주소(IP:포트 또는 URL)를 HEAXHub 카탈로그에 등록.
#
# 세 가지 노출 방식(mode):
#   url    : 클릭하면 그 주소를 새 탭으로 연다 (가장 단순한 바로가기).
#   proxy  : /apps/<slug>/ 하위경로로 reverse_proxy (포털 도메인 안으로 흡수).
#   iframe : 포털 페이지 안에 iframe 으로 임베드.
#
# 사용법:
#   scripts/register-url.sh                         # 대화형(질문에 답)
#   scripts/register-url.sh <slug> <addr> [mode]    # 인자 직접
#
# 예:
#   scripts/register-url.sh wiki 10.0.0.5:8080            # mode 기본 url
#   scripts/register-url.sh grafana http://10.0.0.5:3000 proxy
#   scripts/register-url.sh report https://report.intra  iframe
#
# 이름/설명은 일부러 비워 둔다 — 등록 후 매니페스트 파일을 열어 채우면 된다
# (파일 경로는 스크립트가 끝에 출력한다).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INTEGRATIONS_DIR="$REPO_ROOT/integrations"

# ── 입력 수집 ────────────────────────────────────────────────────────────────
SLUG="${1:-}"
ADDR="${2:-}"
MODE="${3:-}"

if [[ -z "$SLUG" ]]; then
  read -rp "slug (영문/숫자/하이픈, 카탈로그 고유 ID): " SLUG
fi
if [[ -z "$ADDR" ]]; then
  read -rp "주소 (IP:포트 또는 http(s)://...): " ADDR
fi
if [[ -z "$MODE" ]]; then
  read -rp "노출 방식 [url|proxy|iframe] (기본 url): " MODE
  MODE="${MODE:-url}"
fi

# ── 검증/정규화 ──────────────────────────────────────────────────────────────
# slug: 소문자/숫자/하이픈만. id 는 언더스코어형(스캐너 규칙과 일치).
SLUG="$(echo "$SLUG" | tr '[:upper:]' '[:lower:]' | tr ' _' '--')"
if [[ ! "$SLUG" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  echo "오류: slug 는 영문 소문자/숫자/하이픈만 가능합니다: '$SLUG'" >&2
  exit 1
fi
APP_ID="$(echo "$SLUG" | tr '-' '_')"

case "$MODE" in
  url|proxy|iframe) ;;
  *) echo "오류: mode 는 url|proxy|iframe 중 하나여야 합니다: '$MODE'" >&2; exit 1 ;;
esac

# 스킴 없으면 http:// 를 붙인다(IP:포트 입력 편의).
if [[ ! "$ADDR" =~ ^https?:// ]]; then
  ADDR="http://$ADDR"
fi

DEST_DIR="$INTEGRATIONS_DIR/$SLUG/.portal"
MANIFEST="$DEST_DIR/manifest.yaml"
if [[ -e "$MANIFEST" ]]; then
  echo "오류: 이미 존재합니다: $MANIFEST" >&2
  echo "      (수정하려면 파일을 직접 열어 편집하세요.)" >&2
  exit 1
fi
mkdir -p "$DEST_DIR"

# ── mode 별 stack / launch 블록 ─────────────────────────────────────────────
case "$MODE" in
  url)
    STACK="external_link"
    LAUNCH="launch:
  mode: url
  url: $ADDR
  open_in: new_tab"
    ;;
  iframe)
    STACK="external_iframe"
    LAUNCH="launch:
  mode: iframe
  url: $ADDR"
    ;;
  proxy)
    STACK="external_proxy"
    LAUNCH="launch:
  mode: proxy
  upstream: $ADDR
  strip_prefix: true"
    ;;
esac

# ── 매니페스트 작성 (name/description/owner 는 빈칸 — 나중에 채움) ───────────
cat > "$MANIFEST" <<YAML
schema_version: 2
id: $APP_ID
name: ""                 # TODO: 카탈로그에 표시할 이름
owner: ""                # TODO: 담당자 (예: koo.park)
status: stable
app_type: external_link
execution_target: external_url
description: ""           # TODO: 설명
build:
  stack: $STACK
$LAUNCH
permissions:
  visibility: company     # company | department | team | private
YAML

echo
echo "■ 등록됨: $MANIFEST"
echo "  id=$APP_ID  mode=$MODE  주소=$ADDR"
echo
echo "▶ 다음 단계:"
echo "  1) (선택) 이름/설명/담당자 채우기:  \$EDITOR $MANIFEST"
echo "  2) 카탈로그 반영 — 5분 스캔을 기다리거나 즉시 트리거:"
echo "       cd $REPO_ROOT/backend && .venv/bin/python -c \\"
echo "         'from app.workers.integration_tasks import scan_integrations_periodic as s; print(s()[\"by_action\"])'"
echo
