"""App catalog endpoints."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select

from app.core.errors import NotFoundError, ValidationError
from app.db.models.app import App, AppStatus, AppType, AppVisibility
from app.db.models.app_version import AppVersion
from app.db.models.job import Job
from app.db.models.user_favorite import UserFavorite
from app.deps import CurrentUser, DbSession, get_app_or_404
from app.schemas.app import AppDetailOut, AppOut, AppVersionOut
from app.schemas.common import Paginated
from app.schemas.job import JobOut
from app.services import job_orchestrator, permission_service
from app.services.workspace_manager import safe_join

router = APIRouter(prefix="/apps", tags=["apps"])


@router.get("", response_model=Paginated[AppOut])
def list_apps(
    db: DbSession,
    user: CurrentUser,
    q: str | None = Query(default=None),
    app_type: AppType | None = Query(default=None),
    status_: AppStatus | None = Query(default=None, alias="status"),
    visibility: AppVisibility | None = Query(default=None),
    tag: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> Paginated[AppOut]:
    stmt = select(App).order_by(App.updated_at.desc())
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(App.name).like(like), func.lower(App.id).like(like)))
    if app_type:
        stmt = stmt.where(App.app_type == app_type)
    if status_:
        stmt = stmt.where(App.status == status_)
    if visibility:
        stmt = stmt.where(App.visibility == visibility)

    visible_ids = permission_service.visible_app_ids(db, user)
    if visible_ids is not None:
        if not visible_ids:
            return Paginated(items=[], total=0, page=page, page_size=page_size)
        stmt = stmt.where(App.id.in_(visible_ids))

    all_rows = list(db.execute(stmt).scalars())
    if tag:
        all_rows = [a for a in all_rows if a.tags and tag in a.tags]

    total = len(all_rows)
    offset = (page - 1) * page_size
    page_rows = all_rows[offset : offset + page_size]
    return Paginated(
        items=[AppOut.model_validate(a) for a in page_rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/recommended", response_model=list[AppOut])
def recommended_apps(db: DbSession, user: CurrentUser) -> list[AppOut]:
    """Return up to 8 stable apps the user can see, ordered by recent activity."""
    visible = permission_service.visible_app_ids(db, user)
    stmt = (
        select(App)
        .where(App.status == AppStatus.STABLE)
        .order_by(App.updated_at.desc())
        .limit(8)
    )
    if visible is not None:
        if not visible:
            return []
        stmt = stmt.where(App.id.in_(visible))
    rows = list(db.execute(stmt).scalars())
    return [AppOut.model_validate(a) for a in rows]


@router.get("/favorites", response_model=list[AppOut])
def list_favorites(db: DbSession, user: CurrentUser) -> list[AppOut]:
    rows = (
        db.execute(
            select(App)
            .join(UserFavorite, UserFavorite.app_id == App.id)
            .where(UserFavorite.user_id == user.id)
            .order_by(UserFavorite.created_at.desc())
        )
        .scalars()
        .all()
    )
    visible = permission_service.visible_app_ids(db, user)
    if visible is not None:
        rows = [a for a in rows if a.id in visible]
    return [AppOut.model_validate(a) for a in rows]


@router.post("/{app_id}/favorite")
def toggle_favorite(
    app: Annotated[App, Depends(get_app_or_404)],
    db: DbSession,
    user: CurrentUser,
) -> dict[str, bool]:
    permission_service.assert_view(db, app, user)
    existing = db.execute(
        select(UserFavorite).where(
            UserFavorite.user_id == user.id, UserFavorite.app_id == app.id
        )
    ).scalar_one_or_none()
    if existing:
        db.delete(existing)
        db.commit()
        return {"favorited": False}
    db.add(UserFavorite(user_id=user.id, app_id=app.id))
    db.commit()
    return {"favorited": True}


@router.get("/{app_id}/history", response_model=Paginated[JobOut])
def app_run_history(
    app: Annotated[App, Depends(get_app_or_404)],
    db: DbSession,
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> Paginated[JobOut]:
    permission_service.assert_view(db, app, user)
    base = select(Job).where(Job.app_id == app.id).order_by(Job.created_at.desc())
    rows = list(db.execute(base).scalars())
    total = len(rows)
    offset = (page - 1) * page_size
    return Paginated(
        items=[JobOut.model_validate(j) for j in rows[offset : offset + page_size]],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{app_id}", response_model=AppDetailOut)
def get_app(
    app: Annotated[App, Depends(get_app_or_404)],
    db: DbSession,
    user: CurrentUser,
) -> AppDetailOut:
    permission_service.assert_view(db, app, user)
    versions = (
        db.execute(
            select(AppVersion)
            .where(AppVersion.app_id == app.id)
            .order_by(AppVersion.created_at.desc())
        )
        .scalars()
        .all()
    )
    manifest: dict[str, Any] | None = None
    overlay = Path(app.workspace_path) / "overlay" / ".portal" / "manifest.yaml"
    if overlay.exists():
        try:
            import yaml

            manifest = yaml.safe_load(overlay.read_text(encoding="utf-8"))
        except Exception:
            manifest = None
    base = AppDetailOut.model_validate(app)
    base = base.model_copy(
        update={
            "versions": [AppVersionOut.model_validate(v) for v in versions],
            "manifest": manifest,
        }
    )
    return base


@router.get("/{app_id}/manifest")
def get_app_manifest(
    app: Annotated[App, Depends(get_app_or_404)],
    db: DbSession,
    user: CurrentUser,
) -> dict[str, Any]:
    permission_service.assert_view(db, app, user)
    overlay = Path(app.workspace_path) / "overlay" / ".portal" / "manifest.yaml"
    if not overlay.exists():
        raise NotFoundError("Manifest not found")
    import yaml

    return yaml.safe_load(overlay.read_text(encoding="utf-8")) or {}


@router.get("/{app_id}/versions", response_model=list[AppVersionOut])
def get_versions(
    app: Annotated[App, Depends(get_app_or_404)],
    db: DbSession,
    user: CurrentUser,
) -> list[AppVersionOut]:
    permission_service.assert_view(db, app, user)
    rows = (
        db.execute(
            select(AppVersion)
            .where(AppVersion.app_id == app.id)
            .order_by(AppVersion.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [AppVersionOut.model_validate(v) for v in rows]


@router.post("/{app_id}/run", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def run_app(
    app: Annotated[App, Depends(get_app_or_404)],
    db: DbSession,
    user: CurrentUser,
    params_json: Annotated[str, Form()] = "{}",
    version_id: Annotated[str | None, Form()] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
) -> JobOut:
    permission_service.assert_execute(db, app, user)

    try:
        params = json.loads(params_json or "{}")
    except json.JSONDecodeError as exc:
        raise ValidationError(f"params_json is not valid JSON: {exc}") from exc
    params = job_orchestrator.ensure_run_inputs(params)

    file_map: dict[str, tuple[str, bytes]] = {}
    if files:
        for upload in files:
            data = await upload.read()
            file_map[upload.filename or "file"] = (upload.filename or "file", data)

    job = job_orchestrator.submit_job(
        db,
        user=user,
        app=app,
        params=params,
        files=file_map,
        version_id=version_id,
    )
    return JobOut.model_validate(job)


@router.get("/{app_id}/files/{path:path}")
def download_app_file(
    path: str,
    app: Annotated[App, Depends(get_app_or_404)],
    db: DbSession,
    user: CurrentUser,
) -> FileResponse:
    """Download a file from the app's workspace (read-only). Used for fetching
    bundled docs, sample inputs, etc."""
    permission_service.assert_view(db, app, user)
    base = Path(app.workspace_path)
    full = safe_join(base, path)
    if not full.exists() or not full.is_file():
        raise NotFoundError("File not found")
    return FileResponse(str(full), filename=full.name)
