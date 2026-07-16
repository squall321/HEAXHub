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
        hp = ((manifest.get("health_check") or {}).get("path")) or "/api/health"
        Path("/tmp/.redeploy_port").write_text(f"{port}\t{hp}")
PYEOF

# 헬스 확인.
INFO="$(cat /tmp/.redeploy_port 2>/dev/null || true)"; rm -f /tmp/.redeploy_port
if [[ -n "$INFO" ]]; then
  PORT="${INFO%%$'\t'*}"; HP="${INFO#*$'\t'}"
  sleep 2
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 6 "http://127.0.0.1:$PORT$HP" 2>/dev/null || echo 000)"
  echo "✓ 재기동 완료 — port=$PORT health($HP)=$code"
else
  echo "[WARN] 포트 확인 실패 — 로그 확인 필요." >&2
fi
