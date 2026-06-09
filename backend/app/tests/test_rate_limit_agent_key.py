"""Unit tests for rate_limit._user_key — launcher tokens must bucket per-agent
(a stable non-None key), not fall through to the shared IP bucket. Pure token
decode; no DB/Redis.
"""
from __future__ import annotations

from app.core import rate_limit
from app.core.security import create_access_token


class _Req:
    """Minimal stand-in: _user_key only reads request.headers.get('authorization')."""

    def __init__(self, auth: str | None = None) -> None:
        self.headers: dict[str, str] = {}
        if auth is not None:
            self.headers["authorization"] = auth


def test_user_token_buckets_by_sub() -> None:
    tok = create_access_token("user-123")  # no audience
    assert rate_limit._user_key(_Req(f"Bearer {tok}")) == "user-123"


def test_launcher_token_buckets_per_agent_not_none() -> None:
    # A launcher access token carries aud='hwax-agent'. Regardless of how the
    # shared decode_token treats an aud claim, _user_key must resolve a stable
    # per-agent key (never None → which would dump the whole fleet on the IP
    # bucket). Accept either "agent-9" (decode_token tolerated it) or
    # "agent:agent-9" (peek_agent_id branch handled it).
    tok = create_access_token("agent-9", extra={"aud": "hwax-agent"})
    key = rate_limit._user_key(_Req(f"Bearer {tok}"))
    assert key is not None and key.endswith("agent-9")


def test_no_bearer_returns_none() -> None:
    assert rate_limit._user_key(_Req()) is None
    assert rate_limit._user_key(_Req("Basic abc")) is None


def test_launcher_rules_precede_catch_all() -> None:
    prefixes = [r.prefix for r in rate_limit.ROUTE_RULES]
    assert prefixes.index("/api/v1/launcher-agents/") < prefixes.index("/api/v1/")
    enroll = next(r for r in rate_limit.ROUTE_RULES if r.prefix.endswith("/enroll"))
    assert enroll.scope == "ip"  # unauthenticated front door
