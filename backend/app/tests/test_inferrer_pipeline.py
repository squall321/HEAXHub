"""End-to-end test for the static_analyzer + manifest_llm pipeline
exercised through change_request.create_draft, with LLM_PROVIDER=stub.

This test uses the sample_python_cli fixture (left by SA2) as the
``workspace/upstream`` directory and a fake in-memory DB session to avoid
spinning up Postgres.
"""
from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.config import get_settings

FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "sample_python_cli"
)


# ---------------------------------------------------------------------------
# Fake DB / user that mimics enough of SQLAlchemy's Session surface area.
# ---------------------------------------------------------------------------


class _FakeDb:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.committed = 0
        self.refreshed: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.committed += 1

    def refresh(self, obj: Any) -> None:
        self.refreshed.append(obj)

    def get(self, model: Any, ident: Any) -> Any:  # noqa: ARG002
        return None


class _FakeUser:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


# ---------------------------------------------------------------------------
# Workspace prep: copy fixture under a temp workspace_root.
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("LLM_API_KEY", "")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


@pytest.fixture
def workspace_with_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Spin up a temp workspace_root with `sample_python_cli` as upstream."""
    tmp = Path(tempfile.mkdtemp(prefix="heaxhub-inferrer-"))
    app_id = "sample_python_cli"
    upstream = tmp / app_id / "upstream"
    upstream.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURE, upstream)
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp))
    get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_create_draft_pipeline_end_to_end(
    stub_provider: None,
    workspace_with_fixture: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_draft runs static_analyzer → manifest_llm → safety enforcement."""
    # Make sure we use a fresh import path so any prior monkeypatching of
    # static_analyzer/manifest_llm from other tests is reset.
    import sys
    for mod in (
        "app.services.static_analyzer",
        "app.services.manifest_llm",
        "app.services.change_request",
    ):
        sys.modules.pop(mod, None)

    # Reimport now that env is set.
    from app.services import change_request as cr_service

    db = _FakeDb()
    user = _FakeUser()

    cr = cr_service.create_draft(
        db,
        submission_id=None,
        repo_url="https://github.com/example/sample-python-cli",
        actor=user,
        app_id="sample_python_cli",
    )

    # 1. Static facts captured python language.
    assert isinstance(cr.static_facts, dict)
    assert "python" in (cr.static_facts.get("languages") or [])

    # 2. Manifest draft has the required top-level keys.
    md = cr.llm_response.get("manifest_draft") or {}
    assert md.get("schema_version") == 2
    assert "id" in md
    assert md.get("build", {}).get("type") == "python_venv"

    # 3. Stub path → awaiting_assistant status, indicating manual hand-off.
    assert cr.status == "awaiting_assistant"

    # 4. All required_files start with `.portal/` (safety check passed).
    required = (cr.pr_payload or {}).get("required_files") or []
    assert required, "stub should produce at least manifest.yaml + run.sh"
    for f in required:
        assert f["path"].startswith(".portal/")

    # 5. Rendered markdown contains the manifest filename.
    assert ".portal/manifest.yaml" in cr.markdown_body
