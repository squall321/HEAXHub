"""Smoke tests for the LLM inferrer.

When ``LLM_PROVIDER=stub`` (or ``LLM_API_KEY`` empty), ``call_llm`` must return
a deterministic ``LLMResult`` with a valid manifest_draft and developer change
request, without making any network calls.
"""
from __future__ import annotations

from dataclasses import asdict

import pytest

from app.config import get_settings
from app.services.manifest_llm import (
    LLMResponseInvalid,
    LLMResult,
    SYSTEM_PROMPT,
    _coerce_json,
    _validate_no_upstream_modifications,
    build_context,
    call_llm,
)
from app.services.static_analyzer import StaticFacts


@pytest.fixture(autouse=True)
def _force_stub_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("LLM_API_KEY", "")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_stub_call_llm_returns_valid_result() -> None:
    facts = StaticFacts(
        languages=["python"],
        python_version="3.11",
        python_version_source=".python-version",
        detected_env_references=["DATABASE_URL", "JWT_SECRET"],
    )
    ctx = {"static_facts": asdict(facts), "files": {}}

    result = call_llm(ctx)

    assert isinstance(result, LLMResult)
    assert result.manifest_draft.get("schema_version") == 2
    assert result.manifest_draft.get("build", {}).get("type") == "python_venv"
    assert result.manifest_draft.get("build", {}).get("python_version") == "3.11"

    required = result.developer_change_request.get("required_files") or []
    assert any(rf["path"] == ".portal/manifest.yaml" for rf in required)
    assert any(rf["path"] == ".portal/run.sh" for rf in required)
    # Hard rule: only .portal/ files allowed in required_files.
    assert all(rf["path"].startswith(".portal/") for rf in required)


def test_stub_service_inference_from_daemon_indicators() -> None:
    facts = StaticFacts(
        languages=["python"],
        python_version="3.11",
        python_version_source=".python-version",
        daemon_indicators=["uvicorn"],
    )
    ctx = {"static_facts": asdict(facts), "files": {}}

    result = call_llm(ctx)

    assert result.manifest_draft["launch"]["mode"] == "service"
    assert result.manifest_draft["app_type"] == "web_app"


def test_build_context_caps_blob_size(tmp_path) -> None:
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    (upstream / "README.md").write_text("# Hello\n", encoding="utf-8")
    facts = StaticFacts()
    ctx = build_context(tmp_path, facts)
    assert "README.md" in ctx["files"]
    assert ctx["static_facts"]["languages"] == []


def test_validate_rejects_upstream_modification() -> None:
    bad = {
        "developer_change_request": {
            "required_files": [
                {"path": "src/main.py", "kind": "modify", "content": "..."}
            ]
        }
    }
    with pytest.raises(LLMResponseInvalid):
        _validate_no_upstream_modifications(bad)


def test_validate_allows_portal_paths() -> None:
    ok = {
        "developer_change_request": {
            "required_files": [
                {"path": ".portal/manifest.yaml", "kind": "create", "content": "schema_version: 2"}
            ],
            "suggested_files": [
                {"path": "README.md", "kind": "append", "content": "more"}
            ],
        }
    }
    _validate_no_upstream_modifications(ok)


def test_coerce_json_strips_fences() -> None:
    raw = "```json\n{\"a\": 1}\n```"
    assert _coerce_json(raw) == {"a": 1}


def test_system_prompt_mentions_portal_constraint() -> None:
    assert ".portal/" in SYSTEM_PROMPT
