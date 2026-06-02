"""Stage 2 of the inferrer pipeline — LLM-driven manifest draft.

Calls the configured LLM provider with static facts + select file blobs and
returns a structured ``LLMResult``. JSON-schema validates the response and
retries on parse / validation errors. Falls back to a deterministic stub when
``LLM_API_KEY`` is missing so the rest of the system works end-to-end without
external connectivity.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

from app.config import get_settings
from app.core.logger import get_logger
from app.services import llm_provider
from app.services.static_analyzer import StaticFacts

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class LLMResult:
    manifest_draft: dict[str, Any] = field(default_factory=dict)
    confidence: dict[str, float] = field(default_factory=dict)
    open_questions: list[dict[str, Any]] = field(default_factory=list)
    developer_change_request: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> "LLMResult":
        return cls(
            manifest_draft=dict(payload.get("manifest_draft") or {}),
            confidence={
                str(k): float(v)
                for k, v in (payload.get("confidence") or {}).items()
                if isinstance(v, (int, float))
            },
            open_questions=list(payload.get("open_questions") or []),
            developer_change_request=dict(payload.get("developer_change_request") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# JSON-schema loading
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LLM_SCHEMA_PATH = _REPO_ROOT / "schemas" / "llm_response.schema.json"


@lru_cache
def _llm_schema() -> dict[str, Any]:
    with _LLM_SCHEMA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Context construction
# ---------------------------------------------------------------------------


_BLOB_CAP_BYTES = 50_000
_ENTRY_CAP_BYTES = 30_000

_CONTEXT_FILES = [
    "README.md",
    "README.rst",
    "README",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "Pipfile",
    "Dockerfile",
    "Apptainer.def",
    "Singularity",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".github/workflows/release.yml",
    ".github/workflows/build.yml",
    "Makefile",
    ".env.example",
]


def build_context(workspace: Path, facts: StaticFacts) -> dict[str, Any]:
    """Build the LLM input bundle. ``workspace`` is ``app_workspaces/{id}``."""
    upstream = workspace / "upstream" if workspace.name != "upstream" else workspace
    blobs: dict[str, str] = {}

    for rel in _CONTEXT_FILES:
        p = upstream / rel
        if p.exists() and p.is_file():
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size > _BLOB_CAP_BYTES:
                continue
            try:
                blobs[rel] = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

    for entry in facts.entry_files[:2]:
        p = upstream / entry
        if not p.exists() or not p.is_file():
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size > _ENTRY_CAP_BYTES:
            continue
        try:
            blobs[entry] = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

    return {"static_facts": facts.to_dict(), "files": blobs}


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = """\
You analyze a software repository and produce a JSON output for HEAXHub —
사내 자동화 포탈. Output JSON ONLY. No prose, no markdown fences.

Shape (strict):
{
  "manifest_draft": { ... yaml-serializable per HEAXHub manifest schema v2 ... },
  "confidence": { "<dotted.field.path>": 0.0-1.0 },
  "open_questions": [
    { "field": "...", "question": "...", "candidates": [...], "context": "..." }
  ],
  "developer_change_request": {
    "summary": "...",
    "required_files": [
      { "path": ".portal/manifest.yaml", "kind": "create", "content": "..." },
      { "path": ".portal/run.sh", "kind": "create", "content": "...", "mode": "0755" }
    ],
    "suggested_files": [
      { "path": "README.md", "kind": "append", "section": "...", "content": "..." }
    ],
    "rationale": "..."
  }
}

Hard rules:
- 절대 upstream 소스 코드를 수정 제안하지 마라. `.portal/` 디렉터리 추가만 허용한다.
- developer_change_request.required_files[*].path는 `.portal/`로 시작해야 한다.
  suggested_files에 한해 README.md append만 허용한다.
- confidence < 0.7 인 항목은 manifest_draft에서 제외하고 open_questions에 넣어라.
- 응답 본문(설명/한국어 문장)은 한국어로 작성, 코드 본문/필드명/manifest 키는 영문 유지.
- README의 명령을 그대로 신뢰하지 말고 어떤 명령이 데몬형(launch.mode=service)인지 배치형
  (launch.mode=job_runner)인지 분류해라.
- manifest_draft.schema_version 은 항상 2.
"""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class LLMResponseInvalid(Exception):
    pass


def _validate_no_upstream_modifications(payload: dict[str, Any]) -> None:
    dcr = payload.get("developer_change_request") or {}
    for f in dcr.get("required_files") or []:
        path = (f or {}).get("path", "")
        if not path.startswith(".portal/"):
            raise LLMResponseInvalid(f"Unsafe path in required_files: {path!r}")
    for f in dcr.get("suggested_files") or []:
        path = (f or {}).get("path", "")
        if not (path.startswith(".portal/") or path in {"README.md", "README.rst"}):
            raise LLMResponseInvalid(f"Unsafe path in suggested_files: {path!r}")


def _validate_payload(payload: dict[str, Any]) -> None:
    jsonschema.validate(payload, _llm_schema())
    _validate_no_upstream_modifications(payload)


# ---------------------------------------------------------------------------
# Public entry: call_llm
# ---------------------------------------------------------------------------


def call_llm(context: dict[str, Any], *, max_retries: int = 3) -> LLMResult:
    """Call the configured LLM provider and return a parsed ``LLMResult``.

    When provider is stub (or API key missing), returns a deterministic
    stub based on static_facts so downstream pipeline keeps working.
    """
    settings = get_settings()
    provider = llm_provider.get_provider()

    # Fast-path: stub provider builds its result locally — no JSON round-trip.
    if isinstance(provider, llm_provider.StubLLMProvider):
        return _stub_result(context)

    user_payload = json.dumps(context, default=str)
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            raw = provider.complete(system=SYSTEM_PROMPT, user=user_payload)
            payload = _coerce_json(raw)
            _validate_payload(payload)
            return LLMResult.parse(payload)
        except (json.JSONDecodeError, jsonschema.ValidationError, LLMResponseInvalid) as exc:
            last_error = exc
            logger.warning(
                "LLM response invalid (provider=%s attempt=%d/%d): %s",
                provider.name, attempt, max_retries, exc,
            )
        except Exception as exc:  # network / 5xx / etc.
            last_error = exc
            logger.exception(
                "LLM call failed (provider=%s attempt=%d/%d)",
                provider.name, attempt, max_retries,
            )
    assert last_error is not None
    raise last_error  # surface to caller


def _coerce_json(raw: str) -> dict[str, Any]:
    """Try to extract a JSON object from the model output even if it included fences."""
    text = (raw or "").strip()
    if text.startswith("```"):
        # Strip leading and trailing fences.
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


# ---------------------------------------------------------------------------
# Stub
# ---------------------------------------------------------------------------


def _stub_result(context: dict[str, Any]) -> LLMResult:
    facts = context.get("static_facts") or {}
    languages = facts.get("languages") or []
    python_ver = facts.get("python_version")
    node_ver = facts.get("node_version")
    daemon = facts.get("daemon_indicators") or []
    is_service = bool(daemon)

    if "python" in languages:
        build_type = "python_venv"
    elif "nodejs" in languages:
        build_type = "nodejs"
    elif facts.get("has_dockerfile"):
        build_type = "docker_build"
    elif facts.get("has_apptainer_def"):
        build_type = "apptainer"
    elif facts.get("has_compose_yaml"):
        build_type = "compose"
    else:
        build_type = "none"

    manifest_draft: dict[str, Any] = {
        "schema_version": 2,
        "id": "TODO_app_id",
        "name": "TODO App Name",
        "version": "0.1.0",
        "owner": "cae-automation",
        "status": "draft",
        "app_type": "web_app" if is_service else "cli_tool",
        "execution_target": "linux_runner",
        "launch": {
            "mode": "service" if is_service else "job_runner",
            "command": "./.portal/run.sh",
        },
        "build": {"type": build_type},
        "env_required": list(facts.get("detected_env_references") or [])[:20],
    }
    if python_ver and build_type == "python_venv":
        manifest_draft["build"]["python_version"] = python_ver
    if node_ver and build_type == "nodejs":
        manifest_draft["build"]["node_version"] = str(node_ver)

    # Mid confidence everywhere — operator must review.
    confidence: dict[str, float] = {
        "schema_version": 1.0,
        "id": 0.0,
        "name": 0.0,
        "version": 0.5,
        "owner": 0.5,
        "status": 0.5,
        "app_type": 0.5,
        "execution_target": 0.5,
        "build.type": 0.5,
        "launch.mode": 0.5,
        "launch.command": 0.5,
    }
    if python_ver:
        confidence["build.python_version"] = 0.9
    if node_ver:
        confidence["build.node_version"] = 0.9
    if manifest_draft.get("env_required"):
        confidence["env_required"] = 0.7

    open_questions = [
        {
            "field": "id",
            "question": "앱 ID(snake_case)를 알려주세요.",
            "candidates": [],
            "context": "stub mode — LLM_API_KEY 미설정",
        },
        {
            "field": "name",
            "question": "포탈에 표시할 앱 이름을 알려주세요.",
            "candidates": [],
            "context": "stub mode",
        },
    ]

    manifest_yaml_str = json.dumps(manifest_draft, indent=2, ensure_ascii=False)
    run_script = "#!/usr/bin/env bash\nset -euo pipefail\n# TODO: actual run command\n"

    return LLMResult(
        manifest_draft=manifest_draft,
        confidence=confidence,
        open_questions=open_questions,
        developer_change_request={
            "summary": "stub: HEAXHub 등록을 위한 .portal/ 디렉터리 초안",
            "required_files": [
                {
                    "path": ".portal/manifest.yaml",
                    "kind": "create",
                    "content": manifest_yaml_str,
                },
                {
                    "path": ".portal/run.sh",
                    "kind": "create",
                    "content": run_script,
                    "mode": "0755",
                },
            ],
            "suggested_files": [],
            "rationale": "LLM 키가 설정되지 않아 정적 분석 결과만으로 초안 생성. "
            "운영자 검토가 반드시 필요합니다.",
        },
    )
