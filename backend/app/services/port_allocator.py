"""Port allocator — atomically hands out reverse-proxy ports from a configured range.

Algorithm (single DB transaction):
  1. Reuse: pick the oldest row whose `released_at IS NOT NULL`, with `SELECT FOR UPDATE
     SKIP LOCKED`, mark it active, return its port.
  2. Expand: if no released row available and the pool isn't full, allocate the next
     unused port in the configured range.
  3. Exhausted: raise RuntimeError.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.port_allocation import PortAllocation

logger = logging.getLogger(__name__)


def allocate_port(
    db: Session,
    *,
    app_id: str | None = None,
    job_id: str | None = None,
    scope: str = "app",
) -> int:
    """Allocate a port for the given app/job, preferring released ports first.

    Raises RuntimeError("port pool exhausted") when the range is fully used.
    """
    settings = get_settings()
    low = settings.app_port_range_low
    high = settings.app_port_range_high

    # 1) Try to reuse the oldest released port — row-level lock prevents races.
    reuse_stmt = (
        select(PortAllocation)
        .where(PortAllocation.released_at.is_not(None))
        .order_by(PortAllocation.released_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    candidate = db.execute(reuse_stmt).scalar_one_or_none()
    if candidate is not None:
        candidate.app_id = app_id
        candidate.job_id = job_id
        candidate.scope = scope
        candidate.allocated_at = datetime.now(timezone.utc)
        candidate.released_at = None
        db.commit()
        logger.info("reused port %d for scope=%s app_id=%s", candidate.port, scope, app_id)
        return candidate.port

    # 2) Otherwise allocate a fresh port at the high-water mark + 1, within range.
    max_port = db.execute(select(func.max(PortAllocation.port))).scalar_one()
    next_port = low if max_port is None else max(low, int(max_port) + 1)
    if next_port > high:
        db.rollback()
        raise RuntimeError("port pool exhausted")

    row = PortAllocation(
        port=next_port,
        app_id=app_id,
        job_id=job_id,
        scope=scope,
    )
    db.add(row)
    db.commit()
    logger.info("allocated new port %d for scope=%s app_id=%s", next_port, scope, app_id)
    return next_port


def release_port(db: Session, port: int) -> None:
    """Mark a port as released so it can be reused. No-op if already released or unknown."""
    row = db.get(PortAllocation, port)
    if row is None:
        logger.warning("release_port called for unknown port %d", port)
        return
    if row.released_at is not None:
        return
    row.released_at = datetime.now(timezone.utc)
    db.commit()
    logger.info("released port %d", port)


def list_allocations(db: Session) -> list[PortAllocation]:
    """Return all port allocation rows, ordered by port number."""
    stmt = select(PortAllocation).order_by(PortAllocation.port.asc())
    return list(db.execute(stmt).scalars().all())
