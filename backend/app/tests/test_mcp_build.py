# MCP 레지스트리 목록이 앱 줄마다 떠 있는 인스턴스의 판(build)을 싣는지 — 모르면 칸째 빼는지 확인한다
"""포털 PT-R3 · P-3(운영본 표시) — ``GET /api/v1/mcp/servers`` 의 ``build`` 칸.

게이트웨이는 ``{commit: [0-9a-f]{7,40}, started_at?: 40자 이하}`` 일 때만 받아 싣고 그 밖은 버린다.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.v1 import mcp

_SHA = "bfa1977b0d0bf9abb650e0444790cf47ad276d58"


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"commit": _SHA, "started_at": 1794700000.5},
         {"commit": "bfa1977", "started_at": "2026-11-14T23:46:40Z"}),
        ({"commit": _SHA}, {"commit": "bfa1977"}),
        ({"commit": None, "started_at": 1794700000.0}, None),  # 기동 뒤 SIF 가 바뀌어 모른다
        ({"started_at": 1794700000.0}, None),                   # 이 칸 전에 뜬 인스턴스
        ({"commit": "sha256:" + "a" * 64}, None),               # 아카이브 다이제스트는 커밋이 아니다
        ({"mode": "proxy", "pid": None}, None),                 # proxy 는 판을 모른다
        (None, None),
    ],
    ids=["commit+started", "commit-only", "unknown", "no-key", "digest", "proxy", "no-state"],
)
def test_build_info(monkeypatch: pytest.MonkeyPatch, state, expected) -> None:
    monkeypatch.setattr(mcp, "_read_state", lambda app_id: state)
    assert mcp._build_info("demo") == expected


class _DB:
    """list_mcp_servers 가 쓰는 두 호출만 흉내 낸다 — 상태 거르기는 쿼리 몫이라 여기선 보지 않는다."""

    def __init__(self, apps, versions):
        self._apps, self._versions = apps, versions

    def execute(self, _stmt):
        return SimpleNamespace(scalars=lambda: list(self._apps))

    def get(self, _model, vid):
        return self._versions.get(vid)


def test_servers_carry_build_only_when_known(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = {"mcp": {"expose": True, "path": "/mcp"}}
    apps = [
        SimpleNamespace(id=i, name=i, description="", current_version_id=f"v-{i}")
        for i in ("known", "unknown")
    ]
    versions = {f"v-{a.id}": SimpleNamespace(manifest_snapshot=manifest) for a in apps}
    states = {"known": {"commit": _SHA, "started_at": 1794700000.0}, "unknown": {"pid": 1}}
    monkeypatch.setattr(mcp, "_read_state", lambda app_id: states.get(app_id))
    monkeypatch.setattr(mcp.permission_service, "visible_app_ids", lambda db, user: None)

    rows = {s["id"]: s for s in mcp.list_mcp_servers(_DB(apps, versions), user=None)["servers"]}

    assert rows["known"]["build"] == {"commit": "bfa1977", "started_at": "2026-11-14T23:46:40Z"}
    assert "build" not in rows["unknown"]  # 모르면 칸째 뺀다 — 빈 값을 싣지 않는다
