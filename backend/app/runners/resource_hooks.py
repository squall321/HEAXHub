"""Shared hooks that runners use to acquire/release licenses + GPUs + secrets.

Both ``LocalRunner`` and ``ApptainerRunner`` need the same lifecycle:
1. read manifest from the AppVersion snapshot,
2. inject env from ``env_required`` via secret_manager (lazy),
3. acquire license tokens (blocking) and GPUs (best-effort),
4. yield to the caller (runs the actual subprocess),
5. release everything (idempotent).

Designed for use as a context manager:

    with ResourceContext(job=job) as ctx:
        env = ctx.env(os.environ.copy())
        ... run subprocess with env ...
"""
from __future__ import annotations

import os
from contextlib import AbstractContextManager
from typing import Any

from app.config import get_settings
from app.core.errors import AppError
from app.core.logger import get_logger
from app.db.models.app import App
from app.db.models.app_version import AppVersion
from app.db.models.gpu_holding import GpuHolding
from app.db.models.job import Job
from app.db.models.license_holding import LicenseHolding
from app.db.session import SessionLocal
from app.services import gpu_manager, license_manager, secret_manager

logger = get_logger(__name__)


class ResourceAcquireError(AppError):
    """Raised when a manifest-declared resource (license/GPU) can't be obtained."""

    status_code = 503
    code = "resource_unavailable"


def _manifest(job: Job) -> dict[str, Any]:
    if not job.app_version_id:
        return {}
    with SessionLocal() as db:
        version = db.get(AppVersion, job.app_version_id)
        if version is None or not version.manifest_snapshot:
            return {}
        return dict(version.manifest_snapshot)


def _inject_secrets(app_id: str, env: dict[str, str], env_required: list[str]) -> dict[str, str]:
    """Use secret_manager if available; otherwise pull from os.environ as a fallback."""
    if not env_required:
        return env
    try:
        with SessionLocal() as db:
            return secret_manager.inject_for_app(  # type: ignore[attr-defined]
                db, app_id=app_id, env=env, keys=env_required
            )
    except Exception:
        logger.exception("secret_manager.inject_for_app failed — falling back to os.environ")
    for key in env_required:
        if key not in env and key in os.environ:
            env[key] = os.environ[key]
    return env


class ResourceContext(AbstractContextManager["ResourceContext"]):
    """Holds onto license + GPU reservations for the lifetime of a runner call."""

    def __init__(self, *, job: Job) -> None:
        self.job = job
        self.manifest: dict[str, Any] = _manifest(job)
        self.license_holding: LicenseHolding | None = None
        self.gpu_holdings: list[GpuHolding] = []
        self.gpu_devices: list[Any] = []
        self._db = None

    # -- enter / exit -------------------------------------------------------

    def __enter__(self) -> "ResourceContext":
        self._db = SessionLocal()
        try:
            self._acquire_license()
            self._acquire_gpus()
        except Exception:
            self.release()
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    # -- public --------------------------------------------------------------

    def env(self, base: dict[str, str]) -> dict[str, str]:
        """Add manifest-driven env (secrets + CUDA_VISIBLE_DEVICES)."""
        env_required = list(self.manifest.get("env_required") or [])
        env = _inject_secrets(self.job.app_id, base, env_required)
        if self.gpu_devices:
            env["CUDA_VISIBLE_DEVICES"] = ",".join(
                str(d.device_index) for d in self.gpu_devices
            )
        return env

    def release(self) -> None:
        try:
            if self.license_holding is not None and self._db is not None:
                license_manager.release(self._db, self.license_holding)
        except Exception:
            logger.exception("license release failed for job=%s", self.job.id)
        try:
            if self.gpu_holdings and self._db is not None:
                gpu_manager.release(self._db, self.gpu_holdings)
        except Exception:
            logger.exception("gpu release failed for job=%s", self.job.id)
        if self._db is not None:
            try:
                self._db.close()
            except Exception:
                pass
            self._db = None

    @property
    def gpu_count(self) -> int:
        return len(self.gpu_devices)

    # -- internals -----------------------------------------------------------

    def _acquire_license(self) -> None:
        lic = self.manifest.get("license") or {}
        if not isinstance(lic, dict) or not lic.get("pool"):
            return
        pool = str(lic["pool"])
        tokens = int(lic.get("tokens") or 1)
        wait = int(lic.get("wait_seconds") or 600)
        holding = license_manager.acquire(
            self._db,  # type: ignore[arg-type]
            pool_name=pool,
            tokens=tokens,
            job_id=self.job.id,
            wait_seconds=wait,
        )
        if holding is None:
            raise ResourceAcquireError(
                f"License '{pool}' unavailable after {wait}s "
                f"(need {tokens} tokens)"
            )
        self.license_holding = holding

    def _acquire_gpus(self) -> None:
        resources = self.manifest.get("resources") or {}
        gpu_spec = resources.get("gpu") if isinstance(resources, dict) else None
        if not gpu_spec:
            return
        if isinstance(gpu_spec, bool):
            if not gpu_spec:
                return
            count, min_mem, cuda_min = 1, None, None
        else:
            count = int(gpu_spec.get("count") or 1)
            min_mem = gpu_spec.get("min_memory_gb")
            cuda_min = gpu_spec.get("cuda_min")
        devices = gpu_manager.acquire(
            self._db,  # type: ignore[arg-type]
            job_id=self.job.id,
            count=count,
            min_memory_gb=int(min_mem) if min_mem else None,
            cuda_min=str(cuda_min) if cuda_min else None,
        )
        if len(devices) < count:
            raise ResourceAcquireError(
                f"Only {len(devices)}/{count} GPUs available matching constraints"
            )
        self.gpu_devices = devices
        # Re-query the holdings we just inserted
        from sqlalchemy import select

        holdings = list(
            self._db.execute(  # type: ignore[union-attr]
                select(GpuHolding)
                .where(GpuHolding.job_id == self.job.id)
                .where(GpuHolding.released_at.is_(None))
            ).scalars()
        )
        self.gpu_holdings = holdings


def app_workspace_for(job: Job) -> str:
    return str(get_settings().workspace_root / job.app_id)


__all__ = ["ResourceContext", "ResourceAcquireError", "app_workspace_for"]
