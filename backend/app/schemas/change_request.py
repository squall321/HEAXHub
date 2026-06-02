"""Pydantic schemas for change_request endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field


class ChangeRequestCreate(BaseModel):
    """Operator initiates a new change-request from a submission or a raw repo URL."""

    submission_id: uuid.UUID | None = None
    repo_url: str = Field(min_length=1, max_length=1024)
    app_id: str | None = None


class ChangeRequestPatch(BaseModel):
    """Operator override patch — recomputes final_manifest + markdown."""

    operator_overrides: dict[str, Any] | None = None


class ChangeRequestIssueRequest(BaseModel):
    via: Literal["pr", "issue", "markdown"]


class ChangeRequestIssueResult(BaseModel):
    url: str | None = None
    content: str | None = None


class AssistantSubmitRequest(BaseModel):
    """Operator pastes Claude's raw response (JSON or markdown) here."""

    raw_text: str = Field(min_length=1)


# Statuses where the operator can still hand off to Claude (zip download
# makes sense and won't overwrite a published artifact).
_ASSISTANT_HANDOFF_STATUSES: frozenset[str] = frozenset(
    {"draft", "awaiting_assistant"}
)


class ChangeRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    submission_id: uuid.UUID | None = None
    app_id: str | None = None
    repo_url: str
    commit_sha: str | None = None
    static_facts: dict[str, Any]
    llm_response: dict[str, Any]
    operator_overrides: dict[str, Any]
    final_manifest: dict[str, Any]
    markdown_body: str
    pr_payload: dict[str, Any] | None = None
    status: str
    pr_url: str | None = None
    issue_url: str | None = None
    issued_at: datetime | None = None
    merged_at: datetime | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def assistant_packet_available(self) -> bool:
        """True iff the operator can still hand this CR off to Claude."""
        return self.status in _ASSISTANT_HANDOFF_STATUSES
