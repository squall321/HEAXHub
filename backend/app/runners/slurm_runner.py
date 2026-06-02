"""SlurmRunner — submits a job to Slurm via ``sbatch`` and tails its log.

Lifecycle:

1. ``start(job)`` writes a generated ``sbatch`` script into the job storage
   (``logs/slurm.sbatch``), submits via ``sbatch --parsable``, captures the
   returned Slurm job id, and stores it in Redis under
   ``slurm:job:{job.id} → slurm_job_id`` so :class:`SlurmRunner.cancel` and the
   ``slurm_tasks.poll_slurm_jobs`` Celery task can find it without a DB
   migration.

   The sbatch script:
     * uses the same ``${workspace}/upstream`` directory and the same
       ``overlay/.portal/run.sh`` (falling back to ``upstream/run.sh``)
       contract as :class:`LocalRunner`;
     * tees stdout/stderr into ``logs/stdout.log`` (matching LocalRunner) so
       the existing log-tailing UI just works;
     * uses sbatch directives derived from
       ``AppVersion.manifest_snapshot.resources``.

2. ``stream_logs(job_id)`` tails ``logs/stdout.log`` and publishes new lines
   onto Redis pubsub channel ``logs:{job_id}`` — same channel LocalRunner
   uses, so the WebSocket endpoint is unchanged. We use a tail-from-file
   pump because the sbatch script writes to the same file directly.

3. ``cancel(job_id)`` looks up the slurm_job_id and shells out to ``scancel``.

4. ``collect_results(job)`` reads ``output/result.json`` (identical to
   LocalRunner).

A separate periodic Celery task (:mod:`app.workers.slurm_tasks`) polls
``squeue`` / ``sacct`` to advance ``Job.status`` for in-flight slurm jobs.
"""
from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import redis

from app.config import get_settings
from app.core.logger import get_logger
from app.db.models.app_version import AppVersion
from app.db.models.job import Job
from app.db.session import SessionLocal
from app.runners.base import BaseRunner, JobResult

logger = get_logger(__name__)


# --- Redis helpers -----------------------------------------------------------


def _redis() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url)


def _slurm_id_key(job_id: str) -> str:
    return f"slurm:job:{job_id}"


def _store_slurm_id(client: redis.Redis, job_id: str, slurm_job_id: str) -> None:
    try:
        # Keep for 7 days — long enough for sacct accounting to settle.
        client.set(_slurm_id_key(job_id), slurm_job_id, ex=7 * 24 * 3600)
        client.sadd("slurm:active_jobs", job_id)
    except Exception:
        logger.exception("failed to store slurm job id for %s", job_id)


def _get_slurm_id(client: redis.Redis, job_id: str) -> str | None:
    try:
        raw = client.get(_slurm_id_key(job_id))
        if raw is None:
            return None
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    except Exception:
        logger.exception("failed to read slurm job id for %s", job_id)
        return None


def _forget_slurm_id(client: redis.Redis, job_id: str) -> None:
    try:
        client.delete(_slurm_id_key(job_id))
        client.srem("slurm:active_jobs", job_id)
    except Exception:
        logger.exception("failed to forget slurm job id for %s", job_id)


def _publish_line(client: redis.Redis, job_id: str, line: str) -> None:
    try:
        client.publish(f"logs:{job_id}", line)
    except Exception:
        logger.exception("redis publish failed")


# --- Manifest / resource extraction ------------------------------------------


def _load_manifest(job: Job) -> dict[str, Any]:
    if not job.app_version_id:
        return {}
    with SessionLocal() as db:
        version = db.get(AppVersion, job.app_version_id)
        if version is None or not version.manifest_snapshot:
            return {}
        return dict(version.manifest_snapshot)


def _sbatch_directives(job: Job, manifest: dict[str, Any]) -> list[str]:
    """Translate ``manifest.resources`` → ``#SBATCH`` lines."""
    settings = get_settings()
    resources = manifest.get("resources") if isinstance(manifest, dict) else None
    resources = resources if isinstance(resources, dict) else {}

    partition = str(resources.get("partition") or settings.slurm_default_partition)
    time_limit_minutes = int(
        resources.get("time_limit_minutes") or settings.slurm_default_time_minutes
    )
    hours, minutes = divmod(max(time_limit_minutes, 1), 60)
    time_str = f"{hours:02d}:{minutes:02d}:00"

    directives: list[str] = [
        f"#SBATCH --job-name=heaxhub-{job.id}",
        f"#SBATCH --partition={partition}",
        f"#SBATCH --time={time_str}",
    ]

    cpu = resources.get("cpu")
    if cpu:
        try:
            directives.append(f"#SBATCH --cpus-per-task={int(cpu)}")
        except (TypeError, ValueError):
            logger.warning("ignoring non-int cpu spec for job=%s: %r", job.id, cpu)

    memory_gb = resources.get("memory_gb")
    if memory_gb:
        try:
            mem_mb = int(float(memory_gb) * 1024)
            directives.append(f"#SBATCH --mem={mem_mb}M")
        except (TypeError, ValueError):
            logger.warning(
                "ignoring non-numeric memory_gb for job=%s: %r", job.id, memory_gb
            )

    gpu_spec = resources.get("gpu")
    gpu_count = 0
    if isinstance(gpu_spec, bool):
        gpu_count = 1 if gpu_spec else 0
    elif isinstance(gpu_spec, dict):
        try:
            gpu_count = int(gpu_spec.get("count") or 1)
        except (TypeError, ValueError):
            gpu_count = 1
    elif isinstance(gpu_spec, int):
        gpu_count = gpu_spec
    if gpu_count > 0:
        directives.append(f"#SBATCH --gres=gpu:{gpu_count}")

    return directives


# --- Workspace + run.sh resolution -------------------------------------------


def _workspace_for_app(app_id: str) -> Path:
    return get_settings().workspace_root / app_id


def _resolve_run_script(workspace: Path) -> Path:
    candidate = workspace / "overlay" / ".portal" / "run.sh"
    if candidate.exists():
        return candidate
    fallback = workspace / "upstream" / "run.sh"
    if fallback.exists():
        return fallback
    raise FileNotFoundError(
        f"No run.sh found at overlay/.portal/run.sh or upstream/run.sh under {workspace}"
    )


def _build_sbatch_script(job: Job, manifest: dict[str, Any]) -> str:
    """Return the full sbatch script text for ``job``.

    The script is fully self-contained: directives at the top, then a bash
    body that ``cd``s into the workspace, exports ``JOB_*`` env vars, and
    execs ``run.sh input output params.json`` with stdout/stderr tee'd to
    ``logs/stdout.log``.
    """
    storage = Path(job.storage_path)
    workspace = _workspace_for_app(job.app_id)
    run_script = _resolve_run_script(workspace)

    log_file = storage / "logs" / "stdout.log"

    directives = _sbatch_directives(job, manifest)
    directives.append(f"#SBATCH --output={log_file}")
    directives.append(f"#SBATCH --error={log_file}")
    directives.append(f"#SBATCH --chdir={workspace / 'upstream'}")

    venv_bin = workspace / "venv" / "bin"
    venv_export = ""
    if venv_bin.exists():
        venv_export = (
            f'export PATH={shlex.quote(str(venv_bin))}:"$PATH"\n'
            f'export VIRTUAL_ENV={shlex.quote(str(workspace / "venv"))}\n'
        )

    body = f"""#!/bin/bash
{chr(10).join(directives)}

set -o pipefail

export JOB_ID={shlex.quote(job.id)}
export APP_ID={shlex.quote(job.app_id)}
export JOB_INPUT={shlex.quote(str(storage / "input"))}
export JOB_OUTPUT={shlex.quote(str(storage / "output"))}
export JOB_PARAMS={shlex.quote(str(storage / "params.json"))}
export PYTHONPATH={shlex.quote(str(workspace / "upstream"))}
{venv_export}
mkdir -p {shlex.quote(str(log_file.parent))}

echo "$ bash {shlex.quote(str(run_script))} {shlex.quote(str(storage / "input"))} {shlex.quote(str(storage / "output"))} {shlex.quote(str(storage / "params.json"))}"

bash {shlex.quote(str(run_script))} \\
    {shlex.quote(str(storage / "input"))} \\
    {shlex.quote(str(storage / "output"))} \\
    {shlex.quote(str(storage / "params.json"))}

rc=$?
echo "__exit__:${{rc}}"
exit $rc
"""
    return body


# --- Runner ------------------------------------------------------------------


class SlurmRunner(BaseRunner):
    name = "slurm"
    is_async = True

    def start(self, job: Job) -> str:
        """Submit ``job`` via sbatch and return the captured slurm_job_id.

        Does NOT block until the job finishes — that is handled by the
        ``slurm_tasks.poll_slurm_jobs`` periodic task. Returning the
        slurm_job_id lets the caller log it; the value is also stored in
        Redis so :meth:`cancel` can find it later.
        """
        settings = get_settings()
        storage = Path(job.storage_path)
        (storage / "logs").mkdir(parents=True, exist_ok=True)

        manifest = _load_manifest(job)
        script_text = _build_sbatch_script(job, manifest)

        script_path = storage / "logs" / "slurm.sbatch"
        script_path.write_text(script_text, encoding="utf-8")

        cmd = [settings.slurm_sbatch_bin, "--parsable", str(script_path)]

        rclient = _redis()
        _publish_line(rclient, job.id, f"$ {' '.join(cmd)}")

        try:
            result = subprocess.run(  # noqa: S603
                cmd,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"sbatch binary not found at {settings.slurm_sbatch_bin!r}"
            ) from exc

        if result.returncode != 0:
            raise RuntimeError(
                f"sbatch failed (rc={result.returncode}): "
                f"stdout={result.stdout!r} stderr={result.stderr!r}"
            )

        # ``sbatch --parsable`` prints either ``<jobid>`` or
        # ``<jobid>;<cluster>``. We want the leading numeric id.
        raw = (result.stdout or "").strip().splitlines()[-1] if result.stdout else ""
        slurm_job_id = raw.split(";", 1)[0].strip()
        if not slurm_job_id:
            raise RuntimeError(
                f"could not parse sbatch output: {result.stdout!r}"
            )

        _store_slurm_id(rclient, job.id, slurm_job_id)
        _publish_line(
            rclient, job.id, f"[slurm] submitted as slurm_job_id={slurm_job_id}"
        )
        logger.info(
            "slurm submit job=%s slurm_job_id=%s script=%s",
            job.id,
            slurm_job_id,
            script_path,
        )
        return slurm_job_id

    async def stream_logs(self, job_id: str) -> AsyncIterator[str]:
        """Yield log lines.

        We subscribe to Redis ``logs:{job_id}`` exactly like LocalRunner; in
        addition, a background task pumps any pre-existing lines from
        ``logs/stdout.log`` into the channel so a late subscriber catches up.
        """
        rclient = _redis()

        # Start a tail-pump for the log file so file output appears on pubsub.
        # We launch it lazily here so multiple stream_logs() calls don't spawn
        # duplicate pumps for the same job (best-effort dedupe via Redis SETNX).
        await asyncio.to_thread(self._ensure_log_pump, rclient, job_id)

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

    def _ensure_log_pump(self, client: redis.Redis, job_id: str) -> None:
        """Spawn a background thread that tails the log file → Redis pubsub.

        Slurm writes to ``logs/stdout.log`` directly (sbatch --output). The
        WebSocket endpoint streams via Redis pubsub, so we need to bridge.
        """
        lock_key = f"slurm:pump:{job_id}"
        # SET NX with TTL — only one pump runs at a time.
        got = client.set(lock_key, "1", nx=True, ex=24 * 3600)
        if not got:
            return

        with SessionLocal() as db:
            job = db.get(Job, job_id)
            log_path = Path(job.storage_path) / "logs" / "stdout.log" if job else None

        if log_path is None:
            client.delete(lock_key)
            return

        import threading

        def _pump() -> None:
            local_client = _redis()
            try:
                # Wait briefly for the file to exist.
                for _ in range(50):
                    if log_path.exists():
                        break
                    import time

                    time.sleep(0.2)
                if not log_path.exists():
                    return
                with log_path.open("r", encoding="utf-8", errors="replace") as fp:
                    while True:
                        line = fp.readline()
                        if line:
                            _publish_line(
                                local_client, job_id, line.rstrip("\n")
                            )
                            if line.startswith("__exit__:"):
                                break
                            continue
                        # No data yet — short sleep + check if slurm job done.
                        import time

                        time.sleep(0.5)
                        if not _get_slurm_id(local_client, job_id):
                            # slurm id forgotten → poller has finalised; exit.
                            break
            finally:
                local_client.delete(lock_key)

        t = threading.Thread(target=_pump, daemon=True, name=f"slurm-pump-{job_id}")
        t.start()

    def cancel(self, job_id: str) -> bool:
        """Issue ``scancel`` for the slurm job id associated with ``job_id``."""
        client = _redis()
        slurm_job_id = _get_slurm_id(client, job_id)
        if not slurm_job_id:
            return False
        settings = get_settings()
        try:
            result = subprocess.run(  # noqa: S603
                [settings.slurm_scancel_bin, slurm_job_id],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            logger.exception(
                "scancel binary missing at %s", settings.slurm_scancel_bin
            )
            return False
        if result.returncode != 0:
            logger.warning(
                "scancel rc=%s stderr=%r for slurm_job_id=%s",
                result.returncode,
                result.stderr,
                slurm_job_id,
            )
            return False
        _publish_line(client, job_id, f"[slurm] scancel issued for {slurm_job_id}")
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


__all__ = [
    "SlurmRunner",
    "_build_sbatch_script",
    "_sbatch_directives",
    "_get_slurm_id",
    "_store_slurm_id",
    "_forget_slurm_id",
]
