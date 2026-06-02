"""Celery tasks for upstream sync (clone, polling)."""
from __future__ import annotations

import uuid
from pathlib import Path

import yaml
from git import GitCommandError, Repo
from sqlalchemy import select

from app.core.logger import get_logger
from app.db.models.app import App
from app.db.models.app_version import AppVersion, BuildStatus
from app.db.models.submission import Submission, SubmissionStatus
from app.db.session import SessionLocal
from app.services import app_lifecycle, workspace_manager
from app.services.audit_service import log as audit_log
from app.services.source_fetcher import fetch_source
from app.workers.build_tasks import (
    build_apptainer_sif,
    build_nodejs,
    build_python_venv,
)
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="sync_tasks.clone_upstream")
def clone_upstream(submission_id: str) -> dict[str, object]:
    """Clone the upstream repo for an approved submission and provision the workspace."""
    sid = uuid.UUID(submission_id)

    with SessionLocal() as db:
        sub = db.get(Submission, sid)
        if sub is None:
            return {"ok": False, "error": "submission not found"}
        app, version = app_lifecycle.provision_workspace(db, submission_id=sid)
        workspace = Path(app.workspace_path)
        upstream_dir = workspace / "upstream"
        # Pick source_config (v2) or fall back to upstream_repo_url (v1).
        source_cfg = getattr(sub, "source_config", None)
        if not isinstance(source_cfg, dict) or not source_cfg:
            source_cfg = {"type": "git", "url": sub.upstream_repo_url}
        sub_url_for_lock = sub.upstream_repo_url

    # Fetch source (outside DB transaction).
    commit: str | None = None
    try:
        fetch_result = fetch_source(source_cfg, upstream_dir)
        commit = fetch_result.get("commit_sha")
        # Lock upstream read-only after a successful fetch.
        workspace_manager.lock_upstream_readonly(workspace)
    except GitCommandError as exc:
        logger.exception("git clone failed for submission=%s", submission_id)
        with SessionLocal() as db:
            sub = db.get(Submission, sid)
            if sub is not None:
                sub.status = SubmissionStatus.FAILED
                sub.review_notes = (sub.review_notes or "") + f"\n[clone error] {exc}"
                db.commit()
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        logger.exception("source fetch failed for submission=%s", submission_id)
        with SessionLocal() as db:
            sub = db.get(Submission, sid)
            if sub is not None:
                sub.status = SubmissionStatus.FAILED
                sub.review_notes = (sub.review_notes or "") + f"\n[fetch error] {exc}"
                db.commit()
        return {"ok": False, "error": str(exc)}

    # Look for an upstream manifest. If present, copy to overlay.
    upstream_manifest = upstream_dir / ".portal" / "manifest.yaml"
    overlay_manifest = workspace / "overlay" / ".portal" / "manifest.yaml"
    manifest_data: dict[str, object] | None = None
    if upstream_manifest.exists():
        manifest_data = yaml.safe_load(upstream_manifest.read_text(encoding="utf-8"))
        overlay_manifest.write_text(
            upstream_manifest.read_text(encoding="utf-8"), encoding="utf-8"
        )

    # Write upstream.lock
    (workspace / "overlay" / "upstream.lock").write_text(
        yaml.safe_dump(
            {
                "url": sub_url_for_lock,
                "commit": commit,
                "source": source_cfg,
            }
        ),
        encoding="utf-8",
    )

    # Update DB + enqueue build
    with SessionLocal() as db:
        sub = db.get(Submission, sid)
        if sub is None:
            return {"ok": False, "error": "submission disappeared"}
        version_row = db.get(AppVersion, version.id)
        if version_row is None:
            return {"ok": False, "error": "version row missing"}
        version_row.git_commit_hash = commit
        if isinstance(manifest_data, dict):
            version_row.manifest_snapshot = manifest_data
        sub.status = SubmissionStatus.BUILDING
        db.commit()
        audit_log(
            db,
            actor_user_id=None,
            action="submission.provisioned",
            target_type="submission",
            target_id=str(sub.id),
            meta={"commit": commit},
        )

    # Pick a build based on manifest.build.type (default to python_venv).
    build_type = "python_venv"
    if isinstance(manifest_data, dict):
        build_spec = manifest_data.get("build")
        if isinstance(build_spec, dict):
            build_type = str(build_spec.get("type", "python_venv"))

    if build_type == "python_venv":
        build_python_venv.delay(app.id, str(version.id))
    elif build_type == "nodejs":
        build_nodejs.delay(app.id, str(version.id))
    elif build_type == "apptainer":
        build_apptainer_sif.delay(app.id, str(version.id))
    else:
        # 'none' or 'external' — short-circuit to success
        with SessionLocal() as db:
            v = db.get(AppVersion, version.id)
            if v is not None:
                v.build_status = BuildStatus.SUCCESS
                db.commit()

    return {"ok": True, "commit": commit, "app_id": app.id}


@celery_app.task(name="sync_tasks.refresh_upstream")
def refresh_upstream(app_id: str) -> dict[str, object]:
    """Pull latest from the app's upstream repo, create a new AppVersion, enqueue build.

    Triggered by /admin/updates/{id}/approve. Does not auto-publish — operator must
    re-approve via the normal publish path after the build finishes.
    """
    with SessionLocal() as db:
        app = db.get(App, app_id)
        if app is None:
            return {"ok": False, "error": "app not found"}
        workspace = Path(app.workspace_path)
        upstream_url = (
            yaml.safe_load((workspace / "overlay" / "upstream.lock").read_text(encoding="utf-8"))
            or {}
        ).get("url") if (workspace / "overlay" / "upstream.lock").exists() else None

    upstream_dir = workspace / "upstream"
    if not upstream_dir.exists():
        return {"ok": False, "error": "upstream dir missing"}

    try:
        repo = Repo(str(upstream_dir))
        repo.remote().fetch(prune=True)
        repo.git.reset("--hard", "origin/HEAD")
        commit = repo.head.commit.hexsha
    except GitCommandError as exc:
        logger.exception("upstream refresh failed for app=%s", app_id)
        return {"ok": False, "error": str(exc)}

    # Refresh overlay manifest from upstream if present
    upstream_manifest = upstream_dir / ".portal" / "manifest.yaml"
    overlay_manifest = workspace / "overlay" / ".portal" / "manifest.yaml"
    manifest_data: dict[str, object] | None = None
    if upstream_manifest.exists():
        manifest_data = yaml.safe_load(upstream_manifest.read_text(encoding="utf-8"))
        overlay_manifest.parent.mkdir(parents=True, exist_ok=True)
        overlay_manifest.write_text(
            upstream_manifest.read_text(encoding="utf-8"), encoding="utf-8"
        )

    (workspace / "overlay" / "upstream.lock").write_text(
        yaml.safe_dump({"url": upstream_url, "commit": commit}),
        encoding="utf-8",
    )

    # Create a new AppVersion row
    new_version_id: uuid.UUID
    with SessionLocal() as db:
        version_str = (
            str(manifest_data.get("version")) if isinstance(manifest_data, dict) else "0.0.0"
        )
        new_v = AppVersion(
            id=uuid.uuid4(),
            app_id=app_id,
            version=version_str,
            git_commit_hash=commit,
            build_status=BuildStatus.PENDING,
            manifest_snapshot=manifest_data if isinstance(manifest_data, dict) else None,
        )
        db.add(new_v)
        db.commit()
        new_version_id = new_v.id
        audit_log(
            db,
            actor_user_id=None,
            action="app.upstream_refreshed",
            target_type="app",
            target_id=app_id,
            meta={"commit": commit, "version": version_str},
        )

    # Pick build type
    build_type = "python_venv"
    if isinstance(manifest_data, dict):
        bs = manifest_data.get("build")
        if isinstance(bs, dict):
            build_type = str(bs.get("type", "python_venv"))

    if build_type == "python_venv":
        build_python_venv.delay(app_id, str(new_version_id))
    elif build_type == "nodejs":
        build_nodejs.delay(app_id, str(new_version_id))
    elif build_type == "apptainer":
        build_apptainer_sif.delay(app_id, str(new_version_id))
    else:
        with SessionLocal() as db:
            v = db.get(AppVersion, new_version_id)
            if v is not None:
                v.build_status = BuildStatus.SUCCESS
                db.commit()

    return {"ok": True, "commit": commit, "version_id": str(new_version_id)}


@celery_app.task(name="sync_tasks.check_upstream_updates")
def check_upstream_updates() -> dict[str, object]:
    """Poll all stable apps for new upstream commits.

    For each app, run `git ls-remote` to compare against the recorded lock.
    Mismatches are recorded as audit_log entries; actual rebuild is operator-driven.
    """
    updates: list[dict[str, str]] = []
    with SessionLocal() as db:
        apps = db.execute(select(App)).scalars().all()
        for app in apps:
            workspace = Path(app.workspace_path)
            lock_file = workspace / "overlay" / "upstream.lock"
            if not lock_file.exists():
                continue
            try:
                lock = yaml.safe_load(lock_file.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            current = lock.get("commit")
            try:
                refs = Repo(str(workspace / "upstream")).remote().fetch()
                if not refs:
                    continue
                latest = refs[0].commit.hexsha
            except Exception:
                logger.exception("ls-remote failed for app=%s", app.id)
                continue
            if latest and latest != current:
                updates.append({"app_id": app.id, "old": str(current), "new": latest})
                audit_log(
                    db,
                    actor_user_id=None,
                    action="upstream.update_available",
                    target_type="app",
                    target_id=app.id,
                    meta={"old": str(current), "new": latest},
                )
    return {"updates": updates}
