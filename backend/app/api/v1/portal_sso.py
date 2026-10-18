"""HWAX Portal SSO downstream consumer — true single sign-on for HEAX Hub.

Flow: the user is logged into the HWAX portal and clicks the HEAX Hub tile. The portal mints a
short-lived RS256 "launch" JWT (aud = "heax-hub") and auto-POSTs it to /api/v1/auth/portal-callback.
We:
  1. fetch the portal's JWKS (cached 300s) and verify the token (RS256, aud, exp, scope=launch),
  2. link-by-email / JIT-create the User (sets sso_subject; auth_source=SSO for new accounts),
  3. issue HEAX's OWN session via auth_service._tokens_for (same machinery as password login),
  4. return a tiny same-origin HTML bootstrap page that writes the zustand-persisted localStorage
     key 'heaxhub.auth' and redirects into the SPA at /heax-hub/ — already logged in.

HEAX stores tokens in localStorage (no auth cookie anywhere), so the callback hands the tokens to
the SPA explicitly via this bootstrap page rather than via a cookie.

Disabled (404) unless `portal_jwks_url` is set, so standalone deploys are unaffected: local
email/password login, PATs, and launcher tokens keep working untouched.
"""
from __future__ import annotations

import json
import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse
from jose import jwt
from sqlalchemy import func, select

from app.config import get_settings
from app.core.security import hash_password
from app.db.models.user import AuthSource, User, UserRole, UserStatus
from app.deps import DbSession
from app.api.v1.auth import _set_session_cookie
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["portal-sso"])
logger = logging.getLogger(__name__)


# Tiny in-process JWKS cache + replay guard (single-process uvicorn). For multi-replica, back these
# with Redis — same seam as the rest of the app.
_jwks_cache: dict[str, Any] = {"keys": None, "fetched": 0.0}
_seen_jti: dict[str, float] = {}
_JWKS_TTL = 300


def _fetch_jwks() -> list[dict[str, Any]]:
    settings = get_settings()
    with httpx.Client(timeout=5) as client:
        r = client.get(settings.portal_jwks_url)
        r.raise_for_status()
        keys = r.json().get("keys", [])
    _jwks_cache["keys"] = keys
    _jwks_cache["fetched"] = time.time()
    return keys


def _portal_jwks(force: bool = False) -> list[dict[str, Any]]:
    now = time.time()
    if (
        not force
        and _jwks_cache["keys"] is not None
        and now - _jwks_cache["fetched"] < _JWKS_TTL
    ):
        return _jwks_cache["keys"]
    return _fetch_jwks()


def _select_key(kid: str | None) -> dict[str, Any]:
    """Select the JWKS key STRICTLY by kid. On a miss, force ONE cache refresh (the key may be
    freshly rotated and not in the 300s-cached set) and re-check. No silent keys[0] fallback."""
    keys = _portal_jwks()
    key = next((k for k in keys if k.get("kid") == kid), None)
    if key is None:
        keys = _portal_jwks(force=True)
        key = next((k for k in keys if k.get("kid") == kid), None)
    if key is None:
        raise HTTPException(status_code=401, detail="No matching JWKS key for token kid")
    return key


def _gc_jti(now: float) -> None:
    for k, exp in list(_seen_jti.items()):
        if exp < now:
            del _seen_jti[k]


def _verify_portal_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        header = jwt.get_unverified_header(token)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="Malformed launch token") from e

    key = _select_key(header.get("kid"))

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=settings.portal_audience,
            # leeway goes in options — python-jose's decode() has NO leeway kwarg (it would
            # TypeError). Tolerates minor portal/HEAX clock skew on split-host deploys; jose
            # validates nbf/iat by default and the portal sets nbf=iat=now.
            # ⚠ python-jose silently ignores a PyJWT-style "require": [...] list — each claim needs
            # its own require_* flag. With the list form a token without aud skipped the audience
            # check and one without jti/exp 500'd on the replay guard below instead of a 401.
            options={"require_exp": True, "require_aud": True, "require_sub": True,
                     "require_jti": True, "leeway": 30},
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="Launch token rejected") from e

    if claims.get("scope") != "launch":
        raise HTTPException(status_code=401, detail="Not a launch token")

    now = time.time()
    _gc_jti(now)
    jti = claims["jti"]
    if jti in _seen_jti:
        raise HTTPException(status_code=401, detail="Launch token already used")
    _seen_jti[jti] = float(claims["exp"])
    return claims


# ---- 포털 PAT(scope=api) — /apps/* 관문(authz)이 받는다 ------------------------------------
# HWAXPortal docs/followups-1017 결정 7(10-18) — 사람별 권한은 허브 권한(can_view_app)만 본다.
# 포털 PAT 은 무기한까지 나가서 폐기(jti)가 유일한 통제다 — 폐기 목록을 한 번도 못 읽었으면 받지 않는다.
_REVOKED_PATH = "/auth/pat/revoked.json"
_REVOKED_TTL = 60     # 포털이 Cache-Control max-age=60 으로 낸다(게이트웨이도 60초)
_REVOKED_RETRY = 10   # 못 읽으면 직전 목록으로 버티고 10초 뒤 다시 — 앱 자산 요청마다 포털을 기다리지 않게
_revoked_cache: dict[str, Any] = {"jtis": None, "next": 0.0}


class PortalPatRejected(Exception):
    """포털 PAT 거절 — 관문이 이 상태와 까닭을 그대로 돌려준다(401 하나로 뭉개면 까닭이 안 보인다)."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _revoked_url() -> str:
    """폐기 목록은 JWKS 와 같은 포털 앱에 있다 — `<포털>/.well-known/jwks.json` → `<포털>/auth/pat/revoked.json`."""
    jwks = get_settings().portal_jwks_url
    if "/.well-known/" not in jwks:
        return ""
    return jwks.rsplit("/.well-known/", 1)[0] + _REVOKED_PATH


def _revoked_jtis() -> set[str] | None:
    """폐기된 jti 집합. 한 번도 못 읽었으면 None(받지 않는다)."""
    now = time.time()
    if now < _revoked_cache["next"]:
        return _revoked_cache["jtis"]
    url = _revoked_url()
    try:
        if not url:
            raise ValueError("PORTAL_JWKS_URL 이 '…/.well-known/…' 꼴이 아니라 폐기 목록 주소를 못 짓는다")
        with httpx.Client(timeout=3) as client:
            r = client.get(url)
            r.raise_for_status()
            body = r.json()
        # ⚠ 'revoked' 칸이 없는 응답을 빈 목록으로 받으면 폐기된 토큰이 통과한다(게이트웨이 rest_proxy 와 같은 판정).
        if not isinstance(body, dict) or not isinstance(body.get("revoked"), list):
            raise ValueError("폐기 목록 형식이 {'revoked': [jti…]} 가 아니다")
        _revoked_cache["jtis"] = {str(j) for j in body["revoked"]}
        _revoked_cache["next"] = now + _REVOKED_TTL
    except Exception as exc:  # noqa: BLE001 — 값은 남기지 않는다
        held = _revoked_cache["jtis"]
        logger.warning("포털 PAT 폐기 목록 갱신 실패(%s) — %s", type(exc).__name__,
                       f"직전 목록({len(held)}건) 유지" if held is not None else "포털 PAT 을 받지 않는다")
        _revoked_cache["next"] = now + _REVOKED_RETRY
    return _revoked_cache["jtis"]


def is_portal_pat(token: str) -> bool:
    """포털이 서명한 토큰(RS256)인가 — 허브 자기 JWT 는 HS256 이라 alg 로 가른다. 포털 SSO 가 꺼져 있으면 늘 아니다."""
    if not get_settings().portal_jwks_url:
        return False
    try:
        return jwt.get_unverified_header(token).get("alg") == "RS256"
    except Exception:  # noqa: BLE001 — JWT 가 아니면 다른 길이 판정한다
        return False


def verify_portal_pat(token: str) -> dict[str, Any]:
    """포털 PAT → 클레임(RS256 · 포털 JWKS · aud heax-hub · scope api · 폐기 목록). 거절은 PortalPatRejected."""
    settings = get_settings()
    try:
        key = _select_key(jwt.get_unverified_header(token).get("kid"))
    except HTTPException as e:
        raise PortalPatRejected(401, "포털 공개키(JWKS)에 이 토큰의 kid 가 없다") from e
    except Exception as e:  # noqa: BLE001 — 포털이 안 닿는다
        raise PortalPatRejected(503, "포털 공개키(JWKS)를 못 읽었다 — 잠시 뒤 다시") from e
    try:
        # ⚠ python-jose 는 PyJWT 식 `"require": [...]` 를 말없이 버린다 — 칸마다 `require_*` 로 준다(없으면 jti 없는 토큰이 500).
        claims = jwt.decode(
            token, key, algorithms=["RS256"], audience=settings.portal_audience,
            options={"require_exp": True, "require_aud": True, "require_sub": True, "require_jti": True,
                     "leeway": 30},
        )
    except Exception as e:  # noqa: BLE001
        raise PortalPatRejected(
            401, f"포털 토큰이 거절됐다(서명 · 만료 · 청중 {settings.portal_audience})") from e
    if claims.get("scope") != "api":
        raise PortalPatRejected(401, "포털 PAT(scope=api)이 아니다")
    revoked = _revoked_jtis()
    if revoked is None:
        raise PortalPatRejected(503, "포털 폐기 목록을 못 읽어 포털 토큰을 받지 않는다 — 잠시 뒤 다시")
    if claims["jti"] in revoked:
        raise PortalPatRejected(401, "폐기된 포털 토큰이다")
    return claims


def portal_pat_read_only(claims: dict[str, Any]) -> bool:
    """scopes 에 read 가 있고 write 가 없을 때만 — 게이트웨이 rest_proxy.is_read_only 와 같은 판정.

    칸이 없는 옛 PAT 은 포털 기본(read · write)이라 아니다. 챗 · 절차 PAT 의 `["chat"]` 도 아니다."""
    raw = claims.get("scopes")
    if not isinstance(raw, list):
        return False
    s = {str(x).strip().lower() for x in raw}
    return "read" in s and "write" not in s


def _link_or_create_user(db: DbSession, claims: dict[str, Any]) -> User:
    email = claims["email"].lower()
    user = db.execute(
        select(User).where(func.lower(User.email) == email)
    ).scalar_one_or_none()

    if user is not None:
        # LINK existing account. Set sso_subject only if currently empty. KEEP auth_source=LOCAL
        # when a password_hash is present so password login still works (login() refuses non-LOCAL).
        if not user.sso_subject:
            user.sso_subject = claims["sub"]
        user.last_login_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(user)
        return user

    # JIT-create on first SSO login. Random unusable password_hash (local password path can never
    # match it). auth_source=SSO marks the account as SSO-provisioned.
    user = User(
        email=email,
        display_name=claims.get("name") or email.split("@")[0],
        organization="",
        password_hash=hash_password(secrets.token_urlsafe(32)),
        auth_source=AuthSource.SSO,
        sso_subject=claims["sub"],
        status=UserStatus.ACTIVE,
        role=UserRole.USER,
        email_verified=True,
        last_login_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _bootstrap_html(payload_json: str, landing: str) -> str:
    # payload_json is a json.dumps() string => a valid JS string literal source. Embedding it as the
    # argument to JSON.parse rebuilds the object, which JSON.stringify re-serializes for localStorage.
    # Escape '<' so a display_name/email containing '</script>' can't break out of the inline
    # <script> (defense-in-depth; values come from the trusted RS256 portal token). '<' is valid
    # inside the JSON string literal and JSON.parse restores it to '<'.
    safe = payload_json.replace("<", "\\u003c")
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>Signing in…</title></head><body>"
        "<script>"
        f"localStorage.setItem('heaxhub.auth', JSON.stringify(JSON.parse({safe})));"
        f"location.replace({json.dumps(landing)});"
        "</script>"
        "<noscript>JavaScript is required to complete sign-in.</noscript>"
        "</body></html>"
    )


@router.post("/portal-callback")
def portal_callback(db: DbSession, token: str = Form(...)) -> HTMLResponse:
    settings = get_settings()
    if not settings.portal_jwks_url:
        raise HTTPException(status_code=404, detail="Portal SSO not enabled")

    claims = _verify_portal_token(token)
    user = _link_or_create_user(db, claims)

    tokens = auth_service._tokens_for(db, user)

    payload = json.dumps(
        {
            "state": {
                "accessToken": tokens.access_token,
                "refreshToken": tokens.refresh_token,
                "user": tokens.user.model_dump(mode="json"),
                "expiresAt": int((time.time() + tokens.expires_in) * 1000),
            },
            "version": 0,
        }
    )

    html = _bootstrap_html(json.dumps(payload), settings.portal_sso_landing)
    resp = HTMLResponse(content=html, headers={"Cache-Control": "no-store"})
    # ⚠ localStorage 만 채우면 SPA 안의 fetch 는 되지만 **브라우저가 직접 여는 URL** 은 못 뚫는다.
    # /apps/{slug}/ 는 Caddy forward_auth 가 게이트하고 그 게이트는 쿠키(또는 Authorization)만
    # 본다 — 주소창 이동에는 JS 가 개입할 수 없어 둘 다 없다. 그래서 포털에서 정상적으로
    # 들어와도 portal_auth 앱(DynaForge 등)이 401 로 막혔다. 로그인·리프레시는 이미 이 쿠키를
    # 심고 있었는데 포털 콜백만 빠져 있었다.
    _set_session_cookie(resp, tokens.access_token)
    return resp
