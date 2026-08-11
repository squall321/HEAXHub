#!/usr/bin/env bash
# 앱을 최신 빌드 SIF로 재기동(전환). --rebuild면 git fetch + SIF 리빌드부터.
#
# 스캐너(5분)가 upstream을 fetch해 SIF를 최신으로 리빌드하지만, 정상 인스턴스는
# 자동 재시작하지 않는다. 이 스크립트가 그 라이브 전환(재기동)을 한 방에 한다.
#
# 사용:
#   deploy/apptainer/redeploy-app.sh <slug>            # 최신 빌드 SIF로 전환(재기동)
#   deploy/apptainer/redeploy-app.sh <slug> --rebuild  # git fetch + SIF 리빌드 후 전환
#   slug = integrations/<slug> 디렉터리명 (예: materialtwin-web)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # HEAXHub 루트
SLUG="${1:?사용: redeploy-app.sh <slug> [--rebuild]}"
REBUILD=0
[[ "${2:-}" == "--rebuild" ]] && REBUILD=1
PY="$ROOT/backend/.venv/bin/python"

[[ -d "$ROOT/integrations/$SLUG" ]] || { echo "[ERROR] integrations/$SLUG 없음" >&2; exit 1; }

REBUILD="$REBUILD" SLUG="$SLUG" HH_ROOT="$ROOT" "$PY" - <<'PYEOF'
import os, sys, yaml
from pathlib import Path

ROOT = Path(os.environ["HH_ROOT"]); slug = os.environ["SLUG"]; rebuild = os.environ["REBUILD"] == "1"
sys.path.insert(0, str(ROOT / "backend"))
from app.db.session import SessionLocal
from app.services import integration_launcher as L

child = ROOT / "integrations" / slug
manifest = yaml.safe_load((child / ".portal" / "manifest.yaml").read_text())
canonical = manifest.get("id") or slug.replace("-", "_")
sif = ROOT / "var" / "sifs" / f"{slug}.sif"
src = manifest.get("source") if isinstance(manifest.get("source"), dict) else None

with SessionLocal() as db:
    if rebuild:
        from app.services.integrations_scanner import SourceSpec
        from app.services import integration_fetcher, integration_sif_builder
        ss = SourceSpec.from_manifest(manifest)
        if ss is None:
            print("manifest.source 없음 — rebuild 불가", file=sys.stderr); sys.exit(2)
        print(f"[fetch] {slug} upstream(git) …")
        fr = integration_fetcher.fetch_for_integration(slug, ss)
        print(f"  commit: {getattr(fr, 'commit', None)}")
        print("[build] SIF 리빌드 …")
        sr = integration_sif_builder.build_sif(slug, manifest, fr)
        print(f"  build: status={getattr(sr, 'status', sr)} sif={getattr(sr, 'sif_path', None)}")

    print(f"[stop] {canonical}: {L.stop(canonical, db=db)}")
    lr = L.launch(child, manifest=manifest, db=db, slug=slug, source=src,
                  sif_path=sif if sif.exists() else None)
    print(f"[launch] action={getattr(lr, 'action', lr)} "
          f"port={getattr(lr, 'port', None)} error={getattr(lr, 'error', None)}")
    port = getattr(lr, "port", None)
    if port:
        # manifest은 여기서 이미 파싱됨 — 헬스 경로까지 bash로 넘긴다.
        # 백엔드와 같은 자리를 읽어야 한다. 스캐너(integrations_scanner:514)도 런처
        # (integration_launcher:244)도 launch.health_check 만 본다 — top-level 은 아무도
        # 안 읽는다. 여기만 top-level 을 읽어서 23개 중 12개가 서로 다른 경로를 프로브했고,
        # 정상 앱이 404 로 찍혔다(실측).
        _launch = manifest.get("launch") or {}
        # 폴백은 런처와 맞춘다 — integration_launcher:244 는 `or spec.health_path or "/"` 다.
        # (스캐너는 "/health" 를 쓰지만 앱의 생존을 실제로 판정하는 건 런처 쪽이다.)
        hp = ((_launch.get("health_check") or {}).get("path")) or "/"
        _top = (manifest.get("health_check") or {}).get("path")
        if _top and not (_launch.get("health_check") or {}).get("path"):
            print(f"[WARN] {slug}: health_check 가 top-level 에 있어 아무도 읽지 않는다"
                  f"(top={_top}) — launch: 아래로 옮겨야 백엔드가 그 경로로 프로브한다."
                  f" 지금은 {hp} 로 프로브한다.")
        # base_path 도 함께 넘긴다. 런처는 _is_healthy(port, health_path, root=base_path) 로
        # /apps/<canonical> 을 앞에 붙여 프로브한다(integration_launcher:242,250). 이걸 빼면
        # 하위경로로 서빙하는 앱(dash·streamlit·flask)이 멀쩡한데도 404/500 으로 찍힌다
        # (실측: :9145/health=500 이지만 :9145/apps/heax_demo_flask/health=200).
        Path("/tmp/.redeploy_port").write_text(f"{port}\t{hp}\t/apps/{canonical}")
PYEOF

# 헬스 확인.
INFO="$(cat /tmp/.redeploy_port 2>/dev/null || true)"; rm -f /tmp/.redeploy_port
if [[ -n "$INFO" ]]; then
  PORT="$(printf '%s' "$INFO" | cut -f1)"
  HP="$(printf '%s' "$INFO" | cut -f2)"
  BASE="$(printf '%s' "$INFO" | cut -f3)"
  sleep 2
  # 판정은 런처의 _is_healthy(integration_launcher:1283)와 똑같이 한다 — 후보 4개를 돌며
  # 500 미만이면 정상. 스택마다 prefix 인식 여부가 달라(strip_prefix) base_path 를 붙일지가
  # 앱마다 다르기 때문이다. 한쪽으로만 고정하면 멀쩡한 앱이 무더기로 실패한다(실측:
  # base 없이 → 3개 실패, base 붙여서 → 10개 실패).
  # `|| echo 000` 은 쓰지 않는다 — curl 은 실패에도 -w 로 이미 000 을 찍어 '000000' 이 된다.
  BARE="${BASE%/}"
  code=000; hit=""
  for u in "$BASE$HP" "$HP" "$BASE" "$BARE"; do
    c="$(curl -s -o /dev/null -w '%{http_code}' --max-time 6 "http://127.0.0.1:$PORT$u" 2>/dev/null)"
    c="${c:-000}"
    if [ "$c" != "000" ] && [ "$c" -lt 500 ]; then code="$c"; hit="$u"; break; fi
    [ "$code" = "000" ] && code="$c"
  done
  if [ -n "$hit" ]; then
    echo "✓ 재기동 완료 — port=$PORT health($hit)=$code"
  else
    echo "✗ 재기동 실패 — port=$PORT, 후보 경로 4개 모두 실패(마지막 코드 $code)" >&2
    echo "   시도: $BASE$HP | $HP | $BASE | $BARE" >&2
    echo "   무응답(000)이면 프로세스가 안 떴다. 앱 로그를 확인하라." >&2
    exit 1
  fi
else
  echo "[WARN] 포트 확인 실패 — 로그 확인 필요." >&2
  exit 1
fi
