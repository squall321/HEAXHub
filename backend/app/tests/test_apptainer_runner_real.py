"""Real-SIF integration test for :class:`ApptainerRunner`.

Skips cleanly if either ``apptainer`` or the pre-built test SIF is missing, so
the standard ``pytest -m "not integration"`` invocation in CI never blocks on
infrastructure.

Strategy:
    1. Build a synthetic ``Job`` row (in-memory; no DB writes) pointing at a
       tempdir-backed storage path that follows the LocalRunner contract:
       ``input/``, ``output/``, ``logs/``, ``params.json``.
    2. Drop a ``run.sh`` into the storage root that emits a marker line and
       writes a minimal ``result.json``.
    3. Monkeypatch the SIF resolver so the runner doesn't need a live DB +
       AppVersion row.
    4. Monkeypatch ``ResourceContext`` to a no-op (we are not exercising the
       license/GPU code path here).
    5. Run ``ApptainerRunner.start(job)`` synchronously and assert.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import uuid
from pathlib import Path
from typing import Iterator

import pytest

# ── skip gates ────────────────────────────────────────────────────────────────

_APPTAINER_CANDIDATES = ("/usr/bin/apptainer", "/usr/local/bin/apptainer")
_SIF_PATH = Path("/home/koopark/serviceApptainers/heaxhub_redis.sif")


def _find_apptainer() -> str | None:
    for cand in _APPTAINER_CANDIDATES:
        if os.path.exists(cand):
            return cand
    from shutil import which

    return which("apptainer")


_APPT_BIN = _find_apptainer()
_DEPS_OK = _APPT_BIN is not None and _SIF_PATH.exists()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _DEPS_OK,
        reason=(
            f"requires apptainer + {_SIF_PATH} "
            f"(apptainer found: {_APPT_BIN!r}, sif exists: {_SIF_PATH.exists()})"
        ),
    ),
]


# ── helpers ───────────────────────────────────────────────────────────────────


class _FakeJob:
    """Minimal duck-typed Job — only the attributes :class:`ApptainerRunner` reads."""

    def __init__(self, storage: Path) -> None:
        self.id = f"job_test_{uuid.uuid4().hex[:8]}"
        self.app_id = "pytest_apptainer_app"
        self.app_version_id = uuid.uuid4()
        self.storage_path = str(storage)
        self.params_json: dict = {}
        self.input_files: list = []


class _NoOpResourceContext:
    """Replacement for :class:`ResourceContext` — does nothing, holds no GPUs."""

    def __init__(self, *, job) -> None:  # type: ignore[no-untyped-def]
        self.job = job
        self.gpu_devices: list = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def env(self, base):
        return dict(base)

    @property
    def gpu_count(self) -> int:
        return 0


@pytest.fixture()
def workspace() -> Iterator[Path]:
    """Tempdir layout matching ``workspace_manager.create_job_storage``."""
    root = Path(tempfile.mkdtemp(prefix="heaxhub-appt-it-"))
    for sub in ("input", "output", "logs", "work"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "params.json").write_text("{}", encoding="utf-8")
    # Drop a synthetic run.sh that the runner will execute inside the SIF.
    run_sh = root / "run.sh"
    run_sh.write_text(
        "#!/bin/sh\n"
        "set -e\n"
        'echo "hello from sif"\n'
        'cat > "$2/result.json" <<JSON\n'
        '{"status":"success","summary":{"ok":true},"outputs":{},"warnings":[],"errors":[]}\n'
        "JSON\n",
        encoding="utf-8",
    )
    run_sh.chmod(run_sh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── tests ─────────────────────────────────────────────────────────────────────


def test_apptainer_runner_executes_real_sif(monkeypatch, workspace: Path) -> None:
    """End-to-end: start → SIF exec → stdout captured → result.json parsed."""
    # Import inside the test so the module-level skip can fire first.
    from app.runners import apptainer_runner as ar_mod
    from app.runners.apptainer_runner import ApptainerRunner

    job = _FakeJob(workspace)

    # 1) Pin SIF resolution so we don't need a DB + AppVersion row.
    monkeypatch.setattr(ar_mod, "_sif_path_for_job", lambda j: _SIF_PATH)
    # 2) Stub out ResourceContext (no license/GPU acquisition needed for IT).
    monkeypatch.setattr(ar_mod, "ResourceContext", _NoOpResourceContext)
    # 3) Force the runner to use the discovered apptainer bin (settings default
    #    points at /usr/bin/apptainer; on this host it's /usr/local/bin).
    from app.config import get_settings as _real_get_settings

    real_settings = _real_get_settings()
    if _APPT_BIN is not None and real_settings.apptainer_bin != _APPT_BIN:
        monkeypatch.setattr(real_settings, "apptainer_bin", _APPT_BIN)

    runner = ApptainerRunner()
    exit_code = runner.start(job)  # type: ignore[arg-type]

    assert exit_code == 0, f"apptainer exec failed with code {exit_code}"

    # logs/stdout.log captured the marker line
    log_text = (workspace / "logs" / "stdout.log").read_text(encoding="utf-8")
    assert "hello from sif" in log_text, log_text

    # result.json materialised
    result_path = workspace / "output" / "result.json"
    assert result_path.exists(), "run.sh failed to write result.json"
    parsed = json.loads(result_path.read_text(encoding="utf-8"))
    assert parsed["status"] == "success"

    # collect_results parses it
    collected = runner.collect_results(job)  # type: ignore[arg-type]
    assert collected.status == "success"
    assert collected.summary.get("ok") is True
