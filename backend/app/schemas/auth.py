"""Auth-related Pydantic schemas."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.db.models.user import AuthSource, UserRole, UserStatus


class UserRegister(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    organization: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str
    password_confirm: str


class VerifyEmailRequest(BaseModel):
    token: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str | None = None
    all_devices: bool = False


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    organization: str | None = Field(default=None, min_length=1, max_length=200)


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    display_name: str
    organization: str
    auth_source: AuthSource
    status: UserStatus
    role: UserRole
    email_verified: bool
    last_login_at: datetime | None = None
    created_at: datetime


class AuthTokens(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserPublic


# --- personal access tokens (PAT) --------------------------------------------


class PatCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120, description="토큰 용도 라벨 (예: 'claude-mcp')")
    expires_days: int | None = Field(
        default=None, ge=1, le=3650,
        description="만료일수. 생략하면 무기한 (폐기로만 무효화)")


class PatPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    token_prefix: str
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class PatCreated(PatPublic):
    # 평문 토큰 — 발급 응답에서 단 한 번만 노출된다.
    token: str
