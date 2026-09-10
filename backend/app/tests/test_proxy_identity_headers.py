# 내부 앱 라우트도 신원 헤더를 업스트림에 넘기는지 — 안 넘기면 '담당자' 칸이 영영 빈다
from app.services.proxy_manager import _IDENTITY_HEADERS, _build_route


def _handle_chain(route: dict) -> list[dict]:
    """Caddy 라우트에서 실제 핸들러 사슬을 꺼낸다(테스트 관례는 test_static_stacks 와 같다)."""
    return route["handle"][0]["routes"][0]["handle"]


def _identity_copy_routes(gate: dict) -> list[dict]:
    """forward_auth 게이트의 2xx 통과 경로에 달린 헤더 복사 라우트."""
    return gate["handle_response"][0].get("routes") or []


def test_internal_app_route_copies_identity_to_upstream():
    """⚠ 이 테스트가 없으면 조용히 되돌아간다.

    예전에는 외부 업스트림 라우트에만 copy_identity 가 붙어 있어서, 허브 안에서 도는 앱은
    누가 부르는지 알 수 없었다. StepForge 가 과제를 만들어도 담당자 칸이 늘 비었던 원인이
    이것이고, 그 사실이 그쪽 코드에 "고치려면 허브 쪽" 이라고 적힌 채 남아 있었다.
    """
    route = _build_route("step_forge", 9300, None)
    gate = _handle_chain(route)[0]
    assert gate["handler"] == "reverse_proxy"           # forward_auth 는 reverse_proxy 로 전개된다
    copy = _identity_copy_routes(gate)
    assert copy, "내부 앱 라우트에 신원 복사가 없다 — 업스트림이 호출자를 알 수 없다"
    setters = copy[0]["handle"][0]["request"]["set"]
    for h in _IDENTITY_HEADERS:
        assert h in setters, f"{h} 를 업스트림으로 넘기지 않는다"
        # 값은 authz 응답에서 가져와야 한다 — 클라이언트가 보낸 값을 그대로 쓰면 위조가 통한다.
        assert setters[h] == [f"{{http.reverse_proxy.header.{h}}}"]


def test_identity_headers_are_set_not_added():
    """`set` 이어야 클라이언트가 위조해 보낸 동명 헤더를 항상 덮는다(add 면 둘 다 남는다)."""
    gate = _handle_chain(_build_route("any_app", 9999, None))[0]
    req = _identity_copy_routes(gate)[0]["handle"][0]["request"]
    assert "set" in req and "add" not in req


def test_gate_still_blocks_non_2xx():
    """신원 복사를 붙이면서 인가 게이트 자체가 느슨해지지 않았는지."""
    gate = _handle_chain(_build_route("any_app", 9999, None))[0]
    assert gate["rewrite"]["method"] == "GET"          # 본문을 소비하지 않는다
    assert gate["handle_response"][0]["match"]["status_code"] == [2]
