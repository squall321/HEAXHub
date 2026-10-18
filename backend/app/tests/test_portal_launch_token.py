# 포털 진입 토큰(scope=launch) 검증이 필수 클레임을 실제로 요구하는지 — python-jose 가 "require": [...] 를 말없이 버리던 결함(10-18)
"""왜 — `_verify_portal_token` 은 options={"require": ["exp","aud","sub","jti"]} 로 필수를 걸었다고 적었지만 그건 PyJWT 꼴이고
python-jose 3.5 는 그 키를 모른 채 버린다. 그래서 aud 가 없는 토큰은 청중 확인을 건너뛰어 통과했고(실측), jti · exp 가 없는 토큰은
재사용 막기에서 KeyError 로 500 이 났다. 포털 관문 PAT 검증을 짜다 시험이 잡았다(HWAXPortal docs/followups-1017 결정 7).

  실행:  .venv/bin/python -m pytest app/tests/test_portal_launch_token.py -q
"""
from __future__ import annotations

import time
import uuid

import pytest
from fastapi import HTTPException
from jose import jwt

from app.api.v1 import portal_sso
from app.config import get_settings
from app.tests.test_authz_portal_pat import JWKS_URL, KID, PRIV, _jwk


@pytest.fixture(autouse=True)
def _portal(monkeypatch):
    monkeypatch.setattr(get_settings(), "portal_jwks_url", JWKS_URL)
    monkeypatch.setattr(portal_sso, "_jwks_cache", {"keys": [_jwk()], "fetched": time.time()})
    monkeypatch.setattr(portal_sso, "_seen_jti", {})


def _launch(**over) -> str:
    now = int(time.time())
    claims = {"sub": "me@example.com", "email": "me@example.com", "aud": "heax-hub", "scope": "launch",
              "iat": now, "nbf": now, "exp": now + 90, "jti": uuid.uuid4().hex}
    claims.update(over)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, PRIV, algorithm="RS256",
                      headers={"kid": KID})


def test_정상_진입_토큰은_통과하고_두_번은_못_쓴다() -> None:
    tok = _launch()
    assert portal_sso._verify_portal_token(tok)["sub"] == "me@example.com"
    with pytest.raises(HTTPException) as e:
        portal_sso._verify_portal_token(tok)
    assert e.value.status_code == 401


@pytest.mark.parametrize("missing", ["aud", "jti", "exp", "sub"])
def test_필수_클레임이_없으면_401(missing) -> None:
    with pytest.raises(HTTPException) as e:
        portal_sso._verify_portal_token(_launch(**{missing: None}))
    assert e.value.status_code == 401, f"{missing} 없는 토큰 — 500 이나 통과가 아니라 401"
