"""FastAPI dependencies: DB session, current user, role checks, app-permission checks."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import decode_token
from app.db.models.app import App
from app.db.models.user import User, UserRole, UserStatus
from app.db.session import get_db


DbSession = Annotated[Session, Depends(get_db)]


def _extract_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("Missing Bearer token")
    return authorization.split(" ", 1)[1].strip()


def get_current_user(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    token = _extract_bearer(authorization)
    payload = decode_token(token, expected_type="access")
    user = db.get(User, payload["sub"])
    if user is None:
        raise UnauthorizedError("User not found")
    if user.status == UserStatus.DISABLED:
        raise UnauthorizedError("User disabled")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*roles: UserRole):
    """Dependency factory that ensures the current user has one of the given roles."""

    def _checker(user: CurrentUser) -> User:
        if user.role not in roles and user.role != UserRole.ADMIN:
            raise ForbiddenError(
                f"This action requires role in {[r.value for r in roles]}"
            )
        return user

    return _checker


def require_admin(user: CurrentUser) -> User:
    if user.role != UserRole.ADMIN:
        raise ForbiddenError("Admin role required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def get_app_or_404(app_id: str, db: DbSession) -> App:
    obj = db.get(App, app_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="App not found")
    return obj


def client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None
