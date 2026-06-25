#!/usr/bin/env bash
# verify-deploy.sh — 배포 직후 스모크 테스트.
#
# 사람이 브라우저에서 502/404 를 발견하기 전에, 스크립트가 먼저 빨간불을 띄운다.
# 세 가지를 점검한다 (이번에 실제로 겪은 실패 클래스):
#   1) 백엔드 /health (502·시크릿 가드 크래시 차단)
#   2) SPA index + 첫 에셋 200 (dist base-path 불일치 = 에셋 404 차단)
#   3) API 가 프리픽스 아래에서 닿는지 (/api/v1/apps → 401=정상도달, 404=프록시 깨짐)
#
# 사용:
#   bash deploy/apptainer/verify-deploy.sh                                  # 로컬(:4180)
#   bash deploy/apptainer/verify-deploy.sh https://hwax.sec.samsung.net/heax-hub   # 포털
set -uo pipefail
BASE="${1:-http://localhost:4180}"; BASE="${BASE%/}"
ORIGIN="$(printf '%s' "$BASE" | sed -E 's#(https?://[^/]+).*#\1#')"
API_PORT="${API_PORT:-4040}"
FAIL=0
pass(){ printf '  \033[1;32m✓\033[0m %s\n' "$*"; }
fail(){ printf '  \033[1;31m✗\033[0m %s\n' "$*" >&2; FAIL=1; }

echo "▶ verify-deploy  base=$BASE"

# 1) 백엔드 health (로컬 직접) ────────────────────────────────────────────────
if curl -fsS -m 6 "http://localhost:${API_PORT}/health" >/dev/null 2>&1; then
  pass "backend /health (localhost:${API_PORT})"
else
  fail "backend /health 미응답 — var/logs/backend.log 확인"
fi

# 2) SPA + 첫 에셋 200 (base-path 404 클래스 차단) ───────────────────────────
idx="$(curl -fsSL -m 10 "$BASE/" 2>/dev/null)"
if [ -z "$idx" ]; then
  fail "SPA index 로드 실패 ($BASE/)"
else
  # 외부 CDN(폰트 등) 말고 로컬 번들(assets/)을 골라야 base-path 불일치를 잡는다.
  asset="$(printf '%s' "$idx" | grep -oE '(src|href)="[^"]*assets/[^"]+\.(js|css)"' | sed -E 's/.*="([^"]+)".*/\1/' | grep -vE '^https?://' | head -1)"
  if [ -z "$asset" ]; then
    fail "index.html 에 에셋 참조 없음"
  else
    case "$asset" in
      http*) url="$asset" ;;
      /*)    url="$ORIGIN$asset" ;;
      *)     url="$BASE/${asset#./}" ;;
    esac
    # content-type 까지 확인 — 없는 에셋은 SPA fallback 으로 index.html(text/html)
    # 또는 프록시 404 가 온다. code=200 만으로는 못 잡으니 js/css 타입을 요구한다.
    case "${asset##*.}" in js) want='javascript|ecmascript' ;; css) want='css' ;; *) want='.' ;; esac
    read -r code ct < <(curl -s -m 10 -o /dev/null -w '%{http_code} %{content_type}' "$url")
    if [ "$code" = "200" ] && printf '%s' "$ct" | grep -qiE "$want"; then
      pass "SPA 에셋 OK ($asset → $ct)"
    else
      fail "SPA 에셋 미존재 (code=$code ct=$ct) — SPA fallback/404 = dist base 불일치 의심 (VITE_BASE_PATH 확인): $url"
    fi
  fi
fi

# 3) API 프리픽스 도달 (/api/v1/apps 는 인증게이트라 401=정상) ────────────────
acode="$(curl -s -m 8 -o /dev/null -w '%{http_code}' "$BASE/api/v1/apps" 2>/dev/null)"
case "$acode" in
  200|401|403) pass "API 프리픽스 도달 ($BASE/api/v1/apps → $acode)" ;;
  404)         fail "API 미도달 404 — 포털 프록시 /api 매핑 또는 base 확인" ;;
  *)           fail "API 응답 이상 (code=$acode) — 백엔드/프록시 확인" ;;
esac

echo
if [ "$FAIL" = "0" ]; then echo "✓ 배포 검증 통과"; else echo "✗ 배포 검증 실패 — 위 항목 확인"; exit 1; fi
