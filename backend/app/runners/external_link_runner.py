"""ExternalLinkRunner — apps that are just outbound URLs. Marks success immediately."""
from __future__ import annotations

from collections.abc import AsyncIterator

from app.db.models.job import Job
from app.runners.base import BaseRunner, JobResult


class ExternalLinkRunner(BaseRunner):
    name = "external_url"

    def start(self, job: Job) -> int:
        return 0

    async def stream_logs(self, job_id: str) -> AsyncIterator[str]:
        yield "External link app: no execution performed."

    def cancel(self, job_id: str) -> bool:
        return True

    def collect_results(self, job: Job) -> JobResult:
        return JobResult(
            status="success",
            summary={"note": "external_link app — no payload"},
            outputs={},
            warnings=[],
            errors=[],
            exit_code=0,
        )
