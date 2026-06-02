"""Claude-in-the-loop assistant packet service.

This module provides the backend half of a *manual* handoff between HEAXHub
and an external Claude chat. Because corporate policy + cost forbid direct
LLM API calls, the operator instead:

1. Clicks "AI packet 만들기" in the HEAXHub admin UI.
2. ``build_packet`` zips up static-analyzer facts and select repo files.
3. The operator downloads that zip, attaches it to Claude (the chat), and
   asks Claude for a manifest proposal.
4. Claude's response (either a JSON envelope or a markdown blob containing
   embedded YAML codeblocks) is pasted into the HEAXHub UI.
5. ``parse_assistant_response`` normalizes it; ``apply_assistant_response``
   re-validates and merges it into the existing ChangeRequest.

This module *must not* call any LLM API. It only packages and parses.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationError
from app.core.logger import get_logger
from app.db.models.change_request import ChangeRequest
from app.db.models.user import User
from app.services import audit_service, change_request as cr_service
from app.services.change_request import _deep_merge
from app.services.workspace_manager import app_workspace_path

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class AssistantPacket:
    change_request_id: uuid.UUID
    zip_bytes: bytes
    instructions_md: str
    static_facts: dict[str, Any]
    included_files: list[dict[str, Any]] = field(default_factory=list)
    packet_sha256: str = ""
    # Format selection — for small repos, just a single markdown file is enough.
    format: str = "zip"                # "zip" | "markdown"
    markdown_bytes: bytes = b""        # populated when format == "markdown"
    content_type: str = "application/zip"
    filename: str = ""                 # heaxhub-packet-{short}.zip or .md


# md-only 모드 결정 임계값. 모든 조건을 만족할 때 markdown만 발행.
_MD_ONLY_MAX_TOTAL_BYTES = 30 * 1024   # 30 KB
_MD_ONLY_MAX_FILE_COUNT = 6
_MD_ONLY_MAX_REPO_BYTES = 200 * 1024   # 200 KB


# ---------------------------------------------------------------------------
# File selection for the packet
# ---------------------------------------------------------------------------

_PACKET_FILE_NAMES: list[str] = [
    # Documentation
    "README.md",
    "README.rst",
    "README",
    # Python
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "Pipfile.lock",
    ".python-version",
    "runtime.txt",
    # Node
    "package.json",
    ".nvmrc",
    # Multi-language toolchains
    ".tool-versions",
    # Containers
    "Dockerfile",
    "Apptainer.def",
    "Singularity.def",
    "docker-compose.yml",
    "docker-compose.yaml",
    # Build / package
    "Makefile",
    "CMakeLists.txt",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    # Deployment
    "Procfile",
]

# Glob-ish: files we want to include even when there are several.
_PACKET_DIR_GLOBS: list[tuple[str, str]] = [
    (".github/workflows", "*.yml"),
    (".github/workflows", "*.yaml"),
]

_MAX_FILE_BYTES = 50 * 1024  # 50 KB per file


def _select_packet_files(upstream: Path, entry_files: list[str]) -> list[Path]:
    """Enumerate the upstream files that go into the packet."""
    found: list[Path] = []
    seen: set[Path] = set()

    def _maybe_add(p: Path) -> None:
        try:
            if not p.is_file():
                return
        except OSError:
            return
        resolved = p.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        found.append(p)

    for name in _PACKET_FILE_NAMES:
        _maybe_add(upstream / name)

    for sub, pattern in _PACKET_DIR_GLOBS:
        sub_dir = upstream / sub
        if sub_dir.is_dir():
            for p in sorted(sub_dir.glob(pattern)):
                _maybe_add(p)

    # PyInstaller-style *.spec files in repo root
    for p in sorted(upstream.glob("*.spec")):
        _maybe_add(p)

    for rel in entry_files or []:
        _maybe_add(upstream / rel)

    return found


# ---------------------------------------------------------------------------
# Instructions template
# ---------------------------------------------------------------------------


_INSTRUCTIONS_TEMPLATE = """\
# HEAXHub 포탈 등록 — Claude 분석 요청서

## 1. 이 zip 의 목적
사내 자동화 포탈 **HEAXHub** 에 다음 저장소를 등록하기 위해, Claude 의 도움이 필요합니다.

- repo: {repo_url}
- change_request_id: {change_request_id}
- commit_sha: {commit_sha}

운영자는 LLM API 호출이 불가능한 망에서 작업하므로, 이 zip 의 정보를 보고 Claude 가
manifest 초안과 명세서를 만들어주시면, 운영자가 결과를 HEAXHub 에 붙여 넣을 것입니다.

## 2. 분석 방법
1. `static_facts.json` 의 결정론적 분석 결과를 먼저 읽어주세요.
2. `files/` 디렉터리의 원본 파일 (README, Dockerfile, package.json 등) 을 참고해서
   `static_facts` 가 채우지 못한 부분을 보강해주세요.
3. README 의 명령을 그대로 신뢰하지 말고, 어떤 명령이 데몬형 (`launch.mode=service`)
   이고 어떤 명령이 배치형 (`launch.mode=job_runner`) 인지 분류해주세요.
4. confidence < 0.7 인 항목은 manifest_draft 에서 빼고 `open_questions` 에 넣어주세요.

## 3. 응답 형식
다음 **두 형식 중 하나** 를 사용해주세요.

### 형식 A — JSON envelope (권장)
순수 JSON 만, 코드펜스 없이 응답해주세요. shape 은 다음과 같습니다.

```json
{{
  "manifest_draft": {{
    "schema_version": 2,
    "id": "snake_case_app_id",
    "name": "표시 이름",
    "version": "0.1.0",
    "owner": "cae-automation",
    "status": "draft",
    "app_type": "cli_tool | web_app | windows_gui | slurm_job | container_app | ...",
    "execution_target": "linux_runner | slurm | apptainer | windows_worker | ...",
    "launch": {{
      "mode": "service | job_runner",
      "command": "./.portal/run.sh"
    }}
  }},
  "confidence": {{
    "id": 0.95,
    "launch.mode": 0.8
  }},
  "open_questions": [
    {{
      "field": "resources.timeout_seconds",
      "question": "최대 실행 시간을 알려주세요.",
      "candidates": [600, 1800, 3600],
      "context": "README 에 명시되지 않음"
    }}
  ],
  "developer_change_request": {{
    "summary": "포탈 등록을 위한 .portal/ 디렉터리 추가",
    "required_files": [
      {{
        "path": ".portal/manifest.yaml",
        "kind": "create",
        "content": "schema_version: 2\\nid: ...\\n"
      }},
      {{
        "path": ".portal/run.sh",
        "kind": "create",
        "mode": "0755",
        "content": "#!/usr/bin/env bash\\n..."
      }}
    ],
    "suggested_files": [],
    "rationale": "근거 설명"
  }}
}}
```

### 형식 B — Markdown + YAML (대안)
다음과 같이 마크다운 본문에 YAML 코드블록을 박아주셔도 됩니다. 코드블록 라벨은
필수입니다.

````markdown
# 분석 결과

## manifest.yaml
```yaml
schema_version: 2
id: snake_case_app_id
name: 표시 이름
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
python -m my_app
```

## open_questions
- `resources.timeout_seconds`: 최대 실행 시간을 알려주세요.
````

이 경우 HEAXHub 는 YAML 블록을 `.portal/manifest.yaml` 로, `run.sh` 라벨이 붙은
bash 블록을 `.portal/run.sh` 로 자동 매핑합니다.

## 4. 금지 사항
**절대 upstream 의 기존 소스 코드를 수정하지 마세요.** 추가 파일은 모두
`.portal/` 디렉터리 아래에만 들어가야 합니다 (예외: `README.md` append 권장만
suggested_files 에 허용).

- `developer_change_request.required_files[*].path` 는 반드시 `.portal/` 로
  시작해야 합니다.
- 위반 시 HEAXHub 가 응답을 거부합니다.

## 5. HEAXHub 에 다시 붙여넣는 방법
1. Claude 가 응답을 출력하면 **응답 전체** 를 복사합니다.
2. HEAXHub 의 change-request 상세 페이지에서 "Claude 응답 붙여넣기" 박스에 paste 합니다.
3. 백엔드가 자동으로 파싱/검증 후 `assistant_responded` 상태로 갱신합니다.

## 6. 응답 끝에 반드시 포함
응답의 어딘가에 다음 두 줄을 그대로 포함해주세요. 매칭에 사용됩니다.

```
change_request_id: {change_request_id}
repo_url: {repo_url}
```
"""


def _render_instructions(cr: ChangeRequest) -> str:
    return _INSTRUCTIONS_TEMPLATE.format(
        change_request_id=str(cr.id),
        repo_url=cr.repo_url or "(unknown)",
        commit_sha=cr.commit_sha or "(unknown)",
    )


# ---------------------------------------------------------------------------
# Packet builder
# ---------------------------------------------------------------------------


def _should_use_md_only(
    file_blobs: list[tuple[str, bytes]],
    static_facts: dict[str, Any],
) -> bool:
    """Return True when a single markdown file is enough (small repo)."""
    if len(file_blobs) > _MD_ONLY_MAX_FILE_COUNT:
        return False
    total_bytes = sum(len(b) for _, b in file_blobs)
    if total_bytes > _MD_ONLY_MAX_TOTAL_BYTES:
        return False
    repo_size = int(static_facts.get("repo_size_bytes") or 0)
    if repo_size > _MD_ONLY_MAX_REPO_BYTES:
        return False
    return True


def _guess_lang_fence(rel_path: str) -> str:
    """Language hint for a fenced codeblock based on file extension/name."""
    name_lower = rel_path.rsplit("/", 1)[-1].lower()
    if name_lower in {"dockerfile", "makefile", "procfile"}:
        return name_lower
    ext = rel_path.rsplit(".", 1)[-1].lower() if "." in rel_path else ""
    mapping = {
        "md": "markdown", "rst": "rst",
        "py": "python", "js": "javascript", "ts": "typescript",
        "jsx": "jsx", "tsx": "tsx",
        "json": "json", "yaml": "yaml", "yml": "yaml", "toml": "toml",
        "sh": "bash", "bash": "bash",
        "def": "singularity",
        "cmake": "cmake",
        "rs": "rust", "go": "go",
        "cpp": "cpp", "c": "c", "h": "c", "hpp": "cpp",
        "java": "java", "kt": "kotlin",
        "html": "html", "css": "css", "tf": "hcl",
    }
    return mapping.get(ext, "")


def _build_markdown_only(
    cr: ChangeRequest,
    instructions_md: str,
    static_facts: dict[str, Any],
    file_blobs: list[tuple[str, bytes]],
) -> bytes:
    """Compose a self-contained markdown packet for small repos."""
    lines: list[str] = []
    lines.append(f"# HEAXHub 분석 패킷 — {cr.repo_url or '(unknown repo)'}")
    lines.append("")
    lines.append("## 0. change_request 식별")
    lines.append("")
    lines.append("```json")
    lines.append(
        json.dumps(
            {
                "id": str(cr.id),
                "repo_url": cr.repo_url,
                "commit_sha": cr.commit_sha,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    lines.append("```")
    lines.append("")
    lines.append("## 1. 안내사항")
    lines.append("")
    lines.append(instructions_md.strip())
    lines.append("")
    lines.append("## 2. 정적 분석 결과 (static_facts.json)")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(static_facts, ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## 3. 첨부 파일")
    lines.append("")
    if not file_blobs:
        lines.append("_(첨부할 파일을 찾지 못했습니다. static_facts만 보고 분석해 주세요.)_")
    for rel, data in file_blobs:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = "(binary content — omitted)"
        lang = _guess_lang_fence(rel)
        lines.append(f"### `{rel}` ({len(data)} bytes)")
        lines.append("")
        lines.append(f"```{lang}")
        lines.append(text)
        lines.append("```")
        lines.append("")
    lines.append("## 4. 응답 방법")
    lines.append("")
    lines.append(
        "분석을 마친 후 응답을 그대로 HEAXHub의 '응답 붙여넣기' 박스에 paste 해주세요. "
        "JSON envelope 또는 yaml+markdown 두 형식 모두 허용됩니다."
    )
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def build_packet(
    db: Session,
    change_request_id: uuid.UUID,
    *,
    force_format: str | None = None,
) -> AssistantPacket:
    """Build the analysis packet for a ChangeRequest.

    Picks zip vs markdown automatically based on repo size. ``force_format``
    ('zip' or 'markdown') overrides the decision for the operator.

    Side effects:
    - Updates ``ChangeRequest.status`` to ``awaiting_assistant`` (only when it
      is currently in a pre-handoff state — draft / awaiting_assistant).
    - Emits an audit entry ``change_request.packet_built``.
    """
    cr = db.get(ChangeRequest, change_request_id)
    if cr is None:
        raise NotFoundError("Change request not found")

    upstream = _resolve_upstream(cr)

    static_facts: dict[str, Any] = dict(cr.static_facts or {})
    entry_files: list[str] = list(static_facts.get("entry_files") or [])
    candidate_paths = _select_packet_files(upstream, entry_files) if upstream else []

    included: list[dict[str, Any]] = []
    file_blobs: list[tuple[str, bytes]] = []

    for path in candidate_paths:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if upstream is None:
            continue
        try:
            rel = path.resolve().relative_to(upstream.resolve())
        except ValueError:
            continue
        rel_str = str(rel).replace("\\", "/")

        if size > _MAX_FILE_BYTES:
            included.append(
                {
                    "path": rel_str,
                    "size": size,
                    "sha256": None,
                    "included": False,
                    "reason": f"size_exceeds_{_MAX_FILE_BYTES}",
                }
            )
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            included.append(
                {
                    "path": rel_str,
                    "size": size,
                    "sha256": None,
                    "included": False,
                    "reason": f"read_error: {exc}",
                }
            )
            continue
        sha = hashlib.sha256(data).hexdigest()
        included.append(
            {
                "path": rel_str,
                "size": size,
                "sha256": sha,
                "included": True,
            }
        )
        file_blobs.append((rel_str, data))

    instructions_md = _render_instructions(cr)
    identifier = {
        "id": str(cr.id),
        "repo_url": cr.repo_url,
        "commit_sha": cr.commit_sha,
    }

    # Decide packet format: small repo → markdown only, otherwise zip.
    if force_format == "markdown":
        use_md = True
    elif force_format == "zip":
        use_md = False
    else:
        use_md = _should_use_md_only(file_blobs, static_facts)

    short_id = str(cr.id)[:8]
    zip_bytes: bytes = b""
    md_bytes: bytes = b""
    packet_sha: str = ""
    fmt: str
    content_type: str
    filename: str

    if use_md:
        md_bytes = _build_markdown_only(cr, instructions_md, static_facts, file_blobs)
        packet_sha = hashlib.sha256(md_bytes).hexdigest()
        fmt = "markdown"
        content_type = "text/markdown; charset=utf-8"
        filename = f"heaxhub-cr-{short_id}.md"
    else:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("instructions.md", instructions_md)
            zf.writestr(
                "static_facts.json",
                json.dumps(static_facts, ensure_ascii=False, indent=2),
            )
            zf.writestr(
                "change_request.json",
                json.dumps(identifier, ensure_ascii=False, indent=2),
            )
            for rel, data in file_blobs:
                zf.writestr(f"files/{rel}", data)
        zip_bytes = buf.getvalue()
        packet_sha = hashlib.sha256(zip_bytes).hexdigest()
        fmt = "zip"
        content_type = "application/zip"
        filename = f"heaxhub-cr-{short_id}.zip"

    # Update CR status — only nudge forward from pre-handoff states.
    if cr.status in {"draft", "awaiting_assistant"}:
        cr.status = "awaiting_assistant"
        db.commit()
        db.refresh(cr)

    audit_service.log(
        db,
        actor_user_id=None,
        action="change_request.packet_built",
        target_type="change_request",
        target_id=str(cr.id),
        meta={
            "packet_sha256": packet_sha,
            "file_count_included": sum(1 for f in included if f.get("included")),
            "file_count_total": len(included),
            "size_bytes": len(zip_bytes) if fmt == "zip" else len(md_bytes),
            "format": fmt,
            "forced": force_format,
        },
    )

    return AssistantPacket(
        change_request_id=cr.id,
        zip_bytes=zip_bytes,
        instructions_md=instructions_md,
        static_facts=static_facts,
        included_files=included,
        packet_sha256=packet_sha,
        format=fmt,
        markdown_bytes=md_bytes,
        content_type=content_type,
        filename=filename,
    )


def _resolve_upstream(cr: ChangeRequest) -> Path | None:
    """Best-effort resolution of the upstream workspace for this CR."""
    if cr.app_id:
        try:
            base = app_workspace_path(cr.app_id)
        except Exception:
            base = None
        if base is not None:
            up = base / "upstream"
            if up.exists():
                return up
            if base.exists():
                return base
    # No app_id or workspace missing. Returning None is fine: included_files
    # will simply be empty, but the packet still ships static_facts + instructions.
    return None


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


_LLM_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3] / "schemas" / "llm_response.schema.json"
)


def _llm_schema() -> dict[str, Any]:
    with _LLM_SCHEMA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


_CODE_FENCE_RE = re.compile(
    r"```([a-zA-Z0-9_+\-./ ]*)\n(.*?)```",
    re.DOTALL,
)


def parse_assistant_response(raw_text: str) -> dict[str, Any]:
    """Normalize a Claude response into LLM_RESPONSE_SCHEMA-compatible dict.

    Two input forms are supported:

    1. **JSON envelope** — the entire text is JSON (optionally wrapped in
       a ```json codefence). Parsed directly.
    2. **Markdown + YAML** — markdown text containing fenced codeblocks; the
       first ```yaml block whose body contains ``schema_version`` becomes
       ``.portal/manifest.yaml`` and ``manifest_draft``, and any block labelled
       ``run.sh`` (case-insensitive) becomes ``.portal/run.sh``.

    Raises :class:`ValidationError` with a field-specific message on bad input.
    """
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValidationError("assistant response is empty")

    text = raw_text.strip()

    # ----- Detect JSON-first ----------------------------------------------
    json_candidate = _strip_json_fence(text)
    if json_candidate is not None:
        try:
            payload = json.loads(json_candidate)
        except json.JSONDecodeError as exc:
            raise ValidationError(
                f"assistant response: JSON parse failed at line {exc.lineno} "
                f"col {exc.colno}: {exc.msg}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValidationError(
                "assistant response: top-level JSON must be an object"
            )
        normalized = payload
    else:
        normalized = _parse_markdown_response(text)

    # ----- Validate against schema ----------------------------------------
    _validate_schema(normalized)
    _validate_no_upstream_modifications(normalized)

    # Ensure confidence has *some* entry (schema only requires the key to be
    # an object); if it's missing entirely (schema requires the key), the
    # earlier validation would have failed.
    return normalized


def _strip_json_fence(text: str) -> str | None:
    """If text looks like JSON, return the JSON substring; else None."""
    stripped = text.strip()
    # Bare JSON object?
    if stripped.startswith("{"):
        return stripped
    # Code-fenced JSON ?
    if stripped.startswith("```"):
        # Take the first ```json|``` block.
        m = _CODE_FENCE_RE.search(stripped)
        if m:
            label = (m.group(1) or "").strip().lower()
            body = m.group(2).strip()
            if (label in {"", "json"}) and body.startswith("{"):
                return body
    return None


def _label_tokens(label: str) -> set[str]:
    """Tokenize a code fence label like 'bash run.sh' or 'yaml/manifest.yaml'."""
    return {t for t in re.split(r"[\s/]+", label) if t}


def _try_extract_manifest_block(
    label: str, body: str, tokens: set[str]
) -> tuple[str, dict[str, Any]] | None:
    """Return (yaml_text, parsed_dict) if this fenced block is the manifest YAML."""
    if not ("yaml" in tokens or "yml" in tokens or "manifest.yaml" in tokens):
        return None
    try:
        parsed = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        raise ValidationError(
            f"assistant response: yaml block parse failed: {exc}"
        ) from exc
    if isinstance(parsed, dict) and "schema_version" in parsed:
        return body.strip(), parsed
    return None


def _try_extract_run_sh_block(label: str, body: str, tokens: set[str]) -> str | None:
    """Return the run.sh body if this fenced block is a run.sh script."""
    label_signals_run = (
        "run.sh" in tokens or "run" in tokens or label.endswith("run.sh")
    )
    if not label_signals_run:
        return None
    if "bash" in tokens or "sh" in tokens or "shell" in tokens:
        return body.strip()
    if label.strip() == "run.sh":
        return body.strip()
    return None


def _scan_markdown_blocks(
    text: str,
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """Walk every fenced block once and pull out manifest YAML + run.sh."""
    blocks = list(_CODE_FENCE_RE.finditer(text))
    if not blocks:
        raise ValidationError(
            "assistant response: no JSON object and no fenced codeblocks found"
        )

    manifest_yaml: str | None = None
    manifest_draft: dict[str, Any] | None = None
    run_sh: str | None = None

    for m in blocks:
        label = (m.group(1) or "").strip().lower()
        body = m.group(2)
        tokens = _label_tokens(label)

        if manifest_yaml is None:
            found = _try_extract_manifest_block(label, body, tokens)
            if found is not None:
                manifest_yaml, manifest_draft = found
                continue

        if run_sh is None:
            run_sh = _try_extract_run_sh_block(label, body, tokens)

    return manifest_yaml, manifest_draft, run_sh


def _ensure_trailing_newline(s: str) -> str:
    return s if s.endswith("\n") else s + "\n"


def _build_required_files(
    manifest_yaml: str, run_sh: str | None
) -> list[dict[str, Any]]:
    """Materialize required_files entries for manifest.yaml (and optional run.sh)."""
    required: list[dict[str, Any]] = [
        {
            "path": ".portal/manifest.yaml",
            "kind": "create",
            "content": _ensure_trailing_newline(manifest_yaml),
        }
    ]
    if run_sh is not None:
        required.append(
            {
                "path": ".portal/run.sh",
                "kind": "create",
                "mode": "0755",
                "content": _ensure_trailing_newline(run_sh),
            }
        )
    return required


def _assemble_markdown_response(
    text: str,
    manifest_yaml: str,
    manifest_draft: dict[str, Any],
    run_sh: str | None,
) -> dict[str, Any]:
    """Compose the LLM-response shape from extracted markdown pieces."""
    summary = _first_paragraph(text) or "Claude 응답 (markdown form)"
    required_files = _build_required_files(manifest_yaml, run_sh)
    confidence = {str(k): 0.5 for k in manifest_draft.keys()}
    return {
        "manifest_draft": manifest_draft,
        "confidence": confidence,
        "open_questions": [],
        "developer_change_request": {
            "summary": summary,
            "required_files": required_files,
            "suggested_files": [],
            "rationale": "Claude markdown 응답에서 자동 매핑됨.",
        },
    }


def _parse_markdown_response(text: str) -> dict[str, Any]:
    """Extract manifest + run.sh from markdown with embedded codeblocks."""
    manifest_yaml, manifest_draft, run_sh = _scan_markdown_blocks(text)
    if manifest_draft is None or manifest_yaml is None:
        raise ValidationError(
            "assistant response: could not find a ```yaml``` block containing "
            "'schema_version' (required for markdown form)"
        )
    return _assemble_markdown_response(text, manifest_yaml, manifest_draft, run_sh)


def _first_paragraph(text: str) -> str | None:
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            return s.lstrip("#").strip() or None
        return s
    return None


def _validate_schema(payload: dict[str, Any]) -> None:
    try:
        jsonschema.validate(payload, _llm_schema())
    except jsonschema.ValidationError as exc:
        # exc.path is a deque of keys/indices. Provide a stable string.
        path = ".".join(str(p) for p in exc.absolute_path) or "<root>"
        raise ValidationError(
            f"assistant response: schema validation failed at '{path}': {exc.message}"
        ) from exc


def _validate_no_upstream_modifications(payload: dict[str, Any]) -> None:
    dcr = payload.get("developer_change_request") or {}
    for f in dcr.get("required_files") or []:
        path = (f or {}).get("path") or ""
        if not path.startswith(".portal/"):
            raise ValidationError(
                f"assistant response: required_files[*].path must start with "
                f"'.portal/'; got '{path}'"
            )
    for f in dcr.get("suggested_files") or []:
        path = (f or {}).get("path") or ""
        # Suggested_files may reference README.md but never upstream source.
        if not (path.startswith(".portal/") or path in {"README.md", "README.rst"}):
            raise ValidationError(
                f"assistant response: suggested_files path '{path}' must live "
                "under .portal/ (README.md / README.rst are the only exceptions)"
            )


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply_assistant_response(
    db: Session,
    change_request_id: uuid.UUID,
    normalized: dict[str, Any],
    actor: User | None,
) -> ChangeRequest:
    """Merge a normalized Claude response into a ChangeRequest.

    Re-validates the safety rules, recomputes ``final_manifest`` and
    ``markdown_body``, transitions status to ``assistant_responded`` and
    emits an audit entry.
    """
    cr = db.get(ChangeRequest, change_request_id)
    if cr is None:
        raise NotFoundError("Change request not found")

    if cr.status in {"merged", "rejected", "superseded"}:
        raise ValidationError(
            f"Cannot apply assistant response to change request in terminal "
            f"status '{cr.status}'"
        )

    _validate_schema(normalized)
    _validate_no_upstream_modifications(normalized)

    # Persist response and recompute downstream fields.
    cr.llm_response = normalized
    manifest_draft = dict(normalized.get("manifest_draft") or {})
    cr.final_manifest = _merge_with_overrides(manifest_draft, cr.operator_overrides)
    cr.markdown_body = cr_service.render_markdown(cr)
    cr.pr_payload = cr_service.render_pr_payload(cr)
    cr.status = "assistant_responded"

    db.commit()
    db.refresh(cr)

    audit_service.log(
        db,
        actor_user_id=actor.id if actor is not None else None,
        action="change_request.assistant_responded",
        target_type="change_request",
        target_id=str(cr.id),
        meta={
            "manifest_id": manifest_draft.get("id"),
            "required_file_count": len(
                (normalized.get("developer_change_request") or {}).get(
                    "required_files"
                )
                or []
            ),
        },
    )
    return cr


def _merge_with_overrides(
    manifest_draft: dict[str, Any], overrides: dict[str, Any] | None
) -> dict[str, Any]:
    """Re-apply operator overrides on top of the new manifest_draft."""
    overrides = overrides or {}
    manifest_override = overrides.get("manifest") or {}
    if not manifest_override:
        return dict(manifest_draft)
    return _deep_merge(manifest_draft, manifest_override)
