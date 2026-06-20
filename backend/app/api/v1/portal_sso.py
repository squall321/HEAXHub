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
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["portal-sso"])


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
            options={"require": ["exp", "aud", "sub", "jti"], "leeway": 30},
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
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})
