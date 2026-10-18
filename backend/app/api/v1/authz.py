# Caddy/Traefik forward_auth 용 인가 판정 엔드포인트 (쿠키 JWT · 허브 PAT · 포털 PAT → App visibility 검사).
"""Forward-auth endpoint for reverse-proxy ``forward_auth`` / ``auth_request``.

프록시가 ``/apps/{slug}/`` 요청을 가로채 이 엔드포인트로 서브리퀘스트를 보낸다.
원본 경로는 ``X-Forwarded-Uri`` 헤더로 전달된다. 200이면 통과, 401/403이면 차단.
"""
from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Header, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.api.v1.portal_sso import (
    PortalPatRejected,
    is_portal_pat,
    portal_pat_read_only,
    verify_portal_pat,
)
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
) -> tuple[User | None, dict | None]:
    """쿠키 우선, 없으면 Authorization 헤더로 사용자 해석. 토큰 없으면 (None, None).

    둘째 값은 포털 PAT 으로 들어왔을 때의 클레임(읽기 전용 판정용), 아니면 None."""
    token = cookie_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        return None, None
    # 헤드리스 클라이언트(MCP·CI)의 PAT — 세션 JWT와 동일 권한으로 해석 (검증 실패 → 401).
    if is_pat_token(token):
        return pat_service.resolve_user(db, token), None
    # 포털 PAT(RS256) — 토큰 하나로 하위 전부(HWAXPortal docs/followups-1017 결정 7). 사람은 이메일로 허브 사용자에
    # 잇고 권한은 허브 것(can_view_app)만 본다. 허브 계정을 여기서 새로 만들지 않는다 — 포털 타일로 한 번 들어와야 생긴다.
    if is_portal_pat(token):
        claims = verify_portal_pat(token)
        email = str(claims.get("email") or "").strip().lower()
        user = db.execute(
            select(User).where(func.lower(User.email) == email)
        ).scalar_one_or_none() if email else None
        if user is None:
            raise PortalPatRejected(
                403, "허브 계정이 없다 — 포털에서 HEAX Hub 타일을 한 번 눌러 허브에 로그인한 뒤 다시 시도한다")
        if user.status == UserStatus.DISABLED:
            raise UnauthorizedError("User not found or disabled")
        return user, claims
    # 검증 실패(만료·위변조)는 미인증으로 취급해 401 로 흐르게 한다.
    payload = decode_token(token, expected_type="access")
    user = db.get(User, payload["sub"])
    if user is None or user.status == UserStatus.DISABLED:
        raise UnauthorizedError("User not found or disabled")
    return user, None


def _is_public_app(app: App) -> bool:
    """쿠키/로그인 없이 열람 가능한 공개 앱인가. (COMPANY visibility + STABLE status)"""
    return (
        app.visibility == AppVisibility.COMPANY
        and app.status == AppStatus.STABLE
    )


def _needs_portal_identity(app: App) -> bool:
    """매니페스트 ``launch.portal_auth: true`` 앱인가 (스캐너가 App.extra 에 내려둔다).

    이 앱들은 게이트가 2xx 를 주면 프록시가 ``X-Heax-User-*`` 를 업스트림에 복사하고,
    업스트림은 그 신원으로 자기 세션을 만든다. 즉 **신원 없이는 동작 자체가 불가능**하다.
    """
    extra = app.extra if isinstance(app.extra, dict) else {}
    return bool(extra.get("portal_auth", False))


@router.get("/authz")
def authz(
    db: DbSession,
    response: Response,
    x_forwarded_uri: Annotated[str | None, Header()] = None,
    x_forwarded_method: Annotated[str | None, Header()] = None,
    cookie_token: Annotated[str | None, Header(alias="cookie")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    """forward_auth 판정. 200(허용)/401(미인증)/403(권한없음).

    - 토큰이 없고 앱이 공개(COMPANY+STABLE)면 200.
    - 토큰이 없고 비공개면 401.
    - 토큰이 유효하고 ``can_view_app`` 통과면 200, 아니면 403.
    - 포털 PAT 의 거절은 까닭을 본문(JSON ``detail``)에 싣는다 — 프록시가 그대로 돌려준다.
      읽기 전용 포털 PAT 은 원 요청(``X-Forwarded-Method``)이 GET · HEAD 일 때만 통과한다.
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
        user, portal_claims = _user_from_request(db, token, authorization)
    except UnauthorizedError:
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)
    except PortalPatRejected as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    if user is None:
        # ⚠ portal_auth 앱은 '공개'여도 익명으로 통과시키면 안 된다. 게이트가 2xx 를 주면
        #   프록시가 X-Heax-User-* 를 복사하는데, 익명이면 그 헤더가 **빈 값**이라 업스트림이
        #   자기 SSO 에서 401 을 낸다. 사용자에게는 로그인 안내가 아니라 **흰 화면**만 남는다
        #   (실측: DynaForge — HTML 200, 자산 200, #root 비어 있음, 콘솔에 SSO 401 하나).
        #   게이트가 통과시켰는데 앱은 못 쓰는 모순이라, 여기서 401 을 줘 로그인으로 보낸다.
        if _is_public_app(app) and not _needs_portal_identity(app):
            return Response(status_code=status.HTTP_200_OK)
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    if permission_service.can_view_app(db, app, user):
        if (portal_claims is not None and portal_pat_read_only(portal_claims)
                and (x_forwarded_method or "").upper() not in ("GET", "HEAD")):
            return JSONResponse(
                {"detail": "읽기 전용 포털 토큰 — GET · HEAD 만 연다"},
                status_code=status.HTTP_403_FORBIDDEN,
            )
        # portal_auth 프록시 앱용 identity 전파 — forward_auth 게이트가 2xx 응답의
        # 이 헤더들을 업스트림 요청으로 복사한다 (proxy_manager._forward_auth_handler).
        ok = Response(status_code=status.HTTP_200_OK)
        # ⚠ **HTTP 헤더는 latin-1 만 담는다.** 한글 표시명을 그대로 넣으면 Starlette 가 인코딩에서
        # `UnicodeEncodeError` 를 내고 이 게이트가 **500** 이 된다. forward_auth 가 500 을 받으면 그
        # 사용자는 `/apps/*` 를 **하나도** 못 쓴다 — 앱이 멀쩡해도 전부 막힌다(실측 2026-09-18:
        # dev 사용자 44명 중 6명이 비latin-1 표시명, backend.log 에 `GET /api/v1/authz 500` 셋).
        # 이름은 **표시용**이라 못 실으면 빼고 보낸다(소비자는 없으면 이름 없이 둔다 — KooRemapper
        # sso_login 의 JIT 프로비저닝). 퍼센트 인코딩으로 바꾸려면 소비자가 unquote 해야 하므로
        # 앱들과 합의가 먼저다 — 지금 바꾸면 ASCII 이름의 공백이 %20 으로 보인다.
        # 이메일도 같은 제약을 받는다. 못 실으면 빈 값이라 앱이 401 로 로그인을 안내한다(500 보다 낫다).
        ok.headers["X-Heax-User-Email"] = _latin1_or_empty(user.email)
        ok.headers["X-Heax-User-Name"] = _latin1_or_empty(user.display_name)
        return ok
    return Response(status_code=status.HTTP_403_FORBIDDEN)


def _latin1_or_empty(value: str | None) -> str:
    """헤더에 실을 수 있으면 그대로, 못 실으면 빈 문자열 — 인코딩 오류로 게이트가 500 이 되지 않게."""
    text = value or ""
    try:
        text.encode("latin-1")
    except UnicodeEncodeError:
        return ""
    return text


def _extract_cookie_value(cookie_header: str | None, name: str) -> str | None:
    """원시 ``Cookie`` 헤더 문자열에서 지정한 쿠키 값을 꺼낸다."""
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key == name:
            return value.strip()
    return None
