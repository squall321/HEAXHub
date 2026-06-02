"""Map execution_target → BaseRunner instance."""
from __future__ import annotations

from app.core.errors import ValidationError
from app.db.models.app import ExecutionTarget
from app.runners.apptainer_runner import ApptainerRunner
from app.runners.base import BaseRunner
from app.runners.external_link_runner import ExternalLinkRunner
from app.runners.local_runner import LocalRunner
from app.runners.slurm_runner import SlurmRunner
from app.runners.windows_agent_client import WindowsAgentClient

_REGISTRY: dict[str, type[BaseRunner]] = {
    ExecutionTarget.LINUX_RUNNER.value: LocalRunner,
    ExecutionTarget.LOCAL_PC.value: LocalRunner,
    ExecutionTarget.SLURM.value: SlurmRunner,
    ExecutionTarget.APPTAINER.value: ApptainerRunner,
    ExecutionTarget.WINDOWS_WORKER.value: WindowsAgentClient,
    ExecutionTarget.EXTERNAL_URL.value: ExternalLinkRunner,
}


def runner_for_target(target: str) -> BaseRunner:
    cls = _REGISTRY.get(target)
    if cls is None:
        raise ValidationError(f"No runner registered for execution_target='{target}'")
    return cls()
