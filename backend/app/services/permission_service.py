"""App-level permission/visibility resolution."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError
from app.db.models.app import App, AppVisibility
from app.db.models.permission import Permission, PermissionLevel, PrincipalType
from app.db.models.user import User, UserRole, UserStatus


def _has_explicit_permission(
    db: Session,
    *,
    app_id: str,
    user: User,
    required: PermissionLevel,
) -> bool:
    """Check the `permissions` table for grants matching the user/role/group."""
    # Permission ladder: manage ⊇ execute ⊇ view
    accept_levels: set[PermissionLevel] = {required}
    if required == PermissionLevel.VIEW:
        accept_levels |= {PermissionLevel.EXECUTE, PermissionLevel.MANAGE}
    elif required == PermissionLevel.EXECUTE:
        accept_levels |= {PermissionLevel.MANAGE}

    stmt = select(Permission).where(Permission.app_id == app_id)
    for row in db.execute(stmt).scalars():
        if row.permission not in accept_levels:
            continue
        if row.principal_type == PrincipalType.USER and row.principal_id == str(user.id):
            return True
        if row.principal_type == PrincipalType.ROLE and row.principal_id == user.role.value:
            return True
        if row.principal_type == PrincipalType.GROUP and row.principal_id == user.organization:
            return True
    return False


def can_view_app(db: Session, app: App, user: User) -> bool:
    if user.role == UserRole.ADMIN:
        return True
    if app.owner_user_id == user.id:
        return True
    if app.visibility == AppVisibility.COMPANY and user.status == UserStatus.ACTIVE:
        return True
    if app.visibility == AppVisibility.TEAM:
        owner = db.get(User, app.owner_user_id)
        if owner and owner.organization == user.organization:
            return True
    if app.visibility == AppVisibility.DEPARTMENT:
        # Department grouping not yet modeled; fall back to explicit perms.
        pass
    # private (or any visibility falling through) → check explicit perms
    return _has_explicit_permission(
        db, app_id=app.id, user=user, required=PermissionLevel.VIEW
    )


def can_execute_app(db: Session, app: App, user: User) -> bool:
    if user.role == UserRole.ADMIN:
        return True
    if app.owner_user_id == user.id:
        return True
    if not can_view_app(db, app, user):
        return False
    # Default: anyone who can view can execute, unless restricted by explicit perms.
    # If any execute-level grants exist, require one of them.
    has_execute_grants = (
        db.execute(
            select(Permission.id).where(
                Permission.app_id == app.id,
                Permission.permission.in_([PermissionLevel.EXECUTE, PermissionLevel.MANAGE]),
            )
        )
        .first()
        is not None
    )
    if not has_execute_grants:
        return True
    return _has_explicit_permission(
        db, app_id=app.id, user=user, required=PermissionLevel.EXECUTE
    )


def can_manage_app(db: Session, app: App, user: User) -> bool:
    if user.role == UserRole.ADMIN:
        return True
    if app.owner_user_id == user.id:
        return True
    return _has_explicit_permission(
        db, app_id=app.id, user=user, required=PermissionLevel.MANAGE
    )


def assert_view(db: Session, app: App, user: User) -> None:
    if not can_view_app(db, app, user):
        raise ForbiddenError("Not allowed to view this app")


def assert_execute(db: Session, app: App, user: User) -> None:
    if not can_execute_app(db, app, user):
        raise ForbiddenError("Not allowed to execute this app")


def assert_manage(db: Session, app: App, user: User) -> None:
    if not can_manage_app(db, app, user):
        raise ForbiddenError("Not allowed to manage this app")


def visible_app_ids(db: Session, user: User) -> list[str] | None:
    """Return list of app_ids the user can view, or None meaning 'all'.

    Used by listing endpoints. Admin returns None (no filter).
    """
    if user.role == UserRole.ADMIN:
        return None
    apps = db.execute(select(App)).scalars().all()
    return [a.id for a in apps if can_view_app(db, a, user)]
