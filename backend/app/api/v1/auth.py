"""Authentication endpoints (local mode)."""
from __future__ import annotations

from fastapi import APIRouter, Request, status

from app.deps import CurrentUser, DbSession, client_ip
from app.schemas.auth import (
    AuthTokens,
    LoginRequest,
    LogoutRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    UserPublic,
    UserRegister,
    VerifyEmailRequest,
)
from app.services import audit_service, auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
def register(payload: UserRegister, db: DbSession, request: Request) -> UserPublic:
    user = auth_service.register_user(db, payload)
    audit_service.log(
        db,
        actor_user_id=user.id,
        action="user.register",
        target_type="user",
        target_id=str(user.id),
        ip_address=client_ip(request),
    )
    return UserPublic.model_validate(user)


@router.post("/verify-email", response_model=UserPublic)
def verify_email(payload: VerifyEmailRequest, db: DbSession) -> UserPublic:
    user = auth_service.verify_email(db, payload.token)
    return UserPublic.model_validate(user)


@router.post("/login", response_model=AuthTokens)
def login(payload: LoginRequest, db: DbSession, request: Request) -> AuthTokens:
    tokens = auth_service.login(db, payload)
    audit_service.log(
        db,
        actor_user_id=tokens.user.id,
        action="user.login",
        target_type="user",
        target_id=str(tokens.user.id),
        ip_address=client_ip(request),
    )
    return tokens


@router.post("/refresh", response_model=AuthTokens)
def refresh(payload: RefreshRequest, db: DbSession) -> AuthTokens:
    return auth_service.refresh_tokens(db, payload.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    payload: LogoutRequest | None,
    user: CurrentUser,
    db: DbSession,
) -> None:
    """Revoke the supplied refresh token (or all tokens for this user)."""
    if payload and payload.all_devices:
        auth_service.revoke_all_for_user(db, str(user.id))
    elif payload and payload.refresh_token:
        auth_service.revoke_refresh_token(db, payload.refresh_token)
    return None


@router.get("/me", response_model=UserPublic)
def me(user: CurrentUser) -> UserPublic:
    return UserPublic.model_validate(user)


@router.post("/password/reset-request", status_code=status.HTTP_202_ACCEPTED)
def password_reset_request(payload: PasswordResetRequest, db: DbSession) -> dict[str, str]:
    auth_service.request_password_reset(db, payload)
    return {"detail": "If the email exists, a reset link has been sent."}


@router.post("/password/reset", status_code=status.HTTP_204_NO_CONTENT)
def password_reset(payload: PasswordResetConfirm, db: DbSession) -> None:
    auth_service.confirm_password_reset(db, payload)
    return None
