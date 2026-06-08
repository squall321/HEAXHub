"""Lightweight Redis-backed rate limiter for FastAPI.

We deliberately do not pull in `slowapi` — the only dependency we need is
`redis`, which is already a project requirement. The limiter uses a per-bucket
counter with TTL (fixed window), which is enough for the abuse cases we care
about (login brute force, signup floods).

Per-route configuration lives in `ROUTE_RULES`. Each rule is keyed by HTTP
method + path-prefix and has the form ``(scope, limit, window_seconds)``.

`scope` is one of:
    "ip"   — bucket by client IP (X-Forwarded-For first hop, falls back to
             request.client.host)
    "user" — bucket by authenticated user id (Bearer token sub claim); if no
             token is present the limiter falls back to the IP bucket.

Limit checks fail-open: if Redis is down or any sub-step throws, requests pass
through (operators see the warning in the log). This is intentional — we never
want the limiter to take the whole site down.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.core.security import decode_token

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Rule:
    method: str           # "*" matches every method
    prefix: str           # request.url.path startswith match
    scope: str            # "ip" | "user"
    limit: int            # max requests
    window: int           # seconds


# Rules are evaluated in order; the first match wins. The final catch-all rule
# is kept last so the auth-specific rules always take precedence.
ROUTE_RULES: list[_Rule] = [
    _Rule("POST", "/api/v1/auth/login",                   "ip", 5,   60),
    _Rule("POST", "/api/v1/auth/register",                "ip", 3,   3600),
    _Rule("POST", "/api/v1/auth/password/reset-request",  "ip", 3,   3600),
    # Launcher unauthenticated front door — tight per-IP (no bearer yet here).
    _Rule("POST", "/api/v1/launcher-agents/enroll",       "ip", 10,  60),
    _Rule("POST", "/api/v1/launcher-agents/refresh",      "ip", 20,  60),
    # Authenticated launcher traffic — bucket PER AGENT (see _user_key), not by
    # the shared corporate-NAT IP, so a fleet of launchers behind one egress IP
    # doesn't collectively starve a single IP budget on routine polls/heartbeats.
    _Rule("*",    "/api/v1/launcher-agents/",             "user", 120, 60),
    _Rule("GET",  "/api/v1/installers/",                  "user", 60,  60),
    # Match every /apps/{id}/run regardless of app_id by prefix-stripping
    # after the match (see _match_path below).
    _Rule("POST", "/api/v1/apps/",                        "user", 30, 60),
    # Catch-all — kept last.
    _Rule("*",    "/api/v1/",                             "ip", 200, 60),
]


def _match_path(rule: _Rule, method: str, path: str) -> bool:
    if rule.method != "*" and rule.method != method.upper():
        return False
    if rule.prefix == "/api/v1/apps/":
        # Only match the /run sub-endpoint, not every /apps/* call.
        return path.startswith("/api/v1/apps/") and path.endswith("/run")
    return path.startswith(rule.prefix)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _user_key(request: Request) -> str | None:
    """Decode the Bearer token without raising; return a per-identity bucket key.

    Handles BOTH plain user tokens and launcher tokens. A launcher access token
    carries ``aud='hwax-agent'``; ``decode_token`` with no expected audience
    rejects it (audience isolation), so we retry with the launcher audience and
    bucket by ``agent:<sub>``. Without this every launcher token fell through to
    the IP bucket — collapsing a whole NAT'd fleet onto one shared budget.
    """
    auth = request.headers.get("authorization")
    if not auth or not auth.lower().startswith("bearer "):
        return None
    token = auth.split(" ", 1)[1].strip()
    try:
        payload = decode_token(token, expected_type="access")
        sub = payload.get("sub")
        return str(sub) if sub else None
    except Exception:  # noqa: BLE001
        pass
    try:
        payload = decode_token(
            token, expected_type="access", expected_audience="hwax-agent"
        )
        sub = payload.get("sub")
        return f"agent:{sub}" if sub else None
    except Exception:  # noqa: BLE001
        return None


def _bucket_key(rule: _Rule, request: Request) -> str:
    window_id = int(time.time() // rule.window)
    if rule.scope == "user":
        ident = _user_key(request) or f"ip:{_client_ip(request)}"
    else:
        ident = f"ip:{_client_ip(request)}"
    return f"rl:{rule.method}:{rule.prefix}:{ident}:{window_id}"


def _increment(key: str, window: int) -> int:
    """Atomically increment the bucket and (re)apply the TTL."""
    import redis as _redis

    client = _redis.Redis.from_url(get_settings().redis_url)
    pipe = client.pipeline()
    pipe.incr(key, 1)
    pipe.expire(key, window)
    count, _ = pipe.execute()
    return int(count)


def _rate_limited_response(rule: _Rule) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "error": {
                "code": "rate_limited",
                "message": f"Too many requests — limit {rule.limit}/{rule.window}s",
                "details": {"limit": rule.limit, "window_seconds": rule.window},
            }
        },
        headers={"Retry-After": str(rule.window)},
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply `ROUTE_RULES` in order. Fail-open on any internal error."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Any]],
    ) -> Any:
        try:
            method = request.method.upper()
            # Never throttle CORS preflights — they need to reach the CORS
            # middleware regardless of any per-route budget.
            if method == "OPTIONS":
                return await call_next(request)
            path = request.url.path
            for rule in ROUTE_RULES:
                if not _match_path(rule, method, path):
                    continue
                key = _bucket_key(rule, request)
                count = _increment(key, rule.window)
                if count > rule.limit:
                    return _rate_limited_response(rule)
                break
        except Exception:  # noqa: BLE001 — fail-open
            logger.exception("rate-limit middleware failed; passing request through")
        return await call_next(request)


__all__ = ["RateLimitMiddleware", "ROUTE_RULES"]
