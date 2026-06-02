"""Users alias endpoints. Mirrors /auth/me so frontends can use either path."""
from __future__ import annotations

from fastapi import APIRouter

from app.deps import CurrentUser, DbSession
from app.schemas.auth import UserPublic, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserPublic)
def get_me(user: CurrentUser) -> UserPublic:
    return UserPublic.model_validate(user)


@router.patch("/me", response_model=UserPublic)
def update_me(payload: UserUpdate, user: CurrentUser, db: DbSession) -> UserPublic:
    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.organization is not None:
        user.organization = payload.organization
    db.commit()
    db.refresh(user)
    return UserPublic.model_validate(user)
