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
from app.services.integration_launcher import _is_alive, _read_state

router = APIRouter(prefix="/mcp", tags=["mcp"])

# 게이트웨이에 노출할 published 상태 (draft/deprecated/archived 제외).
_EXPOSE_STATUSES = (AppStatus.BETA, AppStatus.STABLE)


def _instance_alive(app_id: str) -> bool:
    """manifest 선언이 아니라 **실체** 기준 판정 — 인스턴스가 실제로 떠 있는가.

    streamable_http MCP 는 실행 중인 프로세스 없이는 서빙될 수 없으므로, mcp 노출
    앱에 한해 기동 이력 없음(state 파일 부재)도 부적격으로 본다. 선언만 있고 실체
    없는 앱(미완성·빌드 실패)이 게이트웨이 백엔드 다운으로 계속 표시되던 문제의
    구조적 해소다. 재시작 중 잠깐 빠지는 것은 게이트웨이 revive 루프(60s)가
    자동 복원하므로 무해하다(폴링 실패 None 과 구분되는 정상 목록 축소).
    """
    state = _read_state(app_id)
    if state is None:
        return False
    return _is_alive(state.get("pid"))


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
        if not _instance_alive(app.id):
            continue  # 선언만 있고 인스턴스가 없는 앱 — 게이트웨이에 죽은 백엔드를 만들지 않는다
        sub = str(mcp.get("path", "/mcp"))
        if not sub.startswith("/"):
            sub = "/" + sub
        servers.append(
            {
                "id": app.id,
                "name": app.name,
                "path": f"/apps/{app.id}{sub}",
                "transport": str(mcp.get("transport", "streamable_http")),
                # 게이트웨이 그룹 필터 슬롯 — 비면 전체 공개, 있으면 caller groups 교집합 필요.
                "allowed_groups": list(mcp.get("allowed_groups") or []),
            }
        )
    return {"servers": servers}
