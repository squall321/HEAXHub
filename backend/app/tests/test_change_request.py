"""Smoke tests for the change_request service.

These tests stub out the upstream static_analyzer / manifest_llm pipeline by
monkeypatching the already-imported names inside ``app.services.change_request``.
We can't rely on swapping ``sys.modules`` alone because change_request.py binds
``_analyze`` / ``build_context`` / ``call_llm`` at module top level — once any
earlier test (e.g. test_assistant_packet) imports change_request transitively,
later sys.modules swaps don't reach the names already bound in its namespace.

Covers:
1. create_draft produces a ChangeRequest with non-empty markdown.
2. render_markdown contains the '.portal/manifest.yaml' substring.
3. A forged LLM response with required_files[].path = "src/leak.py" is rejected.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Test doubles for the static_analyzer + manifest_llm pipeline. We patch the
# already-bound names inside change_request so the swap takes effect regardless
# of test ordering (i.e. regardless of whether change_request was imported
# earlier by another test module).
# ---------------------------------------------------------------------------


@dataclass
class _FakeFacts:
    languages: list[str]
    python_version: str | None
    commit_sha: str | None


def _install_fake_sa2(
    monkeypatch: pytest.MonkeyPatch, llm_payload: dict[str, Any]
) -> None:
    from app.services import change_request as cr_service

    def _analyze(workspace: Path) -> _FakeFacts:  # noqa: ARG001
        return _FakeFacts(
            languages=["python"],
            python_version="3.11",
            commit_sha="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        )

    def _build_context(workspace: Path, facts: _FakeFacts) -> dict[str, Any]:  # noqa: ARG001
        return {"files": {}, "static_facts": facts.__dict__}

    def _call_llm(context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
        return llm_payload

    # Patch the names bound inside change_request directly — this is the only
    # way to override functions imported via `from ... import name`.
    monkeypatch.setattr(cr_service, "_analyze", _analyze, raising=True)
    monkeypatch.setattr(cr_service, "build_context", _build_context, raising=True)
    monkeypatch.setattr(cr_service, "call_llm", _call_llm, raising=True)


_GOOD_LLM_PAYLOAD = {
    "manifest_draft": {
        "schema_version": 2,
        "id": "sample_app",
        "name": "Sample App",
        "version": "0.2.0",
        "owner": "cae-automation",
        "status": "draft",
        "app_type": "cli_tool",
        "execution_target": "linux_runner",
        "launch": {"mode": "job_runner", "command": "./.portal/run.sh"},
    },
    "confidence": {"launch.command": 0.95},
    "open_questions": [
        {
            "field": "resources.timeout_seconds",
            "question": "최대 실행 시간을 알려주세요.",
            "candidates": [600, 1800, 3600],
            "context": "README에 명시되지 않음",
        }
    ],
    "developer_change_request": {
        "summary": "포탈 등록을 위한 .portal/ 디렉터리 추가",
        "required_files": [
            {
                "path": ".portal/manifest.yaml",
                "kind": "create",
                "content": "schema_version: 2\nid: sample_app\n",
            },
            {
                "path": ".portal/run.sh",
                "kind": "create",
                "mode": "0755",
                "content": "#!/usr/bin/env bash\nset -euo pipefail\npython -m sample_app\n",
            },
        ],
        "suggested_files": [],
        "rationale": "포탈은 .portal/run.sh 진입점을 호출합니다.",
    },
}


_BAD_LLM_PAYLOAD = {
    "manifest_draft": {"id": "leaky", "version": "0.0.1"},
    "confidence": {},
    "open_questions": [],
    "developer_change_request": {
        "summary": "...",
        "required_files": [
            {"path": "src/leak.py", "kind": "create", "content": "print('hello')\n"},
        ],
        "suggested_files": [],
        "rationale": "...",
    },
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_render_markdown_contains_portal_manifest_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_sa2(monkeypatch, _GOOD_LLM_PAYLOAD)

    from app.db.models.change_request import ChangeRequest
    from app.services.change_request import render_markdown

    cr = ChangeRequest(
        id=uuid.uuid4(),
        submission_id=None,
        app_id="sample_app",
        repo_url="https://github.com/squall321/MXCAEGroupAutomationSample",
        commit_sha=None,
        static_facts={},
        llm_response=_GOOD_LLM_PAYLOAD,
        operator_overrides={},
        final_manifest=_GOOD_LLM_PAYLOAD["manifest_draft"],
        markdown_body="",
        pr_payload=None,
        status="draft",
    )
    body = render_markdown(cr)
    assert ".portal/manifest.yaml" in body
    assert ".portal/run.sh" in body
    assert "sample_app" in body


def test_create_draft_produces_nonempty_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sa2(monkeypatch, _GOOD_LLM_PAYLOAD)

    from app.services import change_request as cr_service

    captured: dict[str, Any] = {}

    def _fake_commit() -> None:
        return None

    def _fake_refresh(obj: Any) -> None:
        return None

    class _FakeDb:
        def add(self, obj: Any) -> None:
            captured["obj"] = obj

        def commit(self) -> None:
            _fake_commit()

        def refresh(self, obj: Any) -> None:
            _fake_refresh(obj)

        def get(self, model: Any, ident: Any) -> Any:  # noqa: ARG002
            return None

    class _FakeUser:
        id = uuid.uuid4()

    cr = cr_service.create_draft(
        _FakeDb(),
        submission_id=None,
        repo_url="https://github.com/squall321/MXCAEGroupAutomationSample",
        actor=_FakeUser(),
        app_id=None,
    )

    assert cr is captured["obj"]
    assert cr.markdown_body
    assert ".portal/manifest.yaml" in cr.markdown_body
    assert cr.status == "draft"
    # pr_payload mirrors the LLM's required_files
    assert cr.pr_payload is not None
    paths = [f["path"] for f in cr.pr_payload["required_files"]]
    assert ".portal/manifest.yaml" in paths
    assert ".portal/run.sh" in paths


def test_create_draft_rejects_unsafe_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sa2(monkeypatch, _BAD_LLM_PAYLOAD)

    from app.core.errors import ValidationError
    from app.services import change_request as cr_service

    class _FakeDb:
        def add(self, obj: Any) -> None:
            raise AssertionError("Should not reach .add() for unsafe payload")

        def commit(self) -> None:  # pragma: no cover
            raise AssertionError("Should not reach .commit() for unsafe payload")

        def refresh(self, obj: Any) -> None:  # pragma: no cover
            raise AssertionError("Should not reach .refresh() for unsafe payload")

        def get(self, model: Any, ident: Any) -> Any:  # noqa: ARG002
            return None

    class _FakeUser:
        id = uuid.uuid4()

    with pytest.raises(ValidationError):
        cr_service.create_draft(
            _FakeDb(),
            submission_id=None,
            repo_url="https://github.com/squall321/MXCAEGroupAutomationSample",
            actor=_FakeUser(),
            app_id=None,
        )


def test_parse_github_url_variants() -> None:
    from app.services.github_integration import parse_github_url

    assert parse_github_url("https://github.com/foo/bar") == ("foo", "bar")
    assert parse_github_url("https://github.com/foo/bar.git") == ("foo", "bar")
    assert parse_github_url("git@github.com:foo/bar.git") == ("foo", "bar")
