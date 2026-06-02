"""Webhook handler tasks (GitHub tag push, etc)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.logger import get_logger
from app.db.models.app import App
from app.db.session import SessionLocal
from app.services.audit_service import log as audit_log
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="webhook_tasks.handle_github_tag")
def handle_github_tag(payload: dict[str, Any]) -> dict[str, object]:
    """Record a tag push as an audit_log entry. Actual rebuild remains operator-driven."""
    ref = payload.get("ref", "")
    repo = (payload.get("repository") or {}).get("clone_url") or (
        payload.get("repository") or {}
    ).get("html_url", "")
    if not ref.startswith("refs/tags/"):
        return {"ok": False, "skipped": True}
    tag = ref.removeprefix("refs/tags/")

    with SessionLocal() as db:
        # Match by upstream_repo_url substring
        matched: list[str] = []
        for app in db.execute(select(App)).scalars():
            if repo and repo.rstrip(".git") in (app.upstream_repo_url or "").rstrip(".git"):
                matched.append(app.id)
                audit_log(
                    db,
                    actor_user_id=None,
                    action="webhook.github_tag",
                    target_type="app",
                    target_id=app.id,
                    meta={"tag": tag, "repository": repo},
                )
    logger.info("github tag webhook tag=%s matched=%s", tag, matched)
    return {"ok": True, "tag": tag, "matched": matched}
