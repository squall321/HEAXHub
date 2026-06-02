"""ApptainerRunner — executes a job inside a built SIF image.

Lifecycle mirrors :class:`LocalRunner`:

1. Resolve the SIF via ``manifest.launch.image_ref`` (preferred) or
   ``AppVersion.sif_path`` (fallback for legacy rows).
2. Acquire manifest-declared license + GPU reservations via
   :class:`ResourceContext`.
3. Spawn ``apptainer exec [--nv] --bind workspace:/workdir SIF bash -c ...``.
4. Tee stdout to ``logs/stdout.log`` and Redis channel ``logs:{job_id}``.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import redis

from app.config import get_settings
from app.core.errors import NotFoundError
from app.core.logger import get_logger
from app.db.models.app_version import AppVersion
from app.db.models.job import Job
from app.db.session import SessionLocal
from app.runners.base import BaseRunner, JobResult
from app.runners.resource_hooks import ResourceContext
from app.services.sif_registry import resolve_sif as _registry_resolve_sif

logger = get_logger(__name__)

# job_id → Popen
_RUNNING: dict[str, subprocess.Popen[bytes]] = {}
_LOCK = threading.Lock()


def _redis() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url)


def _publish_line(client: redis.Redis, job_id: str, line: str) -> None:
    try:
        client.publish(f"logs:{job_id}", line)
    except Exception:
        logger.exception("redis publish failed")


def _default_binds() -> list[str]:
    try:
        return list(get_settings().apptainer_default_bind_list)
    except Exception:
        return []


def _resolve_image_ref(image_ref: dict[str, Any]) -> Path:
    """Translate a manifest ``launch.image_ref`` dict into a host SIF path.

    Delegates to :func:`app.services.sif_registry.resolve_sif` which supports
    ``local_path`` and ``registry`` types (and raises ``NotImplementedError``
    for ``minio``).
    """
    return _registry_resolve_sif(image_ref)


def _sif_path_for_job(job: Job) -> Path:
    """Resolve the SIF path for this job.

    Preference order:
      1. ``manifest.launch.image_ref`` from the AppVersion snapshot
      2. legacy ``AppVersion.sif_path`` column
    """
    if not job.app_version_id:
        raise NotFoundError(
            f"Job {job.id} has no app_version_id; cannot resolve SIF"
        )
    with SessionLocal() as db:
        version = db.get(AppVersion, job.app_version_id)
        if version is None:
            raise NotFoundError(
                f"AppVersion {job.app_version_id} not found"
            )
        manifest = version.manifest_snapshot or {}
        launch = manifest.get("launch") or {} if isinstance(manifest, dict) else {}
        image_ref = launch.get("image_ref") if isinstance(launch, dict) else None
        if isinstance(image_ref, dict) and image_ref:
            return _resolve_image_ref(image_ref)
        if version.sif_path:
            return Path(version.sif_path)
        raise NotFoundError(
            f"AppVersion {job.app_version_id} has neither manifest.launch.image_ref "
            f"nor a built sif_path"
        )


class ApptainerRunner(BaseRunner):
    name = "apptainer"

    def start(self, job: Job) -> int:
        storage = Path(job.storage_path)
        sif = _sif_path_for_job(job)
        if not sif.exists():
            raise FileNotFoundError(f"SIF not found: {sif}")

        with ResourceContext(job=job) as ctx:
            # Env *inside* the container — JOB_* paths reference /workdir.
            container_env: dict[str, str] = {
                "JOB_ID": job.id,
                "APP_ID": job.app_id,
                "JOB_INPUT": "/workdir/input",
                "JOB_OUTPUT": "/workdir/output",
                "JOB_PARAMS": "/workdir/params.json",
            }
            # Merge ResourceContext additions (secrets + CUDA_VISIBLE_DEVICES).
            container_env = ctx.env(container_env)

            # Host env: forward each container var via APPTAINERENV_*.
            host_env = os.environ.copy()
            for key, value in container_env.items():
                if value is None:
                    continue
                host_env[f"APPTAINERENV_{key}"] = str(value)

            binds: list[str] = [f"{storage}:/workdir"]
            binds.extend(_default_binds())
            bind_args: list[str] = []
            for b in binds:
                bind_args += ["--bind", b]

            # Per spec: cd into /workdir, exec ./run.sh with positional args.
            inner_cmd = (
                "cd /workdir && "
                "exec ./run.sh input output params.json"
            )

            cmd: list[str] = [get_settings().apptainer_bin, "exec"]
            if ctx.gpu_count:
                cmd.append("--nv")
            cmd.extend(bind_args)
            cmd.extend([str(sif), "bash", "-c", inner_cmd])

            log_file = storage / "logs" / "stdout.log"
            log_file.parent.mkdir(parents=True, exist_ok=True)

            rclient = _redis()
            _publish_line(rclient, job.id, f"$ {' '.join(cmd)}")

            with log_file.open("ab") as log_fp:
                proc = subprocess.Popen(  # noqa: S603
                    cmd,
                    env=host_env,
                    cwd=str(storage),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=0,
                    start_new_session=True,
                )
                with _LOCK:
                    _RUNNING[job.id] = proc
                try:
                    assert proc.stdout is not None
                    for raw in iter(proc.stdout.readline, b""):
                        log_fp.write(raw)
                        log_fp.flush()
                        try:
                            line = raw.decode("utf-8", errors="replace").rstrip("\n")
                        except Exception:
                            line = "<undecodable line>"
                        _publish_line(rclient, job.id, line)
                    proc.wait()
                finally:
                    with _LOCK:
                        _RUNNING.pop(job.id, None)

            _publish_line(rclient, job.id, f"__exit__:{proc.returncode}")
            return proc.returncode or 0

    async def stream_logs(self, job_id: str) -> AsyncIterator[str]:
        rclient = _redis()
        pubsub = rclient.pubsub()
        pubsub.subscribe(f"logs:{job_id}")
        try:
            while True:
                msg = await asyncio.to_thread(pubsub.get_message, timeout=1.0)
                if msg is None:
                    await asyncio.sleep(0.1)
                    continue
                if msg.get("type") != "message":
                    continue
                data = msg.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="replace")
                yield str(data)
                if isinstance(data, str) and data.startswith("__exit__:"):
                    break
        finally:
            try:
                pubsub.unsubscribe(f"logs:{job_id}")
                pubsub.close()
            except Exception:
                pass

    def cancel(self, job_id: str) -> bool:
        with _LOCK:
            proc = _RUNNING.get(job_id)
        if proc is None:
            return False
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            try:
                proc.send_signal(signal.SIGTERM)
            except Exception:
                logger.exception("Failed to signal apptainer process for job=%s", job_id)
                return False
        return True

    def collect_results(self, job: Job) -> JobResult:
        storage = Path(job.storage_path)
        result_path = storage / "output" / "result.json"
        if not result_path.exists():
            return JobResult(
                status="failed",
                summary={},
                outputs={},
                warnings=[],
                errors=["result.json missing"],
            )
        try:
            raw: dict[str, Any] = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return JobResult(
                status="failed",
                summary={},
                outputs={},
                warnings=[],
                errors=[f"result.json parse error: {exc}"],
            )
        return JobResult(
            status=str(raw.get("status", "success")),
            summary=raw.get("summary", {}) or {},
            outputs=raw.get("outputs", {}) or {},
            warnings=list(raw.get("warnings", []) or []),
            errors=list(raw.get("errors", []) or []),
            raw=raw,
        )
