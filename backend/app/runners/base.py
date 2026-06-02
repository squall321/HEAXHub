"""BaseRunner abstract interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from app.db.models.job import Job


@dataclass
class JobResult:
    """Outcome returned by a runner. ``status`` matches result.schema.json."""

    status: str  # success | warning | failed
    summary: dict[str, Any]
    outputs: dict[str, str]
    warnings: list[str]
    errors: list[str]
    exit_code: int | None = None
    raw: dict[str, Any] | None = None


class BaseRunner(ABC):
    """Abstract runner interface."""

    name: str = "base"

    # When True, ``start()`` returns immediately after submitting work to an
    # external scheduler and the job is finalized by a separate poller
    # (e.g. ``slurm_tasks.poll_slurm_jobs``). When False, ``start()`` blocks
    # until the work is finished and returns an exit code that the caller
    # uses together with ``collect_results``.
    is_async: bool = False

    @abstractmethod
    def start(self, job: Job) -> None:
        """Begin execution. Implementations may block or kick off subprocesses."""

    @abstractmethod
    async def stream_logs(self, job_id: str) -> AsyncIterator[str]:
        """Async generator yielding log lines as they become available."""
        if False:  # pragma: no cover - typing hint
            yield ""

    @abstractmethod
    def cancel(self, job_id: str) -> bool:
        """Best-effort cancel; returns True if cancellation was issued."""

    @abstractmethod
    def collect_results(self, job: Job) -> JobResult:
        """Read result.json (and friends) from the job's output dir."""
