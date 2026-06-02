"""Seed a streamlit-hello sample app + version + workspace.

Idempotent — safe to re-run. Creates:
  * an App row using the fixture template at ``templates/streamlit-hello``
  * a published AppVersion whose ``manifest_snapshot`` mirrors the fixture
    manifest (``launch.mode: service``) so that
    :func:`app.services.service_manager.start_service` can spawn it.
  * the workspace tree under ``settings.workspace_root/{app_id}`` with the
    ``.portal/run.sh`` and ``manifest.yaml`` staged into the overlay so the
    launcher's default command ``./.portal/run.sh`` works.

Run with:  python -m scripts.seed_streamlit_sample
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

# Allow running as a script from the backend/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.logger import get_logger  # noqa: E402
from app.db.models.app import (  # noqa: E402
    App,
    AppStatus,
    AppType,
    AppVisibility,
    ExecutionTarget,
)
from app.db.models.app_version import AppVersion, BuildStatus  # noqa: E402
from app.db.models.user import User, UserRole  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services import workspace_manager  # noqa: E402

logger = get_logger("seed_streamlit_sample")

# App id must satisfy workspace_manager._APP_ID_RE: ^[a-z][a-z0-9_]{2,63}$
APP_ID = "streamlit_hello_sample"
APP_VERSION = "0.1.0"


def _template_dir() -> Path:
    # backend/scripts/seed_streamlit_sample.py -> repo root
    return Path(__file__).resolve().parents[2] / "templates" / "streamlit-hello"


def _load_manifest(template: Path) -> dict[str, Any]:
    manifest_path = template / ".portal" / "manifest.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest.yaml missing: {manifest_path}")
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("manifest.yaml did not parse to a mapping")
    return data


def _stage_workspace(template: Path) -> Path:
    """Create the workspace skeleton and copy the template into upstream + overlay."""
    workspace = workspace_manager.create_app_workspace(APP_ID)
    upstream_portal = workspace / "upstream" / ".portal"
    overlay_portal = workspace / "overlay" / ".portal"
    upstream_portal.mkdir(parents=True, exist_ok=True)
    overlay_portal.mkdir(parents=True, exist_ok=True)

    for name in ("run.sh", "manifest.yaml"):
        src = template / ".portal" / name
        if not src.exists():
            continue
        shutil.copy2(src, upstream_portal / name)
        shutil.copy2(src, overlay_portal / name)
        if name == "run.sh":
            (upstream_portal / name).chmod(0o755)
            (overlay_portal / name).chmod(0o755)
    return workspace


def _pick_owner(db) -> User:
    settings = get_settings()
    owner = db.execute(
        select(User).where(User.email == settings.seed_admin_email.lower())
    ).scalar_one_or_none()
    if owner is not None:
        return owner
    # Fall back to any admin row so the script remains useful in pristine envs.
    owner = db.execute(
        select(User).where(User.role == UserRole.ADMIN).order_by(User.created_at)
    ).scalars().first()
    if owner is None:
        raise RuntimeError(
            "No admin user available; run `python -m scripts.create_admin` first"
        )
    return owner


def main() -> int:
    template = _template_dir()
    if not (template / ".portal" / "run.sh").exists():
        logger.error("streamlit-hello template missing at %s", template)
        return 2

    manifest = _load_manifest(template)
    workspace = _stage_workspace(template)

    with SessionLocal() as db:
        owner = _pick_owner(db)

        app = db.get(App, APP_ID)
        if app is None:
            app = App(
                id=APP_ID,
                name=manifest.get("name") or "Streamlit Hello (sample)",
                description=manifest.get("description"),
                owner_user_id=owner.id,
                app_type=AppType.WEB_APP,
                execution_target=ExecutionTarget.LINUX_RUNNER,
                status=AppStatus.STABLE,
                visibility=AppVisibility.TEAM,
                upstream_repo_url=f"file://{template}",
                tags=manifest.get("tags") or [],
                workspace_path=str(workspace),
            )
            db.add(app)
            db.flush()
            logger.info("created App row id=%s", APP_ID)
        else:
            logger.info("App %s already exists — reusing", APP_ID)

        version = db.execute(
            select(AppVersion)
            .where(AppVersion.app_id == APP_ID, AppVersion.version == APP_VERSION)
        ).scalar_one_or_none()
        if version is None:
            version = AppVersion(
                app_id=APP_ID,
                version=APP_VERSION,
                manifest_snapshot=manifest,
                build_status=BuildStatus.SUCCESS,
            )
            db.add(version)
            db.flush()
            logger.info("created AppVersion %s@%s", APP_ID, APP_VERSION)
        else:
            # Keep the manifest snapshot in sync with the fixture on re-seed.
            version.manifest_snapshot = manifest
            version.build_status = BuildStatus.SUCCESS
            logger.info("AppVersion %s@%s refreshed", APP_ID, APP_VERSION)

        if app.current_version_id != version.id:
            app.current_version_id = version.id
        db.commit()

    logger.info(
        "seed complete: app=%s workspace=%s template=%s",
        APP_ID,
        workspace,
        template,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
