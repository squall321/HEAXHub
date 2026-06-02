"""End-to-end SIF-registry integration test for :class:`ApptainerRunner`.

Drives ``ApptainerRunner.start`` against a real, on-disk ``heaxhub_redis.sif``
through the registry-based ``image_ref`` path. The container is exec'd to run
a tiny shell snippet that prints a marker and queries ``redis-cli --version``;
we verify both lines landed in ``logs/stdout.log`` and that the runner
collected a success ``result.json``.

Skips cleanly when either ``apptainer`` or the redis SIF is missing so
``pytest -m "not integration"`` never blocks on infrastructure.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import textwrap
import uuid
from pathlib import Path
from typing import Iterator

import pytest

# ── skip gates ────────────────────────────────────────────────────────────────

_APPTAINER_CANDIDATES = ("/usr/bin/apptainer", "/usr/local/bin/apptainer")
_REGISTRY_NAME = "test_redis"
_SIF_PATH = Path("/home/koopark/serviceApptainers/heaxhub_redis.sif")


def _find_apptainer() -> str | None:
    for cand in _APPTAINER_CANDIDATES:
        if os.path.exists(cand):
            return cand
    from shutil import which

    return which("apptainer")


_APPT_BIN = _find_apptainer()
_DEPS_OK = _APPT_BIN is not None and _SIF_PATH.exists()


def _sif_has_bash() -> bool:
    """Probe the test SIF for ``/bin/bash`` — the runner invokes ``bash -c``."""
    if not _DEPS_OK:
        return False
    import subprocess  # local import keeps module-level imports tidy

    try:
        proc = subprocess.run(  # noqa: S603
            [_APPT_BIN, "exec", str(_SIF_PATH), "sh", "-c", "command -v bash"],
            capture_output=True,
            timeout=30,
        )
    except Exception:
        return False
    return proc.returncode == 0 and bool(proc.stdout.strip())


_SIF_HAS_BASH = _sif_has_bash()

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
    """Duck-typed Job — only attributes ApptainerRunner reads."""

    def __init__(self, storage: Path) -> None:
        self.id = f"job_test_{uuid.uuid4().hex[:8]}"
        self.app_id = "pytest_apptainer_registry_app"
        self.app_version_id = uuid.uuid4()
        self.storage_path = str(storage)
        self.params_json: dict = {}
        self.input_files: list = []


class _NoOpResourceContext:
    """No-op replacement for :class:`ResourceContext` (no license/GPU)."""

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
    """Tempdir layout matching workspace_manager.create_job_storage."""
    root = Path(tempfile.mkdtemp(prefix="heaxhub-appt-reg-"))
    for sub in ("input", "output", "logs", "work"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "params.json").write_text("{}", encoding="utf-8")
    run_sh = root / "run.sh"
    # The container has redis-cli on PATH; exercise it to prove we're inside
    # the registry-supplied SIF.
    run_sh.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            set -e
            echo "hello from registry sif"
            redis-cli --version
            cat > "$2/result.json" <<JSON
            {"status":"success","summary":{"used_registry":true},"outputs":{},"warnings":[],"errors":[]}
            JSON
            """
        ),
        encoding="utf-8",
    )
    run_sh.chmod(run_sh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture()
def registry_yaml(tmp_path: Path) -> Path:
    """Tiny one-entry registry pointing at heaxhub_redis.sif."""
    reg = tmp_path / "sif_registry.yaml"
    reg.write_text(
        f"{_REGISTRY_NAME}: {_SIF_PATH}\n",
        encoding="utf-8",
    )
    return reg


# ── tests ─────────────────────────────────────────────────────────────────────


def test_resolve_sif_registry(monkeypatch, registry_yaml: Path) -> None:
    """Unit-ish: sif_registry.resolve_sif returns the correct path."""
    from app.config import get_settings as _real_get_settings
    from app.services import sif_registry as reg_mod

    real_settings = _real_get_settings()
    monkeypatch.setattr(real_settings, "sif_registry_path", registry_yaml)
    reg_mod.reload_registry()

    resolved = reg_mod.resolve_sif({"type": "registry", "name": _REGISTRY_NAME})
    assert resolved == _SIF_PATH

    with pytest.raises(Exception):  # NotFoundError
        reg_mod.resolve_sif({"type": "registry", "name": "does_not_exist"})


@pytest.mark.skipif(
    not _SIF_HAS_BASH,
    reason=(
        f"{_SIF_PATH} lacks /bin/bash; ApptainerRunner uses 'bash -c' so this "
        "end-to-end smoke needs a SIF with bash (production solver SIFs do)."
    ),
)
def test_apptainer_runner_uses_registry_image_ref(
    monkeypatch, workspace: Path, registry_yaml: Path
) -> None:
    """End-to-end: registry name -> SIF exec -> stdout captured -> result parsed."""
    from app.config import get_settings as _real_get_settings
    from app.runners import apptainer_runner as ar_mod
    from app.runners.apptainer_runner import ApptainerRunner
    from app.services import sif_registry as reg_mod

    real_settings = _real_get_settings()
    monkeypatch.setattr(real_settings, "sif_registry_path", registry_yaml)
    if _APPT_BIN is not None and real_settings.apptainer_bin != _APPT_BIN:
        monkeypatch.setattr(real_settings, "apptainer_bin", _APPT_BIN)
    reg_mod.reload_registry()

    # Bypass the DB lookup; feed a registry-typed image_ref directly.
    def _fake_sif_path(_job) -> Path:
        return ar_mod._resolve_image_ref(
            {"type": "registry", "name": _REGISTRY_NAME}
        )

    monkeypatch.setattr(ar_mod, "_sif_path_for_job", _fake_sif_path)
    monkeypatch.setattr(ar_mod, "ResourceContext", _NoOpResourceContext)

    job = _FakeJob(workspace)
    runner = ApptainerRunner()
    exit_code = runner.start(job)  # type: ignore[arg-type]
    assert exit_code == 0, f"apptainer exec failed with code {exit_code}"

    log_text = (workspace / "logs" / "stdout.log").read_text(encoding="utf-8")
    assert "hello from registry sif" in log_text, log_text
    # redis-cli prints e.g. "redis-cli 7.x.y"
    assert "redis-cli" in log_text, log_text

    result_path = workspace / "output" / "result.json"
    assert result_path.exists(), "run.sh did not write result.json"
    parsed = json.loads(result_path.read_text(encoding="utf-8"))
    assert parsed["status"] == "success"
    assert parsed["summary"]["used_registry"] is True

    collected = runner.collect_results(job)  # type: ignore[arg-type]
    assert collected.status == "success"
    assert collected.summary.get("used_registry") is True
