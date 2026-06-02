"""Change-request service — Stage 3 of the AI manifest pipeline.

Responsibilities:
- Run the static analyzer + manifest LLM to produce a draft proposal.
- Persist + update ChangeRequest rows.
- Render Markdown bodies and PR payloads.
- Dispatch the request to GitHub (PR or issue) or hand back the raw Markdown.

This module deliberately imports the static_analyzer / manifest_llm modules
lazily so the change_request service can be loaded even before SA2 lands
those files. The GitHub integration is also imported lazily so absent
PyGithub does not break import-time.
"""
from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import NotFoundError, ValidationError
from app.core.logger import get_logger
from app.db.models.app import App as _App
from app.db.models.change_request import ChangeRequest
from app.db.models.submission import Submission
from app.db.models.user import User
from app.services import audit_service, github_integration
from app.services.manifest_llm import build_context, call_llm
from app.services.static_analyzer import analyze as _analyze
from app.services.workspace_manager import app_workspace_path


# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------


def create_draft(
    db: Session,
    *,
    submission_id: uuid.UUID | None,
    repo_url: str,
    actor: User,
    app_id: str | None = None,
) -> ChangeRequest:
    """Run static_analyzer + manifest_llm, persist a draft ChangeRequest.

    The workspace path is derived from the submission's proposed_app_id when
    available; otherwise from app_id; otherwise from a sanitized hash of the
    repo URL. The static_analyzer/manifest_llm modules are imported lazily
    so this module is still loadable when SA2's work isn't merged yet.
    """
    submission = _resolve_submission(db, submission_id)
    resolved_app_id = (
        app_id
        or (submission.proposed_app_id if submission else None)
    )
    workspace = _resolve_workspace(resolved_app_id, repo_url)

    # Only persist app_id if the apps row actually exists — otherwise FK violation.
    db_app_id: str | None = None
    if resolved_app_id is not None and db.get(_App, resolved_app_id) is not None:
        db_app_id = resolved_app_id

    static_facts, llm_response = _run_pipeline(workspace)

    # Validate the LLM's required_files now, before persisting. Re-validating
    # cheaply matches what manifest_llm should already do but acts as a
    # belt-and-braces guard since callers can pass forged payloads via tests.
    _enforce_required_files_safety(llm_response)

    manifest_draft = (llm_response.get("manifest_draft") or {})
    final_manifest = copy.deepcopy(manifest_draft)
    pr_payload = _build_pr_payload(llm_response, markdown_body="")
    commit_sha = static_facts.get("commit_sha")

    initial_status = "draft"
    if _is_stub_response(llm_response):
        # No real LLM available — operator must hand off the packet manually
        # (Claude-in-the-loop). Skip the 'draft → assistant_responded' shortcut.
        initial_status = "awaiting_assistant"

    cr = ChangeRequest(
        id=uuid.uuid4(),
        submission_id=submission.id if submission else None,
        app_id=db_app_id,
        repo_url=repo_url,
        commit_sha=commit_sha,
        static_facts=_jsonable(static_facts),
        llm_response=_jsonable(llm_response),
        operator_overrides={},
        final_manifest=final_manifest,
        markdown_body="",
        pr_payload=pr_payload,
        status=initial_status,
        created_by=actor.id if actor else None,
    )
    # Now that we have the assembled object, render markdown using its fields.
    cr.markdown_body = render_markdown(cr)
    if cr.pr_payload is not None:
        cr.pr_payload["body"] = cr.markdown_body

    db.add(cr)
    db.commit()
    db.refresh(cr)

    if initial_status == "awaiting_assistant":
        _safe_audit(
            db,
            actor_user_id=actor.id if actor else None,
            action="change_request.requires_manual_assistant",
            target_id=str(cr.id),
            meta={
                "reason": "stub LLM provider — operator must download packet "
                "and use external Claude chat",
            },
        )

    return cr


def update_overrides(
    db: Session,
    *,
    change_request_id: uuid.UUID,
    overrides: dict[str, Any],
) -> ChangeRequest:
    """Persist operator_overrides and recompute final_manifest + markdown."""
    cr = db.get(ChangeRequest, change_request_id)
    if cr is None:
        raise NotFoundError("Change request not found")
    if cr.status not in {
        "draft",
        "awaiting_assistant",
        "assistant_responded",
        "issued_md",
        "issued_pr",
        "issued_issue",
    }:
        # Allow edits while issued (operator may want to re-issue), but block
        # after merge/reject so the historical record is not silently rewritten.
        if cr.status in {"merged", "rejected", "superseded"}:
            raise ValidationError(
                f"Cannot edit change request in terminal status '{cr.status}'"
            )

    overrides = overrides or {}
    cr.operator_overrides = overrides
    manifest_draft = (cr.llm_response or {}).get("manifest_draft") or {}
    manifest_override = overrides.get("manifest") or {}
    cr.final_manifest = _deep_merge(manifest_draft, manifest_override)
    cr.markdown_body = render_markdown(cr)
    cr.pr_payload = _build_pr_payload(cr.llm_response, markdown_body=cr.markdown_body)

    db.commit()
    db.refresh(cr)
    return cr


def issue(
    db: Session,
    *,
    change_request_id: uuid.UUID,
    via: str,
    actor: User,
) -> dict[str, Any]:
    """Publish the change request via PR / Issue / Markdown."""
    cr = db.get(ChangeRequest, change_request_id)
    if cr is None:
        raise NotFoundError("Change request not found")

    if via not in {"pr", "issue", "markdown"}:
        raise ValidationError(f"Unknown issue channel '{via}'")

    # Re-validate safety just before sending anything outward.
    _enforce_required_files_safety(cr.llm_response or {})

    settings = get_settings()
    now = datetime.now(timezone.utc)

    if via == "markdown":
        cr.status = "issued_md"
        cr.issued_at = now
        db.commit()
        return {"content": cr.markdown_body}

    if via == "pr":
        required_files = ((cr.pr_payload or {}).get("required_files")) or []
        pr_title = "HEAXHub 포탈 등록 — .portal/ 디렉터리 추가"
        branch_name = f"heaxhub/portal-registration-{str(cr.id)[:8]}"
        pr_url = github_integration.publish_pr(
            repo_url=cr.repo_url,
            bot_token=settings.github_bot_token,
            branch_name=branch_name,
            files=required_files,
            pr_title=pr_title,
            pr_body=cr.markdown_body,
        )
        cr.pr_url = pr_url
        cr.status = "issued_pr"
        cr.issued_at = now
        db.commit()
        return {"url": pr_url}

    # via == "issue"
    issue_url = github_integration.publish_issue(
        repo_url=cr.repo_url,
        bot_token=settings.github_bot_token,
        title="HEAXHub 포탈 등록 요청",
        body=cr.markdown_body,
        labels=["heaxhub", "documentation"],
    )
    cr.issue_url = issue_url
    cr.status = "issued_issue"
    cr.issued_at = now
    db.commit()
    return {"url": issue_url}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_markdown(cr: ChangeRequest) -> str:
    """Render the change-request Markdown body (per CHANGE_REQUEST_DESIGN.md §7)."""
    llm = cr.llm_response or {}
    dcr = (llm.get("developer_change_request") or {})
    open_questions = llm.get("open_questions") or []
    final_manifest = cr.final_manifest or {}

    app_name = (
        final_manifest.get("name")
        or final_manifest.get("id")
        or cr.app_id
        or "(미정)"
    )

    manifest_yaml = yaml.safe_dump(
        final_manifest, sort_keys=False, allow_unicode=True
    ).rstrip()

    run_script = _find_file_content(dcr, ".portal/run.sh") or "# (.portal/run.sh 내용 없음)"

    readme_block = _render_readme_suggestion(dcr)
    questions_block = _render_open_questions(open_questions)
    summary = (dcr.get("summary") or "").strip()
    rationale = (dcr.get("rationale") or "").strip()
    version = final_manifest.get("version") or "0.1.0"

    parts: list[str] = []
    parts.append(f"# HEAXHub 포탈 등록 요청 — {app_name}\n")
    parts.append(
        "안녕하세요. 사내 자동화 포탈 운영팀입니다.\n"
        f"`{app_name}` 앱을 HEAXHub 포탈에 등록하기 위해 다음 변경을 부탁드립니다.\n"
    )
    parts.append(
        "**기존 소스 코드는 건드릴 필요가 없습니다.** "
        "모든 추가 파일은 `.portal/` 디렉터리에만 들어갑니다.\n"
    )
    if summary:
        parts.append("## 0. 요약\n" + summary + "\n")

    parts.append("## 1. 추가할 파일\n")
    parts.append("### `.portal/manifest.yaml` (신규)\n")
    parts.append("```yaml\n" + manifest_yaml + "\n```\n")
    parts.append("### `.portal/run.sh` (신규, 실행 권한 0755)\n")
    parts.append("```bash\n" + run_script.rstrip() + "\n```\n")

    parts.append("## 2. (선택) README에 추가 권장\n" + readme_block + "\n")
    parts.append("## 3. 확인이 필요한 항목\n" + questions_block + "\n")

    parts.append(
        "## 4. 적용 후\n"
        f"위 파일들을 PR로 merge 후 `git tag v{version} && git push --tags` 를 실행하시면\n"
        "포탈이 자동으로 새 버전을 검토 대기열에 올립니다.\n"
    )
    if rationale:
        parts.append("## 5. 근거\n" + rationale + "\n")
    parts.append("문의: heaxhub-operators@company.com\n")

    return "\n".join(parts).strip() + "\n"


def render_pr_payload(cr: ChangeRequest) -> dict[str, Any]:
    """Return the PR payload for a ChangeRequest."""
    return _build_pr_payload(cr.llm_response or {}, markdown_body=cr.markdown_body)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_STUB_SUMMARIES: frozenset[str] = frozenset(
    {
        # llm_provider.StubLLMProvider.complete() default body
        "stub provider — no LLM call made",
        # manifest_llm._stub_result() default body
        "stub: HEAXHub 등록을 위한 .portal/ 디렉터리 초안",
    }
)


def _is_stub_response(llm_response: dict[str, Any]) -> bool:
    """Detect when the LLM pipeline returned a non-AI stub.

    Stub responses must trigger the manual Claude-in-the-loop path instead
    of pretending we have a usable LLM draft.
    """
    dcr = (llm_response or {}).get("developer_change_request") or {}
    summary = (dcr.get("summary") or "").strip()
    if summary in _STUB_SUMMARIES:
        return True
    rationale = (dcr.get("rationale") or "").strip().lower()
    if "stub" in rationale and "llm" in rationale:
        return True
    return False


def _safe_audit(
    db: Session,
    *,
    actor_user_id: uuid.UUID | None,
    action: str,
    target_id: str,
    meta: dict[str, Any] | None,
) -> None:
    """Best-effort audit log. Never raises into the caller."""
    try:
        audit_service.log(
            db,
            actor_user_id=actor_user_id,
            action=action,
            target_type="change_request",
            target_id=target_id,
            meta=meta,
        )
    except Exception:  # pragma: no cover — auditing is non-critical
        get_logger(__name__).warning(
            "audit_log failed for action=%s target_id=%s", action, target_id
        )


def _enforce_required_files_safety(llm_response: dict[str, Any]) -> None:
    """Reject if any required_files path escapes .portal/.

    SA3 spec is strict: required_files must all live under .portal/. README
    edits, if any, must be expressed as suggested_files (which are not auto-
    committed by the PR pipeline).
    """
    dcr = (llm_response.get("developer_change_request") or {})
    for f in dcr.get("required_files") or []:
        path = (f or {}).get("path") or ""
        if not path:
            raise ValidationError("required_files entry has no path")
        if not path.startswith(".portal/"):
            raise ValidationError(
                f"required_files[*].path must start with '.portal/'; got '{path}'"
            )


def _build_pr_payload(
    llm_response: dict[str, Any],
    *,
    markdown_body: str,
) -> dict[str, Any]:
    dcr = (llm_response.get("developer_change_request") or {})
    return {
        "required_files": list(dcr.get("required_files") or []),
        "suggested_files": list(dcr.get("suggested_files") or []),
        "body": markdown_body,
    }


def _find_file_content(dcr: dict[str, Any], path: str) -> str | None:
    for f in dcr.get("required_files") or []:
        if (f or {}).get("path") == path:
            return f.get("content")
    return None


def _render_readme_suggestion(dcr: dict[str, Any]) -> str:
    suggested = dcr.get("suggested_files") or []
    readme_blocks = [
        f for f in suggested if (f or {}).get("path", "").lower().startswith("readme")
    ]
    if not readme_blocks:
        return "_(없음 — README 변경은 권장되지 않습니다.)_"
    lines: list[str] = []
    for f in readme_blocks:
        section = (f or {}).get("section") or "추가 섹션"
        content = (f or {}).get("content") or ""
        lines.append(f"#### {section}\n")
        lines.append(content.rstrip())
    return "\n".join(lines)


def _render_open_questions(open_questions: list[dict[str, Any]]) -> str:
    if not open_questions:
        return "_(없음)_"
    lines = ["| 항목 | 질문 | 후보 |", "|---|---|---|"]
    for q in open_questions:
        field = (q or {}).get("field") or ""
        question = (q or {}).get("question") or ""
        candidates = (q or {}).get("candidates") or []
        cand_text = ", ".join(str(c) for c in candidates) if candidates else "-"
        lines.append(f"| `{field}` | {question} | {cand_text} |")
    return "\n".join(lines)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge — override wins; lists/scalars are replaced wholesale."""
    if not isinstance(base, dict):
        return copy.deepcopy(override) if isinstance(override, dict) else override
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _jsonable(obj: Any) -> Any:
    """Coerce dataclass / Path / set to JSON-safe primitives."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "__dataclass_fields__"):
        from dataclasses import asdict as _asdict

        return _jsonable(_asdict(obj))
    # Fallback — best-effort JSON round-trip
    try:
        return json.loads(json.dumps(obj, default=str))
    except Exception:
        return str(obj)


def _resolve_submission(
    db: Session, submission_id: uuid.UUID | None
) -> Submission | None:
    if submission_id is None:
        return None
    sub = db.get(Submission, submission_id)
    if sub is None:
        raise NotFoundError("Submission not found")
    return sub


def _resolve_workspace(app_id: str | None, repo_url: str) -> Path:
    """Best-effort workspace path. Falls back to a deterministic temp path."""
    if app_id:
        try:
            return app_workspace_path(app_id)
        except Exception:
            pass
    # Fallback — analyzer will detect missing files gracefully.
    safe = "".join(c if c.isalnum() else "_" for c in repo_url)[:64]
    return Path(get_settings().workspace_root) / "_change_request_scratch" / safe


def _run_pipeline(workspace: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Invoke static_analyzer + manifest_llm."""
    facts = _analyze(workspace)
    facts_dict = _jsonable(facts)

    context = build_context(workspace, facts)
    result = call_llm(context)
    result_dict = _jsonable(result)

    if not isinstance(result_dict, dict):
        raise ValidationError("manifest_llm.call_llm must return a mapping")
    return facts_dict, result_dict
