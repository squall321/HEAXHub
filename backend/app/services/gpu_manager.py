"""GPU inventory + reservation manager.

- ``discover_local_gpus()`` shells out to ``nvidia-smi`` and returns the parsed
  list. If the binary is missing, returns an empty list.
- ``register_gpus(db)`` upserts ``gpu_devices`` rows keyed on
  ``(host, device_index)``.
- ``acquire(db, ...)`` picks free devices matching the manifest constraints,
  locking with ``FOR UPDATE SKIP LOCKED`` and flipping ``status='busy'``.
- ``release(db, holdings)`` flips them back to ``free`` and stamps the
  ``released_at`` timestamp on each holding.

All operations use sync SQLAlchemy sessions.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logger import get_logger
from app.db.models.gpu_device import GpuDevice
from app.db.models.gpu_holding import GpuHolding

logger = get_logger(__name__)

NVIDIA_SMI_QUERY = "index,uuid,name,memory.total,compute_cap"


def _hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "localhost"


def discover_local_gpus() -> list[dict]:
    """Run ``nvidia-smi`` and parse the output. Empty list if the tool is missing."""
    binary = shutil.which("nvidia-smi")
    if binary is None:
        return []
    try:
        out = subprocess.check_output(  # noqa: S603
            [
                binary,
                f"--query-gpu={NVIDIA_SMI_QUERY}",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("nvidia-smi failed: %s", exc)
        return []

    gpus: list[dict] = []
    for raw_line in out.strip().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            idx = int(parts[0])
        except ValueError:
            continue
        try:
            memory_mb = int(float(parts[3]))
        except ValueError:
            memory_mb = None
        gpus.append(
            {
                "device_index": idx,
                "uuid": parts[1] or None,
                "model": parts[2] or None,
                "memory_mb": memory_mb,
                "cuda_capability": parts[4] or None,
            }
        )
    return gpus


def register_gpus(db: Session) -> int:
    """Upsert ``gpu_devices`` for the current host. Returns the number of rows touched."""
    host = _hostname()
    discovered = discover_local_gpus()
    existing = {
        d.device_index: d
        for d in db.execute(
            select(GpuDevice).where(GpuDevice.host == host)
        ).scalars()
    }
    touched = 0
    for spec in discovered:
        row = existing.get(spec["device_index"])
        if row is None:
            row = GpuDevice(
                host=host,
                device_index=spec["device_index"],
                uuid=spec.get("uuid"),
                model=spec.get("model"),
                cuda_capability=spec.get("cuda_capability"),
                memory_mb=spec.get("memory_mb"),
                status="free",
            )
            db.add(row)
        else:
            row.uuid = spec.get("uuid") or row.uuid
            row.model = spec.get("model") or row.model
            row.cuda_capability = spec.get("cuda_capability") or row.cuda_capability
            row.memory_mb = spec.get("memory_mb") or row.memory_mb
        touched += 1
    db.commit()
    logger.info("registered %s GPUs for host=%s", touched, host)
    return touched


def _cuda_at_least(cap: str | None, minimum: str | None) -> bool:
    if not minimum:
        return True
    if not cap:
        return False
    try:
        cap_t = tuple(int(p) for p in cap.split("."))
        min_t = tuple(int(p) for p in minimum.split("."))
    except ValueError:
        return False
    return cap_t >= min_t


def acquire(
    db: Session,
    *,
    job_id: str,
    count: int,
    min_memory_gb: int | None = None,
    cuda_min: str | None = None,
) -> list[GpuDevice]:
    """Reserve ``count`` GPUs that satisfy the constraints. Empty list = none free."""
    if count <= 0:
        return []
    min_mem_mb = (min_memory_gb or 0) * 1024
    host = _hostname()

    stmt = (
        select(GpuDevice)
        .where(GpuDevice.host == host)
        .where(GpuDevice.status == "free")
        .order_by(GpuDevice.device_index)
        .with_for_update(skip_locked=True)
    )
    if min_mem_mb:
        stmt = stmt.where(GpuDevice.memory_mb >= min_mem_mb)

    candidates = list(db.execute(stmt).scalars())
    # CUDA capability filter (string compare not safe — use tuple compare)
    matching = [d for d in candidates if _cuda_at_least(d.cuda_capability, cuda_min)]
    if len(matching) < count:
        db.rollback()
        return []

    chosen = matching[:count]
    now = datetime.now(timezone.utc)
    for dev in chosen:
        dev.status = "busy"
        db.add(dev)
        db.add(GpuHolding(device_id=dev.id, job_id=job_id, acquired_at=now))
    db.commit()
    logger.info(
        "GPU acquired job=%s devices=%s",
        job_id,
        [d.device_index for d in chosen],
    )
    return chosen


def release(db: Session, holdings: list[GpuHolding]) -> None:
    """Flip devices back to ``free`` and stamp ``released_at``. Idempotent."""
    if not holdings:
        return
    now = datetime.now(timezone.utc)
    for h in holdings:
        if h.released_at is None:
            h.released_at = now
            db.add(h)
        dev = db.get(GpuDevice, h.device_id)
        if dev is not None and dev.status != "free":
            dev.status = "free"
            db.add(dev)
    db.commit()
    logger.info(
        "GPU released devices=%s", [h.device_id for h in holdings]
    )


def release_for_job(db: Session, *, job_id: str) -> None:
    """Convenience: release any still-active holdings for a job."""
    active = list(
        db.execute(
            select(GpuHolding)
            .where(GpuHolding.job_id == job_id)
            .where(GpuHolding.released_at.is_(None))
        ).scalars()
    )
    if active:
        release(db, active)
