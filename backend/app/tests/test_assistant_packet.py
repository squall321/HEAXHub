"""Tests for the Claude-in-the-loop assistant packet service.

Covers:
- ``build_packet`` produces a valid zip with the expected entries and updates
  the ChangeRequest status to ``awaiting_assistant``.
- ``parse_assistant_response`` accepts JSON envelopes.
- ``parse_assistant_response`` accepts markdown with embedded YAML+bash blocks.
- ``parse_assistant_response`` rejects unsafe paths (upstream/src/...).
"""
from __future__ import annotations

import io
import json
import uuid
import zipfile
from pathlib import Path
from typing import Any

import pytest

from app.core.errors import ValidationError as _ValErr
from app.db.models.change_request import ChangeRequest
from app.services import assistant_packet as ap
from app.services.assistant_packet import parse_assistant_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_change_request(*, upstream: Path | None) -> Any:
    """Construct an in-memory ChangeRequest stub.

    We avoid hitting the real DB; the service only reads scalar attributes.
    The ``app_id`` is set when an upstream path is provided so that
    workspace_manager.app_workspace_path() can be patched to find it.
    """
    cr = ChangeRequest(
        id=uuid.uuid4(),
        submission_id=None,
        app_id="sample_app" if upstream is not None else None,
        repo_url="https://github.com/example/sample",
        commit_sha="cafebabecafebabecafebabecafebabecafebabe",
        static_facts={
            "languages": ["python"],
            "python_version": "3.11",
            "entry_files": ["src/main.py"],
        },
        llm_response={
            "manifest_draft": {},
            "confidence": {},
            "open_questions": [],
            "developer_change_request": {
                "summary": "x",
                "required_files": [],
            },
        },
        operator_overrides={},
        final_manifest={},
        markdown_body="",
        pr_payload=None,
        status="draft",
    )
    return cr


class _FakeDb:
    """Minimal in-memory replacement for sqlalchemy Session."""

    def __init__(self, cr: Any) -> None:
        self._cr = cr
        self.audit_entries: list[Any] = []

    def get(self, model: Any, ident: Any) -> Any:  # noqa: ARG002
        if model.__name__ == "ChangeRequest" and ident == self._cr.id:
            return self._cr
        return None

    def add(self, obj: Any) -> None:
        self.audit_entries.append(obj)

    def commit(self) -> None:
        return None

    def refresh(self, obj: Any) -> None:  # noqa: ARG002
        return None


# ---------------------------------------------------------------------------
# build_packet
# ---------------------------------------------------------------------------


def test_build_packet_produces_valid_zip_and_updates_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange — create a fake upstream workspace with a couple of files.
    upstream = tmp_path / "ws" / "sample_app" / "upstream"
    upstream.mkdir(parents=True)
    (upstream / "README.md").write_text("# Sample\n\nrun: `python -m sample`\n")
    (upstream / "requirements.txt").write_text("requests==2.31.0\n")
    src = upstream / "src"
    src.mkdir()
    (src / "main.py").write_text("print('hi')\n")

    # Monkey-patch workspace lookup so the service finds our tmp upstream.
    monkeypatch.setattr(
        ap,
        "app_workspace_path",
        lambda app_id: tmp_path / "ws" / app_id,  # noqa: ARG005
    )

    cr = _make_change_request(upstream=upstream)
    db = _FakeDb(cr)

    # Act — force zip format so we always exercise the zip path.
    packet = ap.build_packet(db, cr.id, force_format="zip")

    # Assert — zip is valid and contains the expected entries.
    assert packet.packet_sha256
    assert packet.zip_bytes
    with zipfile.ZipFile(io.BytesIO(packet.zip_bytes)) as zf:
        names = set(zf.namelist())
        assert "instructions.md" in names
        assert "static_facts.json" in names
        assert "change_request.json" in names
        # File contents preserved.
        assert "files/README.md" in names
        assert "files/requirements.txt" in names
        # Entry file picked up.
        assert "files/src/main.py" in names
        # static_facts.json round-trips.
        facts = json.loads(zf.read("static_facts.json").decode())
        assert facts["languages"] == ["python"]
        # change_request.json contains the CR id.
        ident = json.loads(zf.read("change_request.json").decode())
        assert ident["id"] == str(cr.id)
        # instructions.md mentions Claude.
        instructions = zf.read("instructions.md").decode()
        assert "Claude" in instructions
        assert str(cr.id) in instructions

    # Status nudged forward.
    assert cr.status == "awaiting_assistant"

    # included_files records sha256s for everything that landed in the zip.
    sha_paths = {f["path"] for f in packet.included_files if f["included"]}
    assert "README.md" in sha_paths
    assert "requirements.txt" in sha_paths


def test_build_packet_markdown_only_for_small_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Small repos should get a single self-contained markdown file."""
    upstream = tmp_path / "ws" / "sample_app" / "upstream"
    upstream.mkdir(parents=True)
    (upstream / "README.md").write_text("# tiny\n")
    (upstream / "requirements.txt").write_text("requests\n")

    monkeypatch.setattr(
        ap,
        "app_workspace_path",
        lambda app_id: tmp_path / "ws" / app_id,  # noqa: ARG005
    )
    cr = _make_change_request(upstream=upstream)
    cr.static_facts = dict(cr.static_facts)
    cr.static_facts["repo_size_bytes"] = 1024  # 1 KB → md-only
    db = _FakeDb(cr)

    packet = ap.build_packet(db, cr.id)  # no force_format → auto

    assert packet.format == "markdown"
    assert packet.zip_bytes == b""
    assert packet.markdown_bytes
    body = packet.markdown_bytes.decode("utf-8")
    assert "static_facts" in body or "정적 분석" in body
    assert "README.md" in body


def test_build_packet_skips_oversized_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = tmp_path / "ws" / "sample_app" / "upstream"
    upstream.mkdir(parents=True)
    # 60 KB README — over the 50 KB cap.
    (upstream / "README.md").write_text("x" * (60 * 1024))
    (upstream / "requirements.txt").write_text("requests\n")

    monkeypatch.setattr(
        ap,
        "app_workspace_path",
        lambda app_id: tmp_path / "ws" / app_id,  # noqa: ARG005
    )
    cr = _make_change_request(upstream=upstream)
    db = _FakeDb(cr)

    packet = ap.build_packet(db, cr.id, force_format="zip")
    with zipfile.ZipFile(io.BytesIO(packet.zip_bytes)) as zf:
        names = set(zf.namelist())
        assert "files/README.md" not in names
        assert "files/requirements.txt" in names

    # README is recorded as not-included with the size reason.
    readme_entry = next(f for f in packet.included_files if f["path"] == "README.md")
    assert readme_entry["included"] is False
    assert "size_exceeds_" in readme_entry["reason"]


# ---------------------------------------------------------------------------
# parse_assistant_response — JSON envelope
# ---------------------------------------------------------------------------


_GOOD_JSON_PAYLOAD: dict[str, Any] = {
    "manifest_draft": {
        "schema_version": 2,
        "id": "sample_app",
        "name": "Sample",
        "version": "0.1.0",
        "owner": "cae-automation",
        "status": "draft",
        "app_type": "cli_tool",
        "execution_target": "linux_runner",
        "launch": {"mode": "job_runner", "command": "./.portal/run.sh"},
    },
    "confidence": {"id": 0.95, "launch.mode": 0.8},
    "open_questions": [],
    "developer_change_request": {
        "summary": "포탈 등록",
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
                "content": "#!/usr/bin/env bash\npython -m sample\n",
            },
        ],
        "suggested_files": [],
        "rationale": "...",
    },
}


def test_parse_json_envelope_passes() -> None:
    raw = json.dumps(_GOOD_JSON_PAYLOAD, ensure_ascii=False)
    result = parse_assistant_response(raw)
    assert result["manifest_draft"]["id"] == "sample_app"
    paths = [
        f["path"]
        for f in result["developer_change_request"]["required_files"]
    ]
    assert ".portal/manifest.yaml" in paths
    assert ".portal/run.sh" in paths


def test_parse_json_envelope_with_code_fence() -> None:
    raw = "```json\n" + json.dumps(_GOOD_JSON_PAYLOAD) + "\n```"
    result = parse_assistant_response(raw)
    assert result["manifest_draft"]["schema_version"] == 2


# ---------------------------------------------------------------------------
# parse_assistant_response — Markdown form
# ---------------------------------------------------------------------------


_GOOD_MD_RESPONSE = """\
# Sample 분석 결과

이 저장소는 단순한 python CLI 도구로 보입니다.

## manifest.yaml
```yaml
schema_version: 2
id: sample_app
name: Sample App
version: 0.1.0
owner: cae-automation
status: draft
app_type: cli_tool
execution_target: linux_runner
launch:
  mode: job_runner
  command: ./.portal/run.sh
```

## run.sh
```bash run.sh
#!/usr/bin/env bash
set -euo pipefail
python -m sample
```

change_request_id: 00000000-0000-0000-0000-000000000000
repo_url: https://github.com/example/sample
"""


def test_parse_markdown_with_yaml_and_bash() -> None:
    result = parse_assistant_response(_GOOD_MD_RESPONSE)
    assert result["manifest_draft"]["id"] == "sample_app"
    paths = [
        f["path"]
        for f in result["developer_change_request"]["required_files"]
    ]
    assert ".portal/manifest.yaml" in paths
    assert ".portal/run.sh" in paths
    # Default confidence values inserted.
    assert result["confidence"]["id"] == 0.5


# ---------------------------------------------------------------------------
# Rejection paths
# ---------------------------------------------------------------------------


def test_parse_rejects_unsafe_required_file_path() -> None:
    bad = dict(_GOOD_JSON_PAYLOAD)
    bad["developer_change_request"] = dict(_GOOD_JSON_PAYLOAD["developer_change_request"])
    bad["developer_change_request"]["required_files"] = [
        {
            "path": "upstream/src/leak.py",  # NOT under .portal/
            "kind": "create",
            "content": "print('leak')\n",
        }
    ]
    with pytest.raises(_ValErr) as exc_info:
        parse_assistant_response(json.dumps(bad))
    assert ".portal/" in str(exc_info.value)


def test_parse_rejects_empty_input() -> None:
    with pytest.raises(_ValErr):
        parse_assistant_response("")


def test_parse_rejects_markdown_without_manifest_yaml() -> None:
    md = (
        "# 분석\n\n"
        "```bash\n"
        "echo hi\n"
        "```\n"
    )
    with pytest.raises(_ValErr):
        parse_assistant_response(md)


def test_parse_rejects_json_with_missing_required_key() -> None:
    incomplete = {
        "manifest_draft": {"id": "x"},
        "confidence": {},
        "open_questions": [],
        # developer_change_request missing -> schema requires it
    }
    with pytest.raises(_ValErr):
        parse_assistant_response(json.dumps(incomplete))
