# Caddy/Traefik forward_auth 용 인가 판정 엔드포인트 (쿠키 JWT → App visibility 검사).
"""Forward-auth endpoint for reverse-proxy ``forward_auth`` / ``auth_request``.

프록시가 ``/apps/{slug}/`` 요청을 가로채 이 엔드포인트로 서브리퀘스트를 보낸다.
원본 경로는 ``X-Forwarded-Uri`` 헤더로 전달된다. 200이면 통과, 401/403이면 차단.
"""
from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Header, Response, status

from app.core.errors import UnauthorizedError
from app.core.security import decode_token, is_pat_token
from app.db.models.app import App, AppStatus, AppVisibility
from app.db.models.user import User, UserStatus
from app.deps import DbSession
from app.services import pat_service, permission_service

router = APIRouter(tags=["authz"])

# forward_auth 쿠키 이름 — auth.py 의 SESSION_COOKIE_NAME 과 동일해야 한다.
_SESSION_COOKIE_NAME = "heax_access_token"
# 원본 URI 에서 앱 slug 추출: /apps/<slug>/... (선행 프록시 prefix 는 무시).
_APP_PATH_RE = re.compile(r"/apps/(?P<slug>[^/?#]+)")


def _slug_from_uri(forwarded_uri: str | None) -> str | None:
    """``X-Forwarded-Uri`` 에서 ``/apps/{slug}/`` 패턴의 slug 를 뽑는다."""
    if not forwarded_uri:
        return None
    match = _APP_PATH_RE.search(forwarded_uri)
    return match.group("slug") if match else None


def _user_from_request(
    db,
    cookie_token: str | None,
    authorization: str | None,
) -> User | None:
    """쿠키 우선, 없으면 Authorization 헤더로 사용자 해석. 토큰 없으면 None."""
    token = cookie_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        return None
    # 헤드리스 클라이언트(MCP·CI)의 PAT — 세션 JWT와 동일 권한으로 해석 (검증 실패 → 401).
    if is_pat_token(token):
        return pat_service.resolve_user(db, token)
    # 검증 실패(만료·위변조)는 미인증으로 취급해 401 로 흐르게 한다.
    payload = decode_token(token, expected_type="access")
    user = db.get(User, payload["sub"])
    if user is None or user.status == UserStatus.DISABLED:
        raise UnauthorizedError("User not found or disabled")
    return user


def _is_public_app(app: App) -> bool:
    """쿠키/로그인 없이 열람 가능한 공개 앱인가. (COMPANY visibility + STABLE status)"""
    return (
        app.visibility == AppVisibility.COMPANY
        and app.status == AppStatus.STABLE
    )


@router.get("/authz")
def authz(
    db: DbSession,
    response: Response,
    x_forwarded_uri: Annotated[str | None, Header()] = None,
    cookie_token: Annotated[str | None, Header(alias="cookie")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    """forward_auth 판정. 200(허용)/401(미인증)/403(권한없음).

    - 토큰이 없고 앱이 공개(COMPANY+STABLE)면 200.
    - 토큰이 없고 비공개면 401.
    - 토큰이 유효하고 ``can_view_app`` 통과면 200, 아니면 403.
    """
    slug = _slug_from_uri(x_forwarded_uri)
    if slug is None:
        # /apps/{slug}/ 가 아닌 경로는 이 엔드포인트가 보호 대상이 아니다 → 통과.
        return Response(status_code=status.HTTP_200_OK)

    app = db.get(App, slug)
    if app is None:
        # 존재하지 않는 앱: 정보 노출 최소화를 위해 미인증으로 취급.
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    # Header(alias="cookie") 는 원시 Cookie 헤더 전체를 받는다 → 쿠키 값만 파싱.
    token = _extract_cookie_value(cookie_token, _SESSION_COOKIE_NAME)

    try:
        user = _user_from_request(db, token, authorization)
    except UnauthorizedError:
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    if user is None:
        if _is_public_app(app):
            return Response(status_code=status.HTTP_200_OK)
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    if permission_service.can_view_app(db, app, user):
        return Response(status_code=status.HTTP_200_OK)
    return Response(status_code=status.HTTP_403_FORBIDDEN)


def _extract_cookie_value(cookie_header: str | None, name: str) -> str | None:
    """원시 ``Cookie`` 헤더 문자열에서 지정한 쿠키 값을 꺼낸다."""
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key == name:
            return value.strip()
    return None
