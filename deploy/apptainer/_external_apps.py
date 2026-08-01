# 운영서버 외부 IP 서비스(venv로 뜬 백엔드+프론트)를 컨테이너 없이 proxy 매니페스트로 펼친다.
"""var/external-apps.yaml → integrations/ext_<id>/.portal/manifest.yaml (launch.mode: proxy).

운영서버에서 다른 팀이 로컬 경로에 가상환경으로 띄운 서비스(백엔드+프론트, IP:port 로 접근)를
HEAXHub 하위 경로(/apps/ext_<id>/)로 reverse_proxy 연동한다. 컨테이너/빌드/소스클론 없음.

라이프사이클 분리(핵심):
- 입력  var/external-apps.yaml  는 gitignore(var/) → update-all 의 `git reset --hard` 가 못 건드림.
- 출력  integrations/ext_*/     도 gitignore → 마찬가지로 update-all 이 보존.
  → update-all 은 "내가 관리하는(추적) 앱"만 갱신하고, 운영팀 외부앱은 그대로 둔다.
  → 운영팀 외부앱은 이 스크립트(별도 apply)로만 추가/수정/삭제된다.

적용 경로:
- 매니페스트를 쓰면 reconcile 비트(45s, build-free)가 라우트를, scan 비트(5분)가 카탈로그를 자동 반영.
- 이 스크립트는 끝에서 best-effort 로 reconcile 을 한 번 당겨 라우트를 즉시 등록한다(실패해도 무해).

호출: deploy/apptainer/register-external.sh (backend/.venv python 우선 — yaml + 백엔드 import 가능).
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "var" / "external-apps.yaml"
INTEG = ROOT / "integrations"
PREFIX = "ext_"
# 운영팀이 쓰는 base id — 소문자/숫자/언더스코어. 최종 canonical 은 ext_ 를 붙여 내 앱과 충돌 방지.
ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_HEADER = (
    "# [자동생성] var/external-apps.yaml → deploy/apptainer/register-external.sh 로 생성됨.\n"
    "# 직접 편집 금지 — 재실행 시 덮어씀. 원본은 var/external-apps.yaml 을 수정하세요.\n"
)


def _canonical(base_id: str) -> str:
    return base_id if base_id.startswith(PREFIX) else PREFIX + base_id


def _build_manifest(entry: dict) -> dict:
    """레지스트리 1건 → proxy 매니페스트 dict. (heax-demo-external-proxy 스키마 준수.)"""
    base_id = str(entry["id"]).strip()
    canonical = _canonical(base_id)
    upstream = str(entry["upstream"]).strip()
    manifest = {
        "schema_version": 2,
        "id": canonical,
        "name": str(entry.get("name") or base_id),
        "version": str(entry.get("version", "1.0.0")),
        "owner": str(entry.get("owner", "ops-external")),
        # MCP 레지스트리 노출 조건(status ∈ {beta,stable})을 위해 기본 stable.
        "status": str(entry.get("status", "stable")),
        "app_type": "external_link",
        "execution_target": "external_url",
        "build": {"stack": "external_proxy"},
        "launch": {
            "mode": "proxy",
            "upstream": upstream,
            "strip_prefix": bool(entry.get("strip_prefix", True)),
        },
        "description": str(
            entry.get("description") or f"운영서버 외부 연계(proxy) → {upstream}"
        ),
        "permissions": {"visibility": str(entry.get("visibility", "company"))},
    }
    mcp = entry.get("mcp")
    if isinstance(mcp, dict) and mcp.get("expose"):
        manifest["mcp"] = {
            "expose": True,
            "path": str(mcp.get("path", "/mcp")),
            "transport": str(mcp.get("transport", "streamable_http")),
        }
    return manifest


def _load_registry() -> list[dict]:
    if not REGISTRY.exists():
        print(f"· 레지스트리 없음 ({REGISTRY.relative_to(ROOT)}) — 할 일 없음.")
        print("  예시: deploy/apptainer/external-apps.example.yaml 를 복사해 시작하세요.")
        sys.exit(0)
    try:
        data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        print(f"✗ {REGISTRY.name} 파싱 실패: {exc}")
        sys.exit(2)
    services = data.get("services") if isinstance(data, dict) else None
    if services is None:
        services = data if isinstance(data, list) else []
    if not isinstance(services, list):
        print("✗ external-apps.yaml: 최상위에 services: 리스트가 있어야 합니다.")
        sys.exit(2)
    return [s for s in services if isinstance(s, dict)]


def _validate(services: list[dict]) -> list[str]:
    """원자성: 하나라도 잘못되면 아무것도 안 쓰고 실패(부분 적용 금지)."""
    errors: list[str] = []
    seen: set[str] = set()
    for i, e in enumerate(services):
        tag = f"services[{i}]"
        bid = str(e.get("id", "")).strip()
        if not bid:
            errors.append(f"{tag}: id 필수")
        elif not ID_RE.match(bid.removeprefix(PREFIX)):
            errors.append(f"{tag}: id '{bid}' 는 소문자/숫자/언더스코어만 (^[a-z][a-z0-9_]*$)")
        else:
            canon = _canonical(bid)
            if canon in seen:
                errors.append(f"{tag}: id '{bid}' 중복")
            seen.add(canon)
        up = str(e.get("upstream", "")).strip()
        if not up:
            errors.append(f"{tag}: upstream 필수 (예: http://10.0.0.5:8200)")
        elif not up.startswith(("http://", "https://")):
            errors.append(f"{tag}: upstream '{up}' 은 http:// 또는 https:// 로 시작해야 함")
    return errors


def _sync_manifests(services: list[dict]) -> tuple[list[str], list[str]]:
    """desired ext_ 매니페스트를 쓰고, 레지스트리에서 사라진 ext_ 디렉터리는 삭제한다."""
    INTEG.mkdir(exist_ok=True)
    desired: dict[str, dict] = {}
    for e in services:
        m = _build_manifest(e)
        desired[m["id"]] = m  # id 는 이미 ext_ 접두

    written: list[str] = []
    for canon, manifest in sorted(desired.items()):
        portal = INTEG / canon / ".portal"
        portal.mkdir(parents=True, exist_ok=True)
        body = _HEADER + yaml.safe_dump(
            manifest, allow_unicode=True, sort_keys=False, default_flow_style=False
        )
        (portal / "manifest.yaml").write_text(body, encoding="utf-8")
        written.append(canon)

    # prune: 레지스트리에 없는 ext_* 디렉터리 제거(운영팀이 서비스 내렸을 때)
    pruned: list[str] = []
    for child in INTEG.iterdir():
        if child.is_dir() and child.name.startswith(PREFIX) and child.name not in desired:
            shutil.rmtree(child, ignore_errors=True)
            pruned.append(child.name)
    return written, pruned


def _apply_now(services: list[dict], pruned: list[str]) -> None:
    """best-effort 즉시 반영 — ext_* 라우트만 스코프(내 앱은 안 건드림). 실패해도 exit 0 유지.

    라우트만 즉시 등록/해제한다. 카탈로그 DB 행은 scan 비트(≤5분)/재시작이 만든다.
    backend 를 import 못 하면(host python 에 앱 미설치) 조용히 생략 — 어차피 reconcile 비트가 반영.
    """
    try:
        from app.services import proxy_manager  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        print("  · 즉시 라우트 적용 생략(backend import 불가) — reconcile 비트(≤45s)에 자동 반영.")
        return

    # 삭제분 라우트 즉시 해제(reconcile 은 존재하는 dir 만 보므로 사라진 앱은 스스로 못 지움).
    for canon in pruned:
        try:
            proxy_manager.unregister_app_route(app_id=canon)
        except Exception as exc:  # noqa: BLE001
            print(f"    · {canon} 라우트 해제 실패({exc.__class__.__name__}) — 다음 재시작에 정리됨.")

    # 신규/변경 라우트 즉시 등록 — integration_launcher 의 proxy 분기와 동일한 스코프 호출.
    for e in services:
        canon = _canonical(str(e["id"]).strip())
        try:
            proxy_manager.register_external_proxy_route(
                app_id=canon,
                upstream_url=str(e["upstream"]).strip(),
                base_path=f"/apps/{canon}",
                strip_prefix=bool(e.get("strip_prefix", True)),
            )
        except Exception as exc:  # noqa: BLE001
            print(f"    · {canon} 라우트 등록 실패({exc.__class__.__name__}) — reconcile 비트에 재시도.")
    if services or pruned:
        print("  · 라우트 즉시 적용 완료(카탈로그 등재는 scan 비트 ≤5분).")


def main() -> None:
    services = _load_registry()
    errors = _validate(services)
    if errors:
        print(f"✗ external-apps.yaml 검증 실패 ({len(errors)}건) — 아무것도 적용 안 함:")
        for e in errors:
            print(f"    - {e}")
        sys.exit(2)

    written, pruned = _sync_manifests(services)
    print(f"✓ 외부 연계 매니페스트 동기화: 등록/갱신 {len(written)}건, 삭제 {len(pruned)}건")
    for c in written:
        print(f"    + {c}  → /apps/{c}/")
    for c in pruned:
        print(f"    - {c}  (제거)")
    _apply_now(services, pruned)


if __name__ == "__main__":
    main()
