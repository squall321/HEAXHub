"""Unit + integration tests for :class:`SlurmRunner`.

* ``test_build_sbatch_script_*`` — pure-function checks on the generated
  sbatch body. No subprocess, no Slurm, no Redis required.
* ``test_start_submits_via_sbatch_and_parses_jobid`` — mocks
  :func:`subprocess.run` and verifies we (a) invoke the right sbatch
  command, (b) parse ``--parsable`` output, (c) record the slurm_job_id
  in Redis.
* ``test_slurm_runner_real_sbatch`` — `@pytest.mark.integration` end-to-end
  on an actual cluster (the host's ``normal`` partition). Skips cleanly
  when sbatch/squeue or the partition is missing.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest

from app.config import get_settings
from app.runners import slurm_runner as sr_mod
from app.runners.slurm_runner import (
    SlurmRunner,
    _build_sbatch_script,
    _forget_slurm_id,
    _get_slurm_id,
    _redis,
    _sbatch_directives,
)


# ---------------------------------------------------------------------------
# Skip gates for the real-Slurm test.
# ---------------------------------------------------------------------------


_SBATCH_BIN = "/usr/local/slurm/bin/sbatch"
_SQUEUE_BIN = "/usr/local/slurm/bin/squeue"
_SACCT_BIN = "/usr/local/slurm/bin/sacct"
_SCANCEL_BIN = "/usr/local/slurm/bin/scancel"


def _slurm_available() -> bool:
    return all(
        os.path.exists(p)
        for p in (_SBATCH_BIN, _SQUEUE_BIN, _SACCT_BIN, _SCANCEL_BIN)
    )


def _partition_idle(name: str) -> bool:
    if not os.path.exists("/usr/local/slurm/bin/sinfo"):
        return False
    try:
        out = subprocess.run(  # noqa: S603
            [
                "/usr/local/slurm/bin/sinfo",
                "--partition",
                name,
                "--noheader",
                "--format=%a",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0 and "up" in (out.stdout or "").lower()


# ---------------------------------------------------------------------------
# Common fixtures
# ---------------------------------------------------------------------------


class _FakeJob:
    """Minimal duck-typed Job — only the attributes SlurmRunner reads."""

    def __init__(self, storage: Path, *, app_id: str = "pytest_slurm_app") -> None:
        self.id = f"job_slurm_{uuid.uuid4().hex[:8]}"
        self.app_id = app_id
        self.app_version_id = None  # forces _load_manifest → {}
        self.storage_path = str(storage)
        self.params_json: dict = {}
        self.input_files: list = []


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Build a workspace + job storage tree matching the runner contract."""
    storage = tmp_path / "storage"
    for sub in ("input", "output", "logs"):
        (storage / sub).mkdir(parents=True, exist_ok=True)
    (storage / "params.json").write_text("{}", encoding="utf-8")

    # Workspace root with an upstream/run.sh.
    ws_root = tmp_path / "workspaces"
    app_id = "pytest_slurm_app"
    upstream = ws_root / app_id / "upstream"
    upstream.mkdir(parents=True, exist_ok=True)
    run_sh = upstream / "run.sh"
    run_sh.write_text(
        "#!/bin/bash\n"
        "set -e\n"
        'echo "hello-from-run-sh args=$@"\n'
        'mkdir -p "$2"\n'
        'cat > "$2/result.json" <<JSON\n'
        '{"status":"success","summary":{"ok":true},"outputs":{},"warnings":[],"errors":[]}\n'
        "JSON\n",
        encoding="utf-8",
    )
    run_sh.chmod(0o755)

    # Re-point settings at our tmp workspace_root.
    settings = get_settings()
    original_root = settings.workspace_root
    monkeypatch.setattr(settings, "workspace_root", ws_root)
    try:
        yield storage
    finally:
        monkeypatch.setattr(settings, "workspace_root", original_root)
        shutil.rmtree(tmp_path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


def test_sbatch_directives_default(workspace: Path) -> None:
    job = _FakeJob(workspace)
    directives = _sbatch_directives(job, manifest={})

    joined = "\n".join(directives)
    assert "#SBATCH --partition=normal" in joined
    assert "#SBATCH --time=01:00:00" in joined  # 60-minute default
    assert f"#SBATCH --job-name=heaxhub-{job.id}" in joined
    assert "--gres=gpu" not in joined  # no gpu requested


def test_sbatch_directives_cpu_mem_gpu_partition_time(workspace: Path) -> None:
    job = _FakeJob(workspace)
    manifest = {
        "resources": {
            "cpu": 8,
            "memory_gb": 16,
            "gpu": {"count": 2},
            "partition": "gpu",
            "time_limit_minutes": 125,  # 2h 5m
        }
    }
    joined = "\n".join(_sbatch_directives(job, manifest=manifest))
    assert "#SBATCH --partition=gpu" in joined
    assert "#SBATCH --cpus-per-task=8" in joined
    assert "#SBATCH --mem=16384M" in joined
    assert "#SBATCH --gres=gpu:2" in joined
    assert "#SBATCH --time=02:05:00" in joined


def test_build_sbatch_script_contains_run_sh_invocation(workspace: Path) -> None:
    job = _FakeJob(workspace)
    script = _build_sbatch_script(job, manifest={})

    # Header + bash shebang
    assert script.startswith("#!/bin/bash\n")
    # Tees stdout/err to the same log file LocalRunner uses.
    assert f"--output={workspace / 'logs' / 'stdout.log'}" in script
    assert f"--error={workspace / 'logs' / 'stdout.log'}" in script
    # JOB_* env vars (LocalRunner contract).
    assert "export JOB_INPUT=" in script
    assert "export JOB_OUTPUT=" in script
    assert "export JOB_PARAMS=" in script
    # The run.sh is invoked with input output params.json args.
    assert "run.sh" in script
    assert "input" in script and "output" in script and "params.json" in script
    # Exit marker that stream_logs uses to detect job end.
    assert '"__exit__:${rc}"' in script


def test_start_submits_via_sbatch_and_parses_jobid(workspace: Path) -> None:
    """Mock subprocess + redis. Verify cmd + slurm_job_id capture."""
    job = _FakeJob(workspace)

    fake_redis = MagicMock()
    sbatch_proc = MagicMock()
    sbatch_proc.returncode = 0
    sbatch_proc.stdout = "12345;normal\n"
    sbatch_proc.stderr = ""

    with patch.object(sr_mod, "_redis", return_value=fake_redis), patch.object(
        sr_mod.subprocess, "run", return_value=sbatch_proc
    ) as mock_run:
        runner = sr_mod.SlurmRunner()
        slurm_id = runner.start(job)  # type: ignore[arg-type]

    assert slurm_id == "12345"

    # The sbatch script must have been written to disk.
    script_path = workspace / "logs" / "slurm.sbatch"
    assert script_path.exists()
    body = script_path.read_text(encoding="utf-8")
    assert "#SBATCH --partition=normal" in body
    assert "run.sh" in body

    # subprocess.run called with --parsable and script path.
    args, kwargs = mock_run.call_args
    cmd = args[0]
    assert cmd[0].endswith("sbatch")
    assert "--parsable" in cmd
    assert cmd[-1] == str(script_path)

    # Redis SET was called with slurm:job:<job.id> = "12345".
    keys_written = [c.args[0] for c in fake_redis.set.call_args_list]
    assert any(k == f"slurm:job:{job.id}" for k in keys_written)
    # And the job was added to slurm:active_jobs set.
    fake_redis.sadd.assert_any_call("slurm:active_jobs", job.id)


def test_start_raises_on_sbatch_failure(workspace: Path) -> None:
    job = _FakeJob(workspace)
    fake_redis = MagicMock()
    proc = MagicMock()
    proc.returncode = 1
    proc.stdout = ""
    proc.stderr = "sbatch: error: invalid partition"

    with patch.object(sr_mod, "_redis", return_value=fake_redis), patch.object(
        sr_mod.subprocess, "run", return_value=proc
    ):
        runner = sr_mod.SlurmRunner()
        with pytest.raises(RuntimeError, match="sbatch failed"):
            runner.start(job)  # type: ignore[arg-type]


def test_cancel_calls_scancel_with_stored_id(workspace: Path) -> None:
    job = _FakeJob(workspace)
    fake_redis = MagicMock()
    fake_redis.get.return_value = b"98765"
    cancel_proc = MagicMock()
    cancel_proc.returncode = 0

    with patch.object(sr_mod, "_redis", return_value=fake_redis), patch.object(
        sr_mod.subprocess, "run", return_value=cancel_proc
    ) as mock_run:
        runner = sr_mod.SlurmRunner()
        assert runner.cancel(job.id) is True

    args, _ = mock_run.call_args
    cmd = args[0]
    assert cmd[0].endswith("scancel")
    assert cmd[1] == "98765"


def test_collect_results_parses_result_json(workspace: Path) -> None:
    job = _FakeJob(workspace)
    (workspace / "output").mkdir(parents=True, exist_ok=True)
    (workspace / "output" / "result.json").write_text(
        json.dumps(
            {
                "status": "success",
                "summary": {"n": 42},
                "outputs": {"f": "out/log.txt"},
                "warnings": [],
                "errors": [],
            }
        ),
        encoding="utf-8",
    )

    result = SlurmRunner().collect_results(job)  # type: ignore[arg-type]
    assert result.status == "success"
    assert result.summary["n"] == 42


def test_collect_results_missing_file_returns_failed(workspace: Path) -> None:
    job = _FakeJob(workspace)
    result = SlurmRunner().collect_results(job)  # type: ignore[arg-type]
    assert result.status == "failed"
    assert any("result.json missing" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Integration test — exercises real sbatch/squeue/sacct.
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(
    not _slurm_available(),
    reason="Slurm bins missing under /usr/local/slurm/bin",
)
@pytest.mark.skipif(
    not _partition_idle("normal"),
    reason="Slurm partition 'normal' not available",
)
def test_slurm_runner_real_sbatch(workspace: Path) -> None:
    """Submit a tiny sleep job and verify squeue sees it, then scancel it."""
    # Replace the fixture-provided run.sh with an even simpler sleep stub
    # (we only need Slurm to accept it; collect_results is exercised separately).
    run_sh = (
        get_settings().workspace_root
        / "pytest_slurm_app"
        / "upstream"
        / "run.sh"
    )
    assert run_sh.parent.exists(), f"workspace fixture did not create {run_sh.parent}"
    run_sh.write_text("#!/bin/bash\nsleep 3\necho done\n", encoding="utf-8")
    run_sh.chmod(0o755)

    job = _FakeJob(workspace)
    runner = SlurmRunner()

    slurm_job_id = runner.start(job)  # type: ignore[arg-type]
    assert slurm_job_id.isdigit(), slurm_job_id

    client = _redis()
    try:
        assert _get_slurm_id(client, job.id) == slurm_job_id

        # squeue should see it (in PENDING or RUNNING) within a few seconds.
        deadline = time.time() + 20
        seen = False
        while time.time() < deadline:
            out = subprocess.run(  # noqa: S603
                [_SQUEUE_BIN, "--job", slurm_job_id, "--noheader", "--format=%T"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if out.returncode == 0 and out.stdout.strip():
                seen = True
                break
            time.sleep(1)
        assert seen, "squeue never reported the submitted slurm job"

        # Cancel and verify scancel succeeded.
        assert runner.cancel(job.id) is True
    finally:
        # Tidy: forget the redis key + try a best-effort scancel just in case.
        try:
            subprocess.run(  # noqa: S603
                [_SCANCEL_BIN, slurm_job_id],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except Exception:
            pass
        _forget_slurm_id(client, job.id)
