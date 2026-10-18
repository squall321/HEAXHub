# 허브 관문(/api/v1/authz)이 포털 PAT 을 받는지 — 공개키 · 청중 · scope · 폐기 목록 · 허브 계정 · 읽기 전용(HWAXPortal followups-1017 결정 7)
"""사용자 결정(10-18, 질문 카드) — 허브 앱(/apps/*)을 포털 토큰으로 열 때 사람별 권한은 **허브 권한(can_view_app)만** 본다.
조사(10-18) — 관문은 쿠키 → 허브 PAT → 허브 HS256 JWT 순으로만 받아 포털 RS256 PAT 은 늘 401 이었다(토큰 하나로 하위 전부에서
허브 앱만 빠졌다). 포털 PAT 은 무기한까지 나가므로 폐기 목록을 한 번도 못 읽었으면 받지 않는다.

  실행:  .venv/bin/python -m pytest app/tests/test_authz_portal_pat.py -q
"""
from __future__ import annotations

import time
import uuid

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jose import jwk, jwt
from sqlalchemy.orm import Session

from app.api.v1 import portal_sso
from app.config import get_settings
from app.db.models.app import AppStatus, AppVisibility
from app.tests.test_authz_gate import _cleanup, _fwd, _mk_app, _mk_user, client, db  # noqa: F401

KID = "portal-test-kid"
JWKS_URL = "http://portal.test/.well-known/jwks.json"


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(serialization.Encoding.PEM,
                                        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv, pub


PRIV, PUB = _keypair()
OTHER_PRIV, _ = _keypair()


def _jwk() -> dict:
    return {**jwk.construct(PUB, "RS256").to_dict(), "kid": KID, "use": "sig", "alg": "RS256"}


@pytest.fixture(autouse=True)
def portal(monkeypatch):
    """포털 SSO 켬 · 공개키 캐시 채움 · 폐기 목록 비어 있음(막 읽음). 네트워크는 타지 않는다."""
    monkeypatch.setattr(get_settings(), "portal_jwks_url", JWKS_URL)
    monkeypatch.setattr(portal_sso, "_jwks_cache", {"keys": [_jwk()], "fetched": time.time()})
    monkeypatch.setattr(portal_sso, "_revoked_cache", {"jtis": set(), "next": time.time() + 60})

    def _no_network(*_a, **_k):
        raise AssertionError("이 시험은 포털을 부르지 않아야 한다")
    monkeypatch.setattr(portal_sso, "_fetch_jwks", _no_network)


def _pat(email: str, *, priv: str = PRIV, kid: str = KID, **over) -> str:
    now = int(time.time())
    claims = {"iss": "hwax-portal", "sub": email, "email": email, "aud": ["mx-white-paper", "heax-hub", "mcp-gateway"],
              "scope": "api", "scopes": ["read", "write"], "iat": now, "nbf": now, "exp": now + 3600,
              "jti": uuid.uuid4().hex}
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, priv, algorithm="RS256", headers={"kid": kid})


def _ask(client: TestClient, slug: str, token: str, method: str | None = "GET"):
    headers = {**_fwd(slug), "Authorization": f"Bearer {token}"}
    if method:
        headers["X-Forwarded-Method"] = method
    return client.get("/api/v1/authz", headers=headers)


@pytest.fixture()
def owned(db: Session):
    owner = _mk_user(db)
    stranger = _mk_user(db)
    app = _mk_app(db, owner, visibility=AppVisibility.PRIVATE, status_=AppStatus.STABLE)
    yield owner, stranger, app
    _cleanup(db, app, owner, stranger)


def test_포털_PAT_으로_허브_권한대로_연다(client: TestClient, owned) -> None:
    owner, stranger, app = owned
    r = _ask(client, app.id, _pat(owner.email.upper()))
    assert r.status_code == 200, r.text
    assert r.headers.get("X-Heax-User-Email") == owner.email, "이메일(대소문자 무시)로 허브 사용자에 잇고 신원을 앱에 싣는다"
    assert _ask(client, app.id, _pat(stranger.email)).status_code == 403, "권한은 허브 것(can_view_app)만"


def test_허브_계정이_없으면_한_번_로그인을_안내한다(client: TestClient, owned) -> None:
    _, _, app = owned
    r = _ask(client, app.id, _pat(f"nobody-{uuid.uuid4().hex[:6]}@example.com"))
    assert r.status_code == 403 and "허브 계정이 없다" in r.json()["detail"], r.text


@pytest.mark.parametrize("over, why", [
    ({"aud": ["mcp-gateway"]}, "청중에 heax-hub 가 없다(챗 · 절차 PAT)"),
    ({"scope": "launch"}, "90초 진입 토큰은 PAT 이 아니다"),
    ({"exp": int(time.time()) - 3600}, "만료"),
    ({"jti": None}, "jti 없음 — 폐기할 수 없는 토큰"),
])
def test_받지_않는_포털_토큰(client: TestClient, owned, over, why) -> None:
    owner, _, app = owned
    r = _ask(client, app.id, _pat(owner.email, **over))
    assert r.status_code == 401, f"{why}: {r.status_code} {r.text}"
    assert r.json()["detail"], "까닭을 본문에 싣는다"


def test_다른_키로_서명한_같은_kid_는_거절(client: TestClient, owned) -> None:
    owner, _, app = owned
    assert _ask(client, app.id, _pat(owner.email, priv=OTHER_PRIV)).status_code == 401


def test_모르는_kid_는_한_번_새로_읽고도_없으면_거절(client: TestClient, owned, monkeypatch) -> None:
    owner, _, app = owned
    fetched = []
    monkeypatch.setattr(portal_sso, "_fetch_jwks", lambda: fetched.append(1) or [_jwk()])
    r = _ask(client, app.id, _pat(owner.email, kid="rotated-away"))
    assert r.status_code == 401 and "kid" in r.json()["detail"] and fetched == [1]


def test_포털_공개키를_못_읽으면_503(client: TestClient, owned, monkeypatch) -> None:
    owner, _, app = owned
    monkeypatch.setattr(portal_sso, "_jwks_cache", {"keys": None, "fetched": 0.0})

    def _down():
        raise httpx.ConnectError("portal down")
    monkeypatch.setattr(portal_sso, "_fetch_jwks", _down)
    r = _ask(client, app.id, _pat(owner.email))
    assert r.status_code == 503 and "JWKS" in r.json()["detail"], "포털이 안 닿는 것을 '나쁜 토큰'(401)과 가른다"


def test_폐기된_토큰은_거절(client: TestClient, owned, monkeypatch) -> None:
    owner, _, app = owned
    tok = _pat(owner.email, jti="revoked-jti")
    monkeypatch.setattr(portal_sso, "_revoked_cache", {"jtis": {"revoked-jti"}, "next": time.time() + 60})
    r = _ask(client, app.id, tok)
    assert r.status_code == 401 and "폐기" in r.json()["detail"]


def _revoked_server(monkeypatch, handler):
    seen = []
    real = httpx.Client

    def _client(*_a, **_k):
        def _h(request):
            seen.append(str(request.url))
            return handler(request)
        return real(transport=httpx.MockTransport(_h))
    monkeypatch.setattr(portal_sso.httpx, "Client", _client)
    return seen


def test_폐기_목록을_한_번도_못_읽었으면_받지_않고_읽히면_연다(client: TestClient, owned, monkeypatch) -> None:
    owner, _, app = owned
    body = {"v": None}
    seen = _revoked_server(monkeypatch, lambda _r: httpx.Response(200, json=body["v"]))
    # 'revoked' 칸이 없거나 목록이 아닌 응답 — 빈 목록으로 받으면 폐기된 토큰이 통과한다({} 는 set() 가 된다)
    for bad in ({"oops": []}, {"revoked": {}}):
        body["v"] = bad
        monkeypatch.setattr(portal_sso, "_revoked_cache", {"jtis": None, "next": 0.0})
        seen.clear()
        r = _ask(client, app.id, _pat(owner.email))
        assert r.status_code == 503 and "폐기 목록" in r.json()["detail"], f"{bad}: {r.text}"
        assert seen == ["http://portal.test/auth/pat/revoked.json"], "폐기 목록은 JWKS 와 같은 포털에서"
    assert _ask(client, app.id, _pat(owner.email)).status_code == 503 and len(seen) == 1, "실패 뒤 10초는 다시 묻지 않는다"

    body["v"] = {"revoked": ["x"]}
    monkeypatch.setattr(portal_sso, "_revoked_cache", {**portal_sso._revoked_cache, "next": 0.0})
    assert _ask(client, app.id, _pat(owner.email)).status_code == 200
    assert portal_sso._revoked_cache["jtis"] == {"x"}


def test_갱신이_실패하면_직전_목록으로_버틴다(client: TestClient, owned, monkeypatch) -> None:
    owner, _, app = owned
    monkeypatch.setattr(portal_sso, "_revoked_cache", {"jtis": {"gone"}, "next": 0.0})
    _revoked_server(monkeypatch, lambda _r: httpx.Response(502))
    assert _ask(client, app.id, _pat(owner.email)).status_code == 200
    assert _ask(client, app.id, _pat(owner.email, jti="gone")).status_code == 401, "직전 목록의 폐기는 그대로 막는다"


def test_읽기_전용_포털_토큰은_GET_HEAD_만(client: TestClient, owned) -> None:
    owner, _, app = owned
    ro = _pat(owner.email, scopes=["read"])
    assert _ask(client, app.id, ro, "GET").status_code == 200
    assert _ask(client, app.id, ro, "HEAD").status_code == 200
    for m in ("POST", "PUT", "DELETE", None):
        r = _ask(client, app.id, ro, m)
        assert r.status_code == 403 and "읽기 전용" in r.json()["detail"], f"{m}: {r.status_code}"
    assert _ask(client, app.id, _pat(owner.email), "POST").status_code == 200, "read · write 는 쓰기도"
    assert _ask(client, app.id, _pat(owner.email, scopes=None), "POST").status_code == 200, "칸 없는 옛 PAT 은 포털 기본(read · write)"


def test_포털_SSO_가_꺼져_있으면_포털_토큰은_종전대로_401(client: TestClient, owned, monkeypatch) -> None:
    owner, _, app = owned
    monkeypatch.setattr(get_settings(), "portal_jwks_url", "")
    r = _ask(client, app.id, _pat(owner.email))
    assert r.status_code == 401 and not r.content, "허브 HS256 검증으로 흘러 종전과 같다"


def test_폐기_목록_주소는_JWKS_에서_짓는다(monkeypatch) -> None:
    for jwks_url, want in [
        ("http://127.0.0.1:8723/.well-known/jwks.json", "http://127.0.0.1:8723/auth/pat/revoked.json"),
        ("https://hwax.example/portal/.well-known/jwks.json", "https://hwax.example/portal/auth/pat/revoked.json"),
        ("https://hwax.example/keys", ""),
    ]:
        monkeypatch.setattr(get_settings(), "portal_jwks_url", jwks_url)
        assert portal_sso._revoked_url() == want
