"""WindowsAgentClient — dispatches jobs to Windows Worker Agents over Redis.

Flow:
    Hub side                            Agent side
    ────────                            ──────────
    start(job)
      ├─ pick agent (agent_registry)    poll /agents/poll
      └─ LPUSH agent:{id}:queue {...} → BRPOP / GET → run EXE → POST logs/files/status
                                                                    ↓
                                              hub Redis pub/sub logs:{job_id}
                                                                    ↓
                                              WS endpoint streams to browser

`collect_results` simply verifies the agent already uploaded the expected files.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import redis

from app.config import get_settings
from app.core.logger import get_logger
from app.db.models.app import App
from app.db.models.job import Job
from app.db.session import SessionLocal
from app.runners.base import BaseRunner, JobResult
from app.services import agent_registry

logger = get_logger(__name__)


def _redis() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url)


def _agent_queue_key(agent_id: Any) -> str:
    return f"agent:{agent_id}:queue"


def _agent_control_key(agent_id: Any) -> str:
    return f"agent:{agent_id}:control"


def _job_assignment_key(job_id: str) -> str:
    return f"job:{job_id}:agent"


class WindowsAgentClient(BaseRunner):
    name = "windows_worker"

    def start(self, job: Job) -> None:
        """Pick an agent in the manifest's pool, enqueue the job, record assignment.

        The manifest's pool name is read from the job's params (`__agent_pool`) or
        falls back to the job's app extra config. The actual EXE/command is
        resolved by the agent from the workspace bundle it has downloaded.
        """
        pool = self._resolve_pool(job)
        rclient = _redis()

        with SessionLocal() as db:
            agent = agent_registry.dispatch_job_to_pool(db, job=job, pool=pool)
            if agent is None:
                raise RuntimeError(
                    f"No available Windows agent in pool '{pool}' for job {job.id}"
                )

            payload = {
                "job_id": job.id,
                "app_id": job.app_id,
                "params": job.params_json or {},
                "storage_path": job.storage_path,
                "hub_url": get_settings().app_base_url,
            }
            rclient.lpush(_agent_queue_key(agent.id), json.dumps(payload))
            # Track the assignment so the API layer can find which agent owns this job.
            rclient.set(_job_assignment_key(job.id), str(agent.id))
            logger.info(
                "windows job %s assigned to agent %s (pool=%s)", job.id, agent.id, pool
            )

    async def stream_logs(self, job_id: str) -> AsyncIterator[str]:
        """Subscribe to redis pubsub `logs:{job_id}` — the agent POSTs lines that
        the hub republishes via :func:`publish_agent_log`.
        """
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
        """Push a cancel message to the agent's control queue."""
        rclient = _redis()
        agent_id = rclient.get(_job_assignment_key(job_id))
        if agent_id is None:
            return False
        if isinstance(agent_id, bytes):
            agent_id = agent_id.decode("utf-8")
        try:
            rclient.lpush(
                _agent_control_key(agent_id),
                json.dumps({"action": "cancel", "job_id": job_id}),
            )
            return True
        except Exception:
            logger.exception("failed to push cancel for job=%s", job_id)
            return False

    def collect_results(self, job: Job) -> JobResult:
        """Agent already uploaded files via POST; we only verify presence + parse result.json."""
        storage = Path(job.storage_path)
        result_path = storage / "output" / "result.json"
        if not result_path.exists():
            return JobResult(
                status="failed",
                summary={},
                outputs={},
                warnings=[],
                errors=["result.json missing — agent did not upload outputs"],
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

    # ── helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_pool(job: Job) -> str:
        """Best-effort: read pool from job.params_json `__agent_pool`, else
        look at the app's `extra.launch.agent_pool`, else fall back to 'default'.
        """
        params = job.params_json or {}
        if isinstance(params, dict):
            pool = params.get("__agent_pool")
            if isinstance(pool, str) and pool:
                return pool

        # Best-effort lookup via the App row.
        try:
            with SessionLocal() as db:
                app = db.get(App, job.app_id)
                if app and isinstance(app.extra, dict):
                    launch = app.extra.get("launch") or {}
                    pool = launch.get("agent_pool")
                    if isinstance(pool, str) and pool:
                        return pool
        except Exception:
            logger.exception("failed to resolve agent_pool from app config")
        return "default"


# ── helper used by the agents API layer ────────────────────────────────────────


def publish_agent_log(job_id: str, line: str) -> None:
    """Publish a single log line to `logs:{job_id}` so WS subscribers see it."""
    try:
        _redis().publish(f"logs:{job_id}", line)
    except Exception:
        logger.exception("publish_agent_log failed")


def publish_exit(job_id: str, exit_code: int) -> None:
    """Publish the synthetic `__exit__:N` marker that terminates streams."""
    try:
        _redis().publish(f"logs:{job_id}", f"__exit__:{exit_code}")
    except Exception:
        logger.exception("publish_exit failed")


def pop_next_job_for_agent(agent_id: Any) -> dict[str, Any] | None:
    """Atomically pop the next pending job payload for an agent. Returns None if empty."""
    raw = _redis().rpop(_agent_queue_key(agent_id))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return json.loads(raw)
    except Exception:
        logger.exception("failed to decode agent queue payload")
        return None


def pop_control_message(agent_id: Any) -> dict[str, Any] | None:
    """Pop one control-channel message (e.g. cancel) for an agent."""
    raw = _redis().rpop(_agent_control_key(agent_id))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return json.loads(raw)
    except Exception:
        return None
