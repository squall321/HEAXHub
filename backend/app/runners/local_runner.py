"""LocalRunner — runs an app on the same host as the worker.

Subprocess is launched synchronously (Celery worker is itself a process). Logs
are tee-d to a file and published to Redis pub/sub channel ``logs:{job_id}`` so
the WebSocket endpoint can stream them to the browser.
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
from app.core.logger import get_logger
from app.db.models.job import Job
from app.runners.base import BaseRunner, JobResult
from app.runners.resource_hooks import ResourceContext
from app.runners.resource_limits import build_preexec

logger = get_logger(__name__)

# Track running processes per job_id so cancel() can kill them.
_RUNNING_PROCESSES: dict[str, subprocess.Popen[bytes]] = {}
_LOCK = threading.Lock()


def _redis() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url)


def _publish_line(client: redis.Redis, job_id: str, line: str) -> None:
    try:
        client.publish(f"logs:{job_id}", line)
    except Exception:
        logger.exception("redis publish failed")


class LocalRunner(BaseRunner):
    name = "local"

    def start(self, job: Job) -> int:
        """Run the app synchronously. Returns process exit code.

        Manifest-driven hooks (env_required, license, gpu) are wrapped in a
        :class:`ResourceContext` so reservations get released even if the
        subprocess crashes.

        SIF dispatch: if var/sifs/<slug>.sif exists for this app, the job is
        executed inside that SIF via apptainer run (the runscript baked into
        the .def template carries the entrypoint). Falls back to the legacy
        bash overlay/run.sh path otherwise.
        """
        from app.services import apt_runner, managed_workspaces  # noqa: PLC0415

        storage = Path(job.storage_path)
        workspace = Path(self._workspace_for_app(job.app_id))

        # SIF lookup: the App.id is the canonical (heax_demo_*) form; the SIF
        # is keyed by the integrations/ dirname (heax-demo-*). Try both.
        sif_path: Path | None = None
        for candidate in (
            managed_workspaces.sif_path_for(job.app_id),
            managed_workspaces.sif_path_for(job.app_id.replace("_", "-")),
        ):
            if candidate.is_file():
                sif_path = candidate
                break

        run_script: Path | None = None
        if sif_path is None:
            run_script = workspace / "overlay" / ".portal" / "run.sh"
            if not run_script.exists():
                run_script = workspace / "upstream" / "run.sh"
            if not run_script.exists():
                raise FileNotFoundError(
                    f"No run.sh found at overlay/.portal/run.sh or upstream/run.sh for app {job.app_id}"
                )

        with ResourceContext(job=job) as ctx:
            base_env = os.environ.copy()
            base_env["JOB_INPUT"] = str(storage / "input")
            base_env["JOB_OUTPUT"] = str(storage / "output")
            base_env["JOB_PARAMS"] = str(storage / "params.json")
            base_env["JOB_ID"] = job.id
            base_env["APP_ID"] = job.app_id
            venv_bin = workspace / "venv" / "bin"
            if venv_bin.exists():
                base_env["PATH"] = f"{venv_bin}:{base_env.get('PATH', '')}"
                base_env["VIRTUAL_ENV"] = str(workspace / "venv")
            base_env["PYTHONPATH"] = str(workspace / "upstream")

            env = ctx.env(base_env)

            if sif_path is not None:
                # apptainer run <sif> <argv...> — the .def %runscript handles
                # the actual entrypoint (e.g. python -m heax_demo_cli.cli,
                # ./bin/heax-demo-cpp, Rscript run.R). We bind the host job
                # storage in as /job so the script can read /job/input and
                # write /job/output regardless of the SIF's internal layout.
                bind_arg = f"{storage}:/job"
                cmd = [
                    apt_runner.local_apptainer_path(),
                    "run",
                    "--bind", bind_arg,
                    "--pwd", "/app",
                    str(sif_path),
                    "/job/input", "/job/output", "/job/params.json",
                ]
            else:
                cmd = [
                    "bash",
                    str(run_script),
                    str(storage / "input"),
                    str(storage / "output"),
                    str(storage / "params.json"),
                ]

            log_file = storage / "logs" / "stdout.log"
            log_file.parent.mkdir(parents=True, exist_ok=True)

            rclient = _redis()
            _publish_line(rclient, job.id, f"$ {' '.join(cmd)}")

            # Pull resource limits from the manifest (best-effort; Linux only).
            limits = ctx.manifest.get("resources") if isinstance(ctx.manifest, dict) else None

            run_cwd = str(storage) if sif_path is not None else str(workspace / "upstream")

            # apptainer is a Go binary and creates many threads; manifest-derived
            # ulimits (NPROC/AS in particular) trip pthread_create even before
            # the script inside the SIF starts. Skip preexec rlimits in SIF mode
            # — the container's own cgroup/ns isolation is the right place to
            # constrain runtime resources, not host rlimits on the launcher.
            preexec = None if sif_path is not None else build_preexec(limits)

            with log_file.open("ab") as log_fp:
                proc = subprocess.Popen(  # noqa: S603
                    cmd,
                    env=env,
                    cwd=run_cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=0,
                    preexec_fn=preexec,
                )
                with _LOCK:
                    _RUNNING_PROCESSES[job.id] = proc

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
                        _RUNNING_PROCESSES.pop(job.id, None)

            _publish_line(rclient, job.id, f"__exit__:{proc.returncode}")
            return proc.returncode or 0

    async def stream_logs(self, job_id: str) -> AsyncIterator[str]:
        """Subscribe to redis pubsub and yield lines as they arrive."""
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
            proc = _RUNNING_PROCESSES.get(job_id)
        if proc is None:
            return False
        try:
            # We set `os.setsid()` in preexec_fn → child is its own group leader.
            # Killing the group catches descendants spawned by run.sh.
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                proc.send_signal(signal.SIGTERM)
            return True
        except Exception:
            logger.exception("Failed to signal process for job=%s", job_id)
            return False

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

    # --- helpers -------------------------------------------------------------

    @staticmethod
    def _workspace_for_app(app_id: str) -> str:
        return str(get_settings().workspace_root / app_id)
