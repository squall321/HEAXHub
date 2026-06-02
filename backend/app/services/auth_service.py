"""Local-auth (email + password) user lifecycle service."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import ConflictError, NotFoundError, UnauthorizedError, ValidationError
from app.core.logger import get_logger
from app.core.security import (
    create_access_token,
    create_email_verify_token,
    create_password_reset_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.models.refresh_token import RefreshToken
from app.db.models.user import AuthSource, User, UserRole, UserStatus
from app.schemas.auth import (
    AuthTokens,
    LoginRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    UserPublic,
    UserRegister,
)
from app.services import mail_service

logger = get_logger(__name__)


# --- validation helpers ------------------------------------------------------


def _check_email_domain(email: str) -> None:
    settings = get_settings()
    domain = email.split("@", 1)[-1].lower()
    if domain not in settings.allowed_email_domain_list:
        raise ValidationError(
            f"Email domain '{domain}' is not allowed",
            details={"allowed": settings.allowed_email_domain_list},
        )


_PASSWORD_CLASSES = (
    re.compile(r"[a-z]"),
    re.compile(r"[A-Z]"),
    re.compile(r"[0-9]"),
    re.compile(r"[^a-zA-Z0-9]"),
)


def _check_password_strength(password: str) -> None:
    settings = get_settings()
    if len(password) < settings.password_min_length:
        raise ValidationError(
            f"Password must be at least {settings.password_min_length} characters"
        )
    classes = sum(1 for pat in _PASSWORD_CLASSES if pat.search(password))
    if classes < 3:
        raise ValidationError(
            "Password must include at least 3 of: lowercase, uppercase, digit, symbol"
        )


# --- service operations ------------------------------------------------------


def register_user(db: Session, payload: UserRegister) -> User:
    if payload.password != payload.password_confirm:
        raise ValidationError("Passwords do not match")
    _check_email_domain(payload.email)
    _check_password_strength(payload.password)

    email = payload.email.lower()
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("Email already registered")

    user = User(
        email=email,
        display_name=payload.display_name,
        organization=payload.organization,
        password_hash=hash_password(payload.password),
        auth_source=AuthSource.LOCAL,
        status=UserStatus.PENDING_VERIFICATION,
        role=UserRole.USER,
        email_verified=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_email_verify_token(str(user.id))
    try:
        mail_service.send_verification_email(
            to=user.email, display_name=user.display_name, token=token
        )
    except Exception:
        logger.exception("verification mail failed; user may need re-send")

    return user


def verify_email(db: Session, token: str) -> User:
    payload = decode_token(token, expected_type="email_verify")
    user_id = payload["sub"]
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found")
    if user.email_verified and user.status == UserStatus.ACTIVE:
        return user
    user.email_verified = True
    user.status = UserStatus.ACTIVE
    db.commit()
    db.refresh(user)
    return user


def _tokens_for(db: Session, user: User) -> AuthTokens:
    settings = get_settings()
    access = create_access_token(str(user.id), extra={"role": user.role.value})
    refresh, jti, expires_at = create_refresh_token(str(user.id))
    db.add(
        RefreshToken(
            user_id=user.id,
            jti=jti,
            expires_at=expires_at,
        )
    )
    db.commit()
    return AuthTokens(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_ttl_seconds,
        user=UserPublic.model_validate(user),
    )


def login(db: Session, payload: LoginRequest) -> AuthTokens:
    email = payload.email.lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None or user.auth_source != AuthSource.LOCAL:
        raise UnauthorizedError("Invalid credentials")
    if not verify_password(payload.password, user.password_hash):
        raise UnauthorizedError("Invalid credentials")
    if user.status == UserStatus.DISABLED:
        raise UnauthorizedError("Account disabled")
    if user.status == UserStatus.PENDING_VERIFICATION or not user.email_verified:
        raise UnauthorizedError("Email not verified")

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return _tokens_for(db, user)


def refresh_tokens(db: Session, refresh_token: str) -> AuthTokens:
    payload = decode_token(refresh_token, expected_type="refresh")
    user = db.get(User, payload["sub"])
    if user is None or user.status != UserStatus.ACTIVE:
        raise UnauthorizedError("User not active")

    jti = payload.get("jti")
    if not jti:
        raise UnauthorizedError("Refresh token missing jti")

    row = db.execute(
        select(RefreshToken).where(RefreshToken.jti == jti)
    ).scalar_one_or_none()
    if row is None or not row.is_active:
        raise UnauthorizedError("Refresh token revoked or unknown")

    # Issue new tokens
    new_tokens = _tokens_for(db, user)

    # Mark old token rotated: revoke and link replacement
    row.revoked_at = datetime.now(timezone.utc)
    # Find the jti we just issued so we can chain replaced_by_jti.
    new_payload = decode_token(new_tokens.refresh_token, expected_type="refresh")
    row.replaced_by_jti = new_payload.get("jti")
    db.commit()

    return new_tokens


def revoke_refresh_token(db: Session, refresh_token: str) -> None:
    """Mark a refresh token as revoked. Used by /auth/logout."""
    try:
        payload = decode_token(refresh_token, expected_type="refresh")
    except UnauthorizedError:
        return  # silently no-op on invalid token
    jti = payload.get("jti")
    if not jti:
        return
    row = db.execute(
        select(RefreshToken).where(RefreshToken.jti == jti)
    ).scalar_one_or_none()
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        db.commit()


def revoke_all_for_user(db: Session, user_id: str) -> int:
    """Revoke every active refresh token for a user. Returns count revoked."""
    rows = (
        db.execute(
            select(RefreshToken).where(
                RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(timezone.utc)
    for r in rows:
        r.revoked_at = now
    db.commit()
    return len(rows)


def request_password_reset(db: Session, payload: PasswordResetRequest) -> None:
    email = payload.email.lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    # Do not leak which emails exist: always succeed.
    if user is None or user.auth_source != AuthSource.LOCAL:
        logger.info("password reset requested for unknown/non-local email=%s", email)
        return
    token = create_password_reset_token(str(user.id))
    try:
        mail_service.send_password_reset_email(
            to=user.email, display_name=user.display_name, token=token
        )
    except Exception:
        logger.exception("password reset mail failed")


def confirm_password_reset(db: Session, payload: PasswordResetConfirm) -> None:
    decoded = decode_token(payload.token, expected_type="password_reset")
    _check_password_strength(payload.new_password)
    user = db.get(User, decoded["sub"])
    if user is None:
        raise NotFoundError("User not found")
    user.password_hash = hash_password(payload.new_password)
    db.commit()
