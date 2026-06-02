"""License pool manager — token reservation against FlexLM/RLM-style pools.

Uses ``SELECT ... FOR UPDATE`` on the pool row to serialise concurrent acquires,
then sums currently-active holdings and inserts a new ``LicenseHolding`` row if
capacity is available.

Sync SQLAlchemy. Designed to be called from Celery tasks (runners).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFoundError
from app.core.logger import get_logger
from app.db.models.license_holding import LicenseHolding
from app.db.models.license_pool import LicensePool
from app.services.license_providers import UNKNOWN_AVAILABLE, get_provider

logger = get_logger(__name__)


class LicenseUnavailableError(AppError):
    """Raised when the upstream license server (FlexLM/mock) reports zero
    available tokens for the pool's feature — the DB pool may have room but
    the external provider does not, so we must not even try to grab a row."""

    status_code = 409
    code = "license_unavailable"


def _get_pool_for_update(db: Session, pool_name: str) -> LicensePool:
    """Lock the pool row for the rest of the transaction."""
    stmt = (
        select(LicensePool)
        .where(LicensePool.name == pool_name)
        .with_for_update()
    )
    pool = db.execute(stmt).scalar_one_or_none()
    if pool is None:
        raise NotFoundError(f"License pool '{pool_name}' not found")
    return pool


def _active_tokens(db: Session, pool_id) -> int:
    """Sum tokens of holdings that have not been released yet."""
    from sqlalchemy import func as sa_func

    total = (
        db.execute(
            select(sa_func.coalesce(sa_func.sum(LicenseHolding.tokens), 0))
            .where(LicenseHolding.pool_id == pool_id)
            .where(LicenseHolding.released_at.is_(None))
        )
        .scalar_one()
    )
    return int(total or 0)


def _try_acquire_once(
    db: Session, *, pool_name: str, tokens: int, job_id: str
) -> LicenseHolding | None:
    """Single attempt — locks pool, checks free, inserts holding if it fits."""
    pool = _get_pool_for_update(db, pool_name)
    held = _active_tokens(db, pool.id)
    if held + tokens > pool.total_tokens:
        db.rollback()
        return None
    holding = LicenseHolding(
        pool_id=pool.id,
        job_id=job_id,
        tokens=tokens,
    )
    db.add(holding)
    db.commit()
    db.refresh(holding)
    return holding


def _provider_precheck(db: Session, pool_name: str, tokens: int) -> None:
    """Ask the external provider whether ``tokens`` are currently free.

    Raises :class:`LicenseUnavailableError` if the provider definitively
    reports fewer than ``tokens`` available. A provider that returns the
    ``UNKNOWN_AVAILABLE`` sentinel is treated as "no opinion" — we fall
    through to the DB-only check so the system still works when FlexLM is
    offline (degraded mode).
    """
    pool = db.execute(
        select(LicensePool).where(LicensePool.name == pool_name)
    ).scalar_one_or_none()
    if pool is None:
        raise NotFoundError(f"License pool '{pool_name}' not found")
    feature = pool.feature or pool.name
    provider = get_provider(db)
    available = provider.check_available(feature)
    if available == UNKNOWN_AVAILABLE:
        return  # degraded / unknown — defer to DB check
    if available < tokens:
        raise LicenseUnavailableError(
            f"Provider '{provider.name}' reports {available} tokens free for "
            f"feature '{feature}' (need {tokens})",
            details={
                "provider": provider.name,
                "feature": feature,
                "available": available,
                "requested": tokens,
            },
        )


def acquire(
    db: Session,
    *,
    pool_name: str,
    tokens: int,
    job_id: str,
    wait_seconds: int = 600,
) -> LicenseHolding | None:
    """Try to acquire ``tokens`` from ``pool_name`` for the given job.

    Polls every 5 seconds until ``wait_seconds`` is exhausted. Returns the
    ``LicenseHolding`` on success, or ``None`` if the wait timed out. Raises
    :class:`NotFoundError` if the pool does not exist, or
    :class:`LicenseUnavailableError` if the external provider says the
    feature is exhausted *before* we even touch the DB row.
    """
    if tokens <= 0:
        raise ValueError("tokens must be positive")
    # Fail fast against the upstream provider — avoids holding a DB lock when
    # the real license server has no tokens to give.
    _provider_precheck(db, pool_name, tokens)
    deadline = time.monotonic() + max(0, wait_seconds)
    poll_interval = 5
    while True:
        holding = _try_acquire_once(
            db, pool_name=pool_name, tokens=tokens, job_id=job_id
        )
        if holding is not None:
            logger.info(
                "license acquired pool=%s tokens=%s job=%s", pool_name, tokens, job_id
            )
            return holding
        if time.monotonic() >= deadline:
            logger.warning(
                "license acquire timeout pool=%s tokens=%s job=%s wait=%ss",
                pool_name,
                tokens,
                job_id,
                wait_seconds,
            )
            return None
        time.sleep(poll_interval)


def release(db: Session, holding: LicenseHolding) -> None:
    """Mark the holding as released. Idempotent."""
    if holding.released_at is not None:
        return
    holding.released_at = datetime.now(timezone.utc)
    db.add(holding)
    db.commit()
    logger.info(
        "license released pool=%s tokens=%s job=%s",
        holding.pool_id,
        holding.tokens,
        holding.job_id,
    )


def list_active(db: Session, pool_name: str | None = None) -> list[LicenseHolding]:
    """List currently-active holdings, optionally scoped to one pool."""
    stmt = select(LicenseHolding).where(LicenseHolding.released_at.is_(None))
    if pool_name is not None:
        pool = db.execute(
            select(LicensePool).where(LicensePool.name == pool_name)
        ).scalar_one_or_none()
        if pool is None:
            return []
        stmt = stmt.where(LicenseHolding.pool_id == pool.id)
    return list(db.execute(stmt).scalars())


def pool_status(db: Session, pool_name: str) -> dict:
    """Return ``{name, total, in_use, free}`` for one pool."""
    pool = db.execute(
        select(LicensePool).where(LicensePool.name == pool_name)
    ).scalar_one_or_none()
    if pool is None:
        raise NotFoundError(f"License pool '{pool_name}' not found")
    in_use = _active_tokens(db, pool.id)
    return {
        "name": pool.name,
        "total": pool.total_tokens,
        "in_use": in_use,
        "free": max(0, pool.total_tokens - in_use),
    }
