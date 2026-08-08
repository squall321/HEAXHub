# heax 앱 중 매니페스트에 mcp 블록을 선언한 것을 게이트웨이가 자동 흡수하도록 노출하는 레지스트리
"""MCP server registry.

The HWAX MCP Gateway polls ``GET /api/v1/mcp/servers`` and aggregates each
returned server as a backend, so an MCP app added to HEAXHub auto-appears in
the portal chat + personal Claude with no gateway config edit.

An app opts in by declaring an ``mcp`` block in its ``.portal/manifest.yaml``::

    mcp:
      expose: true
      path: /mcp
      transport: streamable_http

No DB migration / new AppType — the scanner already snapshots the raw manifest
into ``AppVersion.manifest_snapshot``, so the ``mcp`` block is preserved as-is.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from app.db.models.app import App, AppStatus
from app.db.models.app_version import AppVersion
from app.deps import CurrentUser, DbSession
from app.services import permission_service
from app.services.integration_launcher import _read_state

router = APIRouter(prefix="/mcp", tags=["mcp"])

# 게이트웨이에 노출할 published 상태 (draft/deprecated/archived 제외).
_EXPOSE_STATUSES = (AppStatus.BETA, AppStatus.STABLE)


def _ever_launched(app_id: str) -> bool:
    """기동 이력(state 파일) 유무 — 선언만 있고 한 번도 뜬 적 없는 앱을 걸러낸다.

    streamable_http MCP 는 실행 중 프로세스 없이 서빙될 수 없으므로, 기동된 적 없는
    앱(미완성·빌드 실패)을 노출하면 게이트웨이에 영구 다운 백엔드가 생긴다.

    현재 pid 생존까지 보지는 **않는다**. 재빌드·재기동하는 수십 초 동안 목록에서 빠지면
    게이트웨이가 그 백엔드를 제거해 사용자에겐 "그런 도구 없습니다"로 보인다 — 37b3f55
    가 폴링 실패에서 막은 것과 같은 증상을 앱 단위로 재현하는 셈이다. 일시적 다운은
    게이트웨이 revive 루프가 재연결하므로 목록에 남겨두고, 실체가 아예 없는 앱만 뺀다.
    """
    return _read_state(app_id) is not None


@router.get("/servers")
def list_mcp_servers(db: DbSession, user: CurrentUser) -> dict[str, Any]:
    """manifest 에 ``mcp.expose: true`` 를 선언한 published 앱 목록.

    각 항목은 게이트웨이가 base(heax Caddy 오리진)와 조합할 **상대경로**(path)를 준다 —
    base 는 게이트웨이 config 가 갖는다(도메인 하드코딩 회피: dev=localhost / prod=도메인).
    호출자(서비스 PAT)의 가시성 밖 앱은 제외한다(fail-closed).
    """
    visible = permission_service.visible_app_ids(db, user)  # None = 전체 가시
    rows = list(
        db.execute(select(App).where(App.status.in_(_EXPOSE_STATUSES))).scalars()
    )
    servers: list[dict[str, Any]] = []
    for app in rows:
        if visible is not None and app.id not in visible:
            continue
        if not app.current_version_id:
            continue
        version = db.get(AppVersion, app.current_version_id)
        manifest = (
            version.manifest_snapshot
            if version and isinstance(version.manifest_snapshot, dict)
            else None
        )
        mcp = (manifest or {}).get("mcp")
        if not isinstance(mcp, dict) or not mcp.get("expose"):
            continue
        if not _ever_launched(app.id):
            continue  # 선언만 있고 기동 이력이 없는 앱 — 영구 다운 백엔드를 만들지 않는다
        sub = str(mcp.get("path", "/mcp"))
        if not sub.startswith("/"):
            sub = "/" + sub
        # 앱 단위 도구 선택 UI 는 이름과 설명이 사용자가 보는 전부다. 설명을 주지 않으면
        # 게이트웨이가 빈 문자열을 싣고, 사용자는 앱 이름만으로 무엇을 하는 앱인지 추측하게 된다.
        # mcp.description(그 MCP 가 무엇을 제공하는지)이 있으면 그것을, 없으면 앱 설명을 쓴다.
        desc = str(mcp.get("description") or app.description or "").strip()
        servers.append(
            {
                "id": app.id,
                "name": app.name,
                "description": desc[:300],
                "path": f"/apps/{app.id}{sub}",
                "transport": str(mcp.get("transport", "streamable_http")),
                # 게이트웨이 그룹 필터 슬롯 — 비면 전체 공개, 있으면 caller groups 교집합 필요.
                "allowed_groups": list(mcp.get("allowed_groups") or []),
            }
        )
    return {"servers": servers}
