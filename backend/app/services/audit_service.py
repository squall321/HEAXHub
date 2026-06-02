"""Audit log helper."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


def log(
    db: Session,
    *,
    actor_user_id: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: str,
    meta: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        meta=meta,
        ip_address=ip_address,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def safe_log(
    db: Session,
    *,
    actor_user_id: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: str,
    meta: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuditLog | None:
    """`log` that swallows database errors instead of bubbling them up.

    Use for fire-and-forget audit writes from request handlers where a failure
    to record audit should never roll back the actual operation.
    """
    try:
        return log(
            db,
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            meta=meta,
            ip_address=ip_address,
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit log write failed action=%s target=%s/%s",
                         action, target_type, target_id)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None
