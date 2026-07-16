"""Authentication endpoints (local mode)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.config import get_settings
from app.deps import CurrentUser, DbSession, client_ip
from app.schemas.auth import (
    AuthTokens,
    LoginRequest,
    LogoutRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    PatCreated,
    PatCreateRequest,
    PatPublic,
    RefreshRequest,
    UserPublic,
    UserRegister,
    VerifyEmailRequest,
)
from app.services import audit_service, auth_service, pat_service

router = APIRouter(prefix="/auth", tags=["auth"])

# forward_auth(/authz)·브라우저 세션이 함께 쓰는 httpOnly 쿠키 이름.
SESSION_COOKIE_NAME = "heax_access_token"


def _set_session_cookie(response: Response, access_token: str) -> None:
    """로그인/리프레시 응답에 httpOnly 세션 쿠키를 심는다. Bearer 본문은 그대로 둔다."""
    settings = get_settings()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=access_token,
        max_age=settings.access_token_ttl_seconds,
        path="/",
        httponly=True,
        # 개발 환경(http)에서는 Secure 끄고, 그 외(staging/production)에서는 켠다.
        secure=settings.app_env != "development",
        samesite="lax",
    )


def _clear_session_cookie(response: Response) -> None:
    """로그아웃 시 세션 쿠키를 즉시 만료시킨다."""
    settings = get_settings()
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=settings.app_env != "development",
        samesite="lax",
    )


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
def login(
    payload: LoginRequest, db: DbSession, request: Request, response: Response
) -> AuthTokens:
    tokens = auth_service.login(db, payload)
    audit_service.log(
        db,
        actor_user_id=tokens.user.id,
        action="user.login",
        target_type="user",
        target_id=str(tokens.user.id),
        ip_address=client_ip(request),
    )
    _set_session_cookie(response, tokens.access_token)
    return tokens


@router.post("/refresh", response_model=AuthTokens)
def refresh(payload: RefreshRequest, db: DbSession, response: Response) -> AuthTokens:
    tokens = auth_service.refresh_tokens(db, payload.refresh_token)
    _set_session_cookie(response, tokens.access_token)
    return tokens


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    payload: LogoutRequest | None,
    user: CurrentUser,
    db: DbSession,
    response: Response,
) -> None:
    """Revoke the supplied refresh token (or all tokens for this user)."""
    if payload and payload.all_devices:
        auth_service.revoke_all_for_user(db, str(user.id))
    elif payload and payload.refresh_token:
        auth_service.revoke_refresh_token(db, payload.refresh_token)
    _clear_session_cookie(response)
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


# --- personal access tokens (PAT) --------------------------------------------
# 헤드리스 클라이언트(MCP·CI)용 장수명 자격증명. 브라우저 SSO 쿠키를 못 타는
# 클라이언트가 Authorization: Bearer <PAT> 로 /apps/* forward_auth 와 API 를 통과한다.


@router.post("/tokens", response_model=PatCreated, status_code=status.HTTP_201_CREATED)
def create_pat(
    payload: PatCreateRequest,
    user: CurrentUser,
    db: DbSession,
    request: Request,
) -> PatCreated:
    """PAT 발급. 평문 토큰은 이 응답에서 단 한 번만 반환된다."""
    row, plaintext = pat_service.issue(
        db, user=user, name=payload.name, expires_days=payload.expires_days
    )
    audit_service.log(
        db,
        actor_user_id=user.id,
        action="user.pat_issued",
        target_type="personal_access_token",
        target_id=str(row.id),
        meta={"name": row.name, "expires_at": row.expires_at.isoformat() if row.expires_at else None},
        ip_address=client_ip(request),
    )
    return PatCreated(token=plaintext, **PatPublic.model_validate(row).model_dump())


@router.get("/tokens", response_model=list[PatPublic])
def list_pats(user: CurrentUser, db: DbSession) -> list[PatPublic]:
    """내 PAT 목록 (평문은 반환하지 않는다 — token_prefix로 식별)."""
    return [PatPublic.model_validate(r) for r in pat_service.list_for_user(db, user.id)]


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_pat(
    token_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    request: Request,
) -> None:
    """PAT 폐기 (소유자 또는 admin). 즉시 무효화된다."""
    if not pat_service.revoke(db, actor=user, token_id=token_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    audit_service.log(
        db,
        actor_user_id=user.id,
        action="user.pat_revoked",
        target_type="personal_access_token",
        target_id=str(token_id),
        ip_address=client_ip(request),
    )
    return None
