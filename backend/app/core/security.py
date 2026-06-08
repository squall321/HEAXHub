"""Security helpers: password hashing + JWT token encode/decode."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import get_settings
from app.core.errors import UnauthorizedError

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

TokenKind = Literal["access", "refresh", "email_verify", "password_reset"]


# --- password helpers --------------------------------------------------------


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return _pwd_context.verify(plain, hashed)
    except Exception:
        return False


# --- token helpers -----------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _encode(payload: dict[str, Any]) -> str:
    settings = get_settings()
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        # We do audience validation explicitly in ``decode_token`` (so behaviour
        # is identical across python-jose versions, which differ on how they
        # treat an ``aud`` claim when no audience is supplied). Disable jose's
        # own aud check here; signature / exp / type are still verified.
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"verify_aud": False},
        )
    except JWTError as exc:
        raise UnauthorizedError("Invalid or expired token") from exc


def create_access_token(subject: str, *, extra: dict[str, Any] | None = None) -> str:
    settings = get_settings()
    now = _now()
    payload: dict[str, Any] = {
        "sub": subject,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.access_token_ttl_seconds)).timestamp()),
    }
    if extra:
        payload.update(extra)
    return _encode(payload)


def create_refresh_token(
    subject: str, *, ttl_seconds: int | None = None
) -> tuple[str, str, datetime]:
    """Return (token, jti, expires_at) so callers can persist a revocation record.

    ``ttl_seconds`` overrides the default refresh TTL (the launcher uses a longer
    one than user sessions); ``None`` keeps the configured default.
    """
    settings = get_settings()
    now = _now()
    jti = secrets.token_urlsafe(16)
    ttl = ttl_seconds if ttl_seconds is not None else settings.refresh_token_ttl_seconds
    expires_at = now + timedelta(seconds=ttl)
    payload = {
        "sub": subject,
        "type": "refresh",
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return _encode(payload), jti, expires_at


def create_email_verify_token(subject: str) -> str:
    settings = get_settings()
    now = _now()
    payload = {
        "sub": subject,
        "type": "email_verify",
        "iat": int(now.timestamp()),
        "exp": int(
            (now + timedelta(hours=settings.email_verify_token_ttl_hours)).timestamp()
        ),
    }
    return _encode(payload)


def create_password_reset_token(subject: str) -> str:
    settings = get_settings()
    now = _now()
    payload = {
        "sub": subject,
        "type": "password_reset",
        "iat": int(now.timestamp()),
        "exp": int(
            (now + timedelta(hours=settings.password_reset_token_ttl_hours)).timestamp()
        ),
    }
    return _encode(payload)


def decode_token(
    token: str,
    *,
    expected_type: TokenKind | None = None,
    expected_audience: str | None = None,
) -> dict[str, Any]:
    payload = _decode(token)
    if expected_type and payload.get("type") != expected_type:
        raise UnauthorizedError(f"Token type mismatch (expected {expected_type})")
    # Audience isolation (jose's own aud check is disabled in _decode):
    #   - expected_audience set  -> the token's ``aud`` must equal it.
    #   - expected_audience None -> reject any audience-scoped token, so a
    #     launcher token (aud=hwax-agent) cannot authenticate a plain user route.
    aud = payload.get("aud")
    if expected_audience is not None:
        if aud != expected_audience:
            raise UnauthorizedError(
                f"Token audience mismatch (expected {expected_audience})"
            )
    elif aud is not None:
        raise UnauthorizedError("Audience-scoped token not accepted here")
    return payload


# --- GitHub webhook signature ------------------------------------------------


def verify_github_signature(secret: str, body: bytes, signature: str | None) -> bool:
    """Validate a GitHub ``X-Hub-Signature-256`` header against the request body.

    Returns ``True`` when no secret is configured (dev mode). Returns ``False``
    when the header is missing or malformed.
    """
    if not secret:
        return True
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
