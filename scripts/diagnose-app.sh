#!/usr/bin/env bash
# 앱이 HEAX 허브에서 안 열릴 때 어디서 막히는지 한 번에 가른다 — 업스트림·Caddy 라우트·인가·DB 상태.
# 사용: bash scripts/diagnose-app.sh kooremapper [heax_access_token]
set -uo pipefail

SLUG="${1:?사용: diagnose-app.sh <app_id> [heax_access_token]}"
TOKEN="${2:-}"
CADDY="${CADDY_BASE:-http://127.0.0.1:4180}"
ADMIN="${CADDY_ADMIN:-http://127.0.0.1:2019}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

code() { curl -s -o /dev/null -w '%{http_code}' -m 6 "$@" 2>/dev/null || echo 000; }

echo "===== 1. 매니페스트 ====="
F="$(grep -rl "^id: *${SLUG}\$" "$ROOT"/integrations/*/.portal/manifest.yaml 2>/dev/null | head -1)"
if [ -z "$F" ]; then
  echo "  X id: $SLUG 인 매니페스트가 integrations/ 에 없다 — 등록 자체가 안 된 것이다."
else
  echo "  파일: ${F#"$ROOT"/}"
  grep -nE "^status:|^app_type:|^execution_target:|mode:|upstream:|portal_auth:|visibility:" "$F" | sed 's/^/    /'
fi

UP="$(grep -oE 'upstream: *http[^ ]*' "${F:-/dev/null}" 2>/dev/null | head -1 | sed 's/upstream: *//')"
# 박스 전용 덮어쓰기(HH-15) — 있으면 허브는 그것을 얹은 효과 매니페스트로 돈다. 위의 추적 파일 값만 보면 엉뚱한 업스트림을 두드린다.
LOCAL="$(dirname "${F:-/nonexistent/x}")/manifest.local.yaml"
if [ -n "$F" ] && [ -f "$LOCAL" ]; then
  echo "  ! 박스 덮어쓰기 ${LOCAL#"$ROOT"/} 가 있다 — 허브는 그것을 얹은 효과 매니페스트로 돈다."
  EFF="$(cd "$ROOT/backend" && .venv/bin/python - "$F" 2>&1 <<'PYEOF'
import logging, sys
from pathlib import Path
logging.disable(logging.CRITICAL)
from app.services import integration_manifest as M
m, o = M.load_file_with_override(Path(sys.argv[1]))
m = m if isinstance(m, dict) else {}
la = m.get("launch") or {}
if o is not None and not o.applied:
    print(f"    무시됨: {o.error} — 추적 파일 값 그대로다")
else:
    print(f"    바뀐 최상위 키: {', '.join(o.changed_keys) if o else '-'}"
          + (f"  (받지 않은 키: {', '.join(o.rejected_keys)})" if o and o.rejected_keys else ""))
    print(f"    효과 값: status={m.get('status')} execution_target={m.get('execution_target')} mode={la.get('mode')}"
          f" upstream={la.get('upstream') or '-'} portal_auth={la.get('portal_auth')}"
          f" visibility={(m.get('permissions') or {}).get('visibility')}")
    print(f"UP={la.get('upstream') or ''}")
PYEOF
)" || EFF="    (효과 값을 못 셌다 — backend/.venv 확인) ${EFF}"
  printf '%s\n' "$EFF" | grep -v '^UP=' || true
  # grep -q 로 판정하지 않는다 — pipefail 아래서 일찍 끝난 grep 이 '있다' 를 '없다' 로 뒤집는다.
  case $'\n'"$EFF" in *$'\n'UP=*) UP="$(printf '%s\n' "$EFF" | sed -n 's/^UP=//p' | tail -1)" ;; esac
fi
echo
echo "===== 2. 업스트림(앱 자체 서버) ====="
if [ -n "$UP" ]; then
  echo "  $UP/health -> $(code "$UP/health")   (200 이어야 한다. 000·502 면 앱 서버가 안 떠 있다)"
  echo "  $UP/       -> $(code "$UP/")"
else
  echo "  (proxy 모드가 아니다 — HEAX 가 SIF 로 직접 띄우는 앱이다)"
  S="$ROOT/var/integration_state/${SLUG}.json"
  if [ -f "$S" ]; then head -12 "$S" | sed 's/^/    /'; else echo "    X state 파일 없음 — 한 번도 기동된 적이 없다."; fi
fi

echo
echo "===== 3. Caddy 라우트 ====="
if curl -s -m 6 "$ADMIN/config/apps/http/servers/srv0/routes" 2>/dev/null | grep -q "apps/${SLUG}"; then
  echo "  O /apps/${SLUG} 라우트가 Caddy 에 등록돼 있다."
else
  echo "  X 라우트가 없다 — reconcile 이 실패했거나 앱이 기동되지 않았다."
fi
echo "  익명 GET $CADDY/apps/$SLUG/ -> $(code "$CADDY/apps/$SLUG/")"
echo "    401=인증 필요(정상) · 403=권한 없음(6번) · 404=라우트 없음 · 502·504=업스트림 부재"

echo
echo "===== 4. 로그인 상태 인가 판정 ====="
if [ -n "$TOKEN" ]; then
  echo "  쿠키로  -> $(code --cookie "heax_access_token=$TOKEN" "$CADDY/apps/$SLUG/")"
  echo "  Bearer  -> $(code -H "Authorization: Bearer $TOKEN" "$CADDY/apps/$SLUG/")"
  echo "    200 이면 정상. 403 이면 그 사용자에게 이 앱을 볼 권한이 없다 -> 6번."
else
  echo "  (토큰 미지정 — 브라우저 개발자도구 Application > Cookies 의 heax_access_token 값을"
  echo "   두 번째 인자로 주면 로그인 상태까지 판정한다)"
fi

echo
echo "===== 5. 소스 부재 폴백 여부 ====="
echo "  fetch 가 실패했는데 프리빌드 SIF 로 떠 있으면 앱은 UP 이지만 코드는 그 SIF 에 굳어 있다"
echo "  (새 커밋이 반영되지 않는다). 확인:"
echo "    cd $ROOT/backend && .venv/bin/python -c \"import sys;sys.path.insert(0,'.');\\"
echo "      from app.db.session import SessionLocal;from app.db.models.app import App;\\"
echo "      a=SessionLocal().get(App,'$SLUG');\\"
echo "      print((a.extra or {}).get('source_unavailable') or '폴백 아님(소스 정상)')\""
echo "  감사기록으로도 남는다: action='integration.source.unavailable'"

echo
echo "===== 6. DB 공개 범위 vs 매니페스트 ====="
echo "  두 값이 다르면 등록된 앱의 visibility 가 옛 값에 굳은 것이다(스캐너 동기화 누락, 커밋 0455af8 로 수정)."
echo "  확인:"
echo "    cd $ROOT/backend && .venv/bin/python -c \"import sys;sys.path.insert(0,'.');\\"
echo "      from app.db.session import SessionLocal;from app.db.models.app import App;\\"
echo "      db=SessionLocal();a=db.get(App,'$SLUG');\\"
echo "      print('visibility',a.visibility.value if a else '행없음','status',a.status.value if a else '-',\\"
echo "            'portal_auth',(a.extra or {}).get('portal_auth') if a else '-')\""
