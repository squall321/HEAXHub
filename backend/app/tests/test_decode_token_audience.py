"""decode_token audience isolation (reverse guard). Pure JWT encode/decode — no
DB. A launcher access token (aud='hwax-agent') must NOT authenticate a plain user
route; a user token (no aud) must keep working.
"""
from __future__ import annotations

import pytest

from app.core.errors import UnauthorizedError
from app.core.security import create_access_token, decode_token


def test_user_token_accepted_without_audience() -> None:
    tok = create_access_token("user-1")  # no aud
    payload = decode_token(tok, expected_type="access")
    assert payload["sub"] == "user-1"


def test_launcher_token_rejected_on_user_route() -> None:
    # aud-scoped (launcher) token must be rejected when no audience is expected.
    tok = create_access_token("agent-1", extra={"aud": "hwax-agent"})
    with pytest.raises(UnauthorizedError):
        decode_token(tok, expected_type="access")


def test_launcher_token_accepted_with_matching_audience() -> None:
    tok = create_access_token("agent-1", extra={"aud": "hwax-agent"})
    payload = decode_token(tok, expected_type="access", expected_audience="hwax-agent")
    assert payload["sub"] == "agent-1"


def test_user_token_rejected_when_audience_required() -> None:
    tok = create_access_token("user-1")  # no aud
    with pytest.raises(UnauthorizedError):
        decode_token(tok, expected_audience="hwax-agent")
