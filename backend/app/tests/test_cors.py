"""CORS allow-list smoke tests.

Confirms that the live CORS middleware echoes back the configured frontend
origins (:4173 dev, :4180 Caddy public) but refuses unknown origins.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

ALLOWED = ("http://localhost:4173", "http://localhost:4180")
DENIED = "http://evil.example.com"


def _preflight(client: TestClient, origin: str) -> str | None:
    resp = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    return resp.headers.get("access-control-allow-origin")


def test_cors_allows_configured_origins() -> None:
    client = TestClient(app)
    for origin in ALLOWED:
        echoed = _preflight(client, origin)
        assert echoed == origin, f"origin {origin} should be allowed, got {echoed!r}"


def test_cors_rejects_unknown_origin() -> None:
    client = TestClient(app)
    echoed = _preflight(client, DENIED)
    assert echoed != DENIED, "unknown origin must not be echoed"
