# PAT 발급·조회·폐기·검증 서비스 — authz/deps의 Bearer PAT 해석 단일 소스.
"""Personal Access Token service.

Issues long-lived revocable tokens for headless clients (MCP clients, CI).
Plaintext is returned exactly once at issuance; storage keeps only the
SHA-256 hash. ``resolve_user`` is the single verification path used by both
the API dependency (``deps.get_current_user``) and the reverse-proxy gate
(``/api/v1/authz``).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.errors import UnauthorizedError
from app.core.security import generate_pat_token, hash_pat_token
from app.db.models.personal_access_token import PersonalAccessToken
from app.db.models.user import User, UserRole, UserStatus

# last_used_at 는 authz가 /apps/* 요청마다 불리므로 무제한 기록하면 쓰기 증폭이 크다.
_LAST_USED_UPDATE_INTERVAL = timedelta(seconds=60)


def issue(
    db: Session,
    *,
    user: User,
    name: str,
    expires_days: int | None = None,
) -> tuple[PersonalAccessToken, str]:
    """Create a PAT for ``user``. Returns (row, plaintext) — plaintext는 1회만 노출."""
    plaintext = generate_pat_token()
    row = PersonalAccessToken(
        user_id=user.id,
        name=name.strip(),
        token_prefix=plaintext[:12],
        token_hash=hash_pat_token(plaintext),
        expires_at=(
            datetime.now(timezone.utc) + timedelta(days=expires_days)
            if expires_days is not None
            else None
        ),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, plaintext


def list_for_user(db: Session, user_id: uuid.UUID) -> list[PersonalAccessToken]:
    return (
        db.query(PersonalAccessToken)
        .filter(PersonalAccessToken.user_id == user_id)
        .order_by(PersonalAccessToken.created_at.desc())
        .all()
    )


def revoke(db: Session, *, actor: User, token_id: uuid.UUID) -> bool:
    """Revoke a PAT. 소유자 본인 또는 admin만 가능. 못 찾으면 False."""
    row = db.get(PersonalAccessToken, token_id)
    if row is None:
        return False
    if row.user_id != actor.id and actor.role != UserRole.ADMIN:
        return False
    if row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        db.commit()
    return True


def resolve_user(db: Session, token: str) -> User:
    """Bearer PAT → User. 실패는 전부 UnauthorizedError (미인증과 동일 취급)."""
    row = (
        db.query(PersonalAccessToken)
        .filter(PersonalAccessToken.token_hash == hash_pat_token(token))
        .one_or_none()
    )
    if row is None or not row.is_active:
        raise UnauthorizedError("Invalid or expired token")
    user = db.get(User, row.user_id)
    if user is None or user.status == UserStatus.DISABLED:
        raise UnauthorizedError("User not found or disabled")

    now = datetime.now(timezone.utc)
    if row.last_used_at is None or now - row.last_used_at > _LAST_USED_UPDATE_INTERVAL:
        row.last_used_at = now
        db.commit()
    return user
