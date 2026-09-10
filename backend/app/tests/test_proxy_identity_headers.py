# 내부 앱도 신원 헤더를 받되, authz 가 준 경우에만 — 빈 값으로 덮으면 앱이 401 을 낸다
from app.services.proxy_manager import _IDENTITY_HEADERS, _build_route


def _gate(app_id: str = "step_forge", port: int = 9303) -> dict:
    route = _build_route(app_id, port, None)
    return route["handle"][0]["routes"][0]["handle"][0]


def _copy_routes(gate: dict) -> list[dict]:
    return gate["handle_response"][0].get("routes") or []


def test_internal_app_route_copies_identity_to_upstream():
    """내부 앱도 호출자를 알 수 있어야 한다.

    예전에는 외부 업스트림 라우트에만 붙어 있어서, 허브 안에서 도는 앱은 누가 부르는지
    영영 알 수 없었다. StepForge 가 과제를 만들어도 담당자 칸이 늘 비었던 원인이다.
    """
    routes = _copy_routes(_gate())
    assert routes, "내부 앱 라우트에 신원 복사가 없다 — 업스트림이 호출자를 알 수 없다"
    sets = {
        h: r for r in routes
        if "set" in r["handle"][0]["request"]
        for h in r["handle"][0]["request"]["set"]
    }
    for h in _IDENTITY_HEADERS:
        assert h in sets, f"{h} 를 업스트림으로 넘기지 않는다"
        # 값은 authz 응답에서 온다 — 클라이언트가 보낸 값을 쓰면 위조가 통한다.
        assert sets[h]["handle"][0]["request"]["set"][h] == [f"{{http.reverse_proxy.header.{h}}}"]


def test_identity_is_set_only_when_authz_provided_it():
    """⚠ 무조건 set 하면 안 된다 — 실사고 재발 방지.

    값이 없을 때 빈 문자열로 덮이는데, 그것을 '신원 없음' 이 아니라 '빈 사용자로 인증됨'
    으로 읽는 앱이 있다. 무조건 set 으로 깔았다가 kooremapper_mcp 가 401 을 내며
    게이트웨이에서 통째로 떨어졌다(2026-09-10). 게이트웨이는 서비스 토큰으로 부르므로
    authz 가 사용자 신원을 싣지 않는다.
    """
    for r in _copy_routes(_gate()):
        if "set" not in r["handle"][0]["request"]:
            continue
        expr = (r.get("match") or [{}])[0].get("expression", "")
        assert "!= ''" in expr, f"조건 없이 set 한다 — 빈 값으로 덮인다: {r.get('match')}"


def test_client_supplied_identity_is_always_deleted():
    """조건부이므로 지우는 단계가 반드시 앞에 있어야 한다 — 없으면 위조 값이 통과한다."""
    routes = _copy_routes(_gate())
    first = routes[0]["handle"][0]["request"]
    assert first.get("delete") == list(_IDENTITY_HEADERS), "위조 헤더 제거가 맨 앞에 없다"
    assert routes[0].get("match") is None, "제거는 무조건이어야 한다"


def test_gate_still_blocks_non_2xx():
    """신원 복사를 붙이면서 인가 게이트 자체가 느슨해지지 않았는지."""
    gate = _gate()
    assert gate["handler"] == "reverse_proxy"
    assert gate["rewrite"]["method"] == "GET"           # 본문을 소비하지 않는다
    assert gate["handle_response"][0]["match"]["status_code"] == [2]
