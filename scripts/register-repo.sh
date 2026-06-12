#!/usr/bin/env bash
#
# register-repo.sh — GitHub(또는 임의 git) 저장소를 HEAXHub 에 등록한다.
# 스캐너가 clone → SIF 빌드 → 서브경로(/apps/<slug>/) 서빙까지 자동 처리한다.
#
# 사용법:
#   scripts/register-repo.sh                              # 대화형
#   scripts/register-repo.sh <slug> <git-url> <stack> [ref]
#
# 예:
#   scripts/register-repo.sh mytool https://github.com/org/mytool fastapi
#   scripts/register-repo.sh dash1  https://github.com/org/dash   dash_plotly main
#
# 지원 stack (포맷):
#   [service] fastapi fastapi_react flask streamlit nextjs nodejs_express
#             go_service rust_actix dotnet_aspnet java_springboot
#             dash_plotly shiny_for_python
#   [static]  static_html mkdocs_static
#   [job]     python_cli r_script cpp_executable
#
# 이름/설명/담당자는 비워 둔다 — 등록 후 매니페스트 파일을 열어 채운다.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INTEGRATIONS_DIR="$REPO_ROOT/integrations"
STACKS_YAML="$REPO_ROOT/config/stacks.yaml"

# ── 입력 수집 ────────────────────────────────────────────────────────────────
SLUG="${1:-}"
GIT_URL="${2:-}"
STACK="${3:-}"
REF="${4:-}"

if [[ -z "$SLUG" ]]; then
  read -rp "slug (영문/숫자/하이픈, 카탈로그 고유 ID): " SLUG
fi
if [[ -z "$GIT_URL" ]]; then
  read -rp "git 주소 (https://github.com/... 또는 git@...): " GIT_URL
fi
if [[ -z "$STACK" ]]; then
  echo "지원 stack: fastapi fastapi_react flask streamlit nextjs nodejs_express"
  echo "            go_service rust_actix dotnet_aspnet java_springboot dash_plotly"
  echo "            shiny_for_python static_html mkdocs_static python_cli r_script cpp_executable"
  read -rp "포맷(stack): " STACK
fi
if [[ -z "$REF" ]]; then
  read -rp "브랜치/태그/커밋 (기본 main): " REF
  REF="${REF:-main}"
fi

# ── 검증 ────────────────────────────────────────────────────────────────────
SLUG="$(echo "$SLUG" | tr '[:upper:]' '[:lower:]' | tr ' _' '--')"
if [[ ! "$SLUG" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  echo "오류: slug 는 영문 소문자/숫자/하이픈만 가능합니다: '$SLUG'" >&2
  exit 1
fi
APP_ID="$(echo "$SLUG" | tr '-' '_')"

# stack 이 stacks.yaml 에 정의돼 있는지 확인.
if ! grep -qE "^  ${STACK}:" "$STACKS_YAML"; then
  echo "오류: 알 수 없는 stack '$STACK'. config/stacks.yaml 에 정의된 값을 쓰세요." >&2
  echo "정의된 stack:" >&2
  grep -E "^  [a-z_]+:" "$STACKS_YAML" | sed 's/://; s/^/  /' >&2
  exit 1
fi

# stack 의 launch_mode 를 stacks.yaml 에서 읽어 launch 블록을 결정.
LAUNCH_MODE="$(
  cd "$REPO_ROOT" && python3 - "$STACK" <<'PY'
import sys, yaml
stack = sys.argv[1]
d = yaml.safe_load(open('config/stacks.yaml'))
print((d.get('stacks') or {}).get(stack, {}).get('launch_mode', 'service'))
PY
)"

DEST_DIR="$INTEGRATIONS_DIR/$SLUG/.portal"
MANIFEST="$DEST_DIR/manifest.yaml"
if [[ -e "$MANIFEST" ]]; then
  echo "오류: 이미 존재합니다: $MANIFEST (수정은 파일 직접 편집)" >&2
  exit 1
fi
mkdir -p "$DEST_DIR"

# ── launch 블록 (stack 의 launch_mode 기준). command 는 stacks.yaml 의 ──────
#    entrypoint 기본값을 쓰므로 생략한다 — 비표준 실행이 필요하면 나중에 추가.
case "$LAUNCH_MODE" in
  service)
    APP_TYPE="web_app"; EXEC_TARGET="linux_runner"
    LAUNCH="launch:
  mode: service
  # command: <기본 entrypoint 대신 직접 지정하려면 주석 해제>
health_check:
  path: /              # TODO: 헬스체크 경로 (예: /health, /healthz)
restart_policy:
  policy: on_failure
  max_retries: 3"
    ;;
  static)
    APP_TYPE="web_app"; EXEC_TARGET="linux_runner"
    LAUNCH="launch:
  mode: static"
    # static 스택은 build.root 로 정적 산출물 경로를 지정한다(기본 '.').
    ;;
  job_runner)
    APP_TYPE="cli_tool"; EXEC_TARGET="linux_runner"
    LAUNCH="launch:
  mode: job_runner
inputs: []             # TODO: 입력 폼 정의 (필요 시)"
    ;;
  *)
    APP_TYPE="web_app"; EXEC_TARGET="linux_runner"
    LAUNCH="launch:
  mode: service"
    ;;
esac

# static 스택은 build 에 root 도 넣어준다.
BUILD_BLOCK="build:
  stack: $STACK"
if [[ "$LAUNCH_MODE" == "static" ]]; then
  BUILD_BLOCK="build:
  stack: $STACK
  root: .              # TODO: 정적 산출물 디렉터리 (빌드 결과물 위치)"
fi

# ── 매니페스트 작성 ─────────────────────────────────────────────────────────
cat > "$MANIFEST" <<YAML
schema_version: 2
id: $APP_ID
name: ""                 # TODO: 카탈로그에 표시할 이름
owner: ""                # TODO: 담당자
status: stable
app_type: $APP_TYPE
execution_target: $EXEC_TARGET
description: ""           # TODO: 설명
$BUILD_BLOCK
$LAUNCH
source:
  type: git
  url: $GIT_URL
  ref: $REF
  # subpath: ""          # 모노레포면 하위 디렉터리 지정
permissions:
  visibility: company     # company | department | team | private
YAML

echo
echo "■ 등록됨: $MANIFEST"
echo "  id=$APP_ID  stack=$STACK  mode=$LAUNCH_MODE"
echo "  source=$GIT_URL  ref=$REF"
echo
echo "▶ 다음 단계:"
echo "  1) (권장) 이름/설명/담당자/헬스체크 경로 채우기:  \$EDITOR $MANIFEST"
echo "  2) clone → SIF 빌드 → 서빙 트리거 (스캔 1회). 첫 빌드는 수 분 걸린다:"
echo "       cd $REPO_ROOT/backend && .venv/bin/python -c \\"
echo "         'from app.workers.integration_tasks import scan_integrations_periodic as s; print(s()[\"by_action\"])'"
echo "  3) 빌드 후 서비스 경로:  /apps/$APP_ID/"
echo
echo "  ※ private 저장소면 토큰 인증이 필요합니다(현재 미지원 — 공개 repo 또는"
echo "     사내 미러를 사용하세요)."
echo
