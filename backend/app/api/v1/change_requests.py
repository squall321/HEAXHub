"""Change-request operator endpoints (Stage 3 of the AI inferrer pipeline).

Routes:
- POST   /change-requests
- GET    /change-requests
- GET    /change-requests/{id}
- PATCH  /change-requests/{id}
- POST   /change-requests/{id}/issue
- GET    /change-requests/{id}/markdown
- POST   /webhooks/github/pr   ← registered here (the path lives under /api/v1)

All operator endpoints require an AdminUser. The webhook is unauthenticated
but verified via the GitHub HMAC signature.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select

from app.config import get_settings
from app.core.errors import NotFoundError, UnauthorizedError, ValidationError
from app.core.security import verify_github_signature
from app.db.models.change_request import ChangeRequest
from app.deps import AdminUser, DbSession
from app.schemas.change_request import (
    AssistantSubmitRequest,
    ChangeRequestCreate,
    ChangeRequestIssueRequest,
    ChangeRequestIssueResult,
    ChangeRequestOut,
    ChangeRequestPatch,
)
from app.schemas.common import Paginated
from app.services import assistant_packet as assistant_packet_service
from app.services import change_request as change_request_service

router = APIRouter(tags=["change-requests"])


# ---------------------------------------------------------------------------
# /change-requests
# ---------------------------------------------------------------------------


@router.post(
    "/change-requests",
    response_model=ChangeRequestOut,
    status_code=status.HTTP_201_CREATED,
)
def create_change_request(
    payload: ChangeRequestCreate,
    db: DbSession,
    actor: AdminUser,
) -> ChangeRequestOut:
    cr = change_request_service.create_draft(
        db,
        submission_id=payload.submission_id,
        repo_url=payload.repo_url,
        actor=actor,
        app_id=payload.app_id,
    )
    return ChangeRequestOut.model_validate(cr)


@router.get("/change-requests", response_model=Paginated[ChangeRequestOut])
def list_change_requests(
    db: DbSession,
    _admin: AdminUser,
    status_: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> Paginated[ChangeRequestOut]:
    stmt = select(ChangeRequest).order_by(ChangeRequest.created_at.desc())
    if status_:
        stmt = stmt.where(ChangeRequest.status == status_)
    rows = list(db.execute(stmt).scalars())
    total = len(rows)
    offset = (page - 1) * page_size
    items = [
        ChangeRequestOut.model_validate(r) for r in rows[offset : offset + page_size]
    ]
    return Paginated(items=items, total=total, page=page, page_size=page_size)


@router.get("/change-requests/{change_request_id}", response_model=ChangeRequestOut)
def get_change_request(
    change_request_id: uuid.UUID,
    db: DbSession,
    _admin: AdminUser,
) -> ChangeRequestOut:
    cr = db.get(ChangeRequest, change_request_id)
    if cr is None:
        raise NotFoundError("Change request not found")
    return ChangeRequestOut.model_validate(cr)


@router.patch(
    "/change-requests/{change_request_id}", response_model=ChangeRequestOut
)
def patch_change_request(
    change_request_id: uuid.UUID,
    payload: ChangeRequestPatch,
    db: DbSession,
    _admin: AdminUser,
) -> ChangeRequestOut:
    overrides = payload.operator_overrides or {}
    cr = change_request_service.update_overrides(
        db,
        change_request_id=change_request_id,
        overrides=overrides,
    )
    return ChangeRequestOut.model_validate(cr)


@router.post(
    "/change-requests/{change_request_id}/issue",
    response_model=ChangeRequestIssueResult,
)
def issue_change_request(
    change_request_id: uuid.UUID,
    payload: ChangeRequestIssueRequest,
    db: DbSession,
    actor: AdminUser,
) -> ChangeRequestIssueResult:
    result = change_request_service.issue(
        db,
        change_request_id=change_request_id,
        via=payload.via,
        actor=actor,
    )
    return ChangeRequestIssueResult(**result)


@router.get(
    "/change-requests/{change_request_id}/markdown",
    response_class=PlainTextResponse,
)
def download_change_request_markdown(
    change_request_id: uuid.UUID,
    db: DbSession,
    _admin: AdminUser,
) -> PlainTextResponse:
    cr = db.get(ChangeRequest, change_request_id)
    if cr is None:
        raise NotFoundError("Change request not found")
    return PlainTextResponse(content=cr.markdown_body, media_type="text/markdown")


# ---------------------------------------------------------------------------
# Claude-in-the-loop assistant endpoints
# ---------------------------------------------------------------------------


@router.post("/change-requests/{change_request_id}/assistant/packet")
def build_assistant_packet(
    change_request_id: uuid.UUID,
    db: DbSession,
    _admin: AdminUser,
    force: str | None = None,
) -> Response:
    """Build and stream the Claude analysis packet.

    Automatically picks zip vs markdown based on repo size. Use
    ``?force=zip`` or ``?force=md`` (alias of ``markdown``) to override.

    Also transitions the CR into ``awaiting_assistant``.
    """
    force_format: str | None = None
    if force is not None:
        normalized = force.lower()
        if normalized in {"md", "markdown"}:
            force_format = "markdown"
        elif normalized == "zip":
            force_format = "zip"
        else:
            raise ValidationError("force must be 'zip' or 'markdown'")

    packet = assistant_packet_service.build_packet(
        db, change_request_id, force_format=force_format
    )
    body = packet.markdown_bytes if packet.format == "markdown" else packet.zip_bytes
    headers = {
        "Content-Disposition": f'attachment; filename="{packet.filename}"',
        "X-HEAXHub-Packet-SHA256": packet.packet_sha256,
        "X-HEAXHub-Packet-Format": packet.format,
    }
    return Response(
        content=body,
        media_type=packet.content_type,
        headers=headers,
    )


@router.get(
    "/change-requests/{change_request_id}/assistant/instructions",
    response_class=PlainTextResponse,
)
def get_assistant_instructions(
    change_request_id: uuid.UUID,
    db: DbSession,
    _admin: AdminUser,
) -> PlainTextResponse:
    """Return just the instructions.md body (no zip side-effects)."""
    cr = db.get(ChangeRequest, change_request_id)
    if cr is None:
        raise NotFoundError("Change request not found")
    instructions = assistant_packet_service._render_instructions(cr)  # noqa: SLF001
    return PlainTextResponse(content=instructions, media_type="text/markdown")


@router.post(
    "/change-requests/{change_request_id}/assistant/submit",
    response_model=ChangeRequestOut,
)
def submit_assistant_response(
    change_request_id: uuid.UUID,
    payload: AssistantSubmitRequest,
    db: DbSession,
    actor: AdminUser,
) -> ChangeRequestOut:
    """Accept Claude's pasted response. Parses, validates, applies."""
    normalized = assistant_packet_service.parse_assistant_response(payload.raw_text)
    cr = assistant_packet_service.apply_assistant_response(
        db,
        change_request_id=change_request_id,
        normalized=normalized,
        actor=actor,
    )
    return ChangeRequestOut.model_validate(cr)


# ---------------------------------------------------------------------------
# /webhooks/github/pr — unauthenticated, signature-verified
# ---------------------------------------------------------------------------


@router.post("/webhooks/github/pr")
async def github_pr_webhook(
    request: Request,
    db: DbSession,
    x_hub_signature_256: str | None = Header(default=None),
) -> dict[str, object]:
    settings = get_settings()
    body = await request.body()
    if not verify_github_signature(settings.github_webhook_secret, body, x_hub_signature_256):
        raise UnauthorizedError("Invalid signature")
    payload = await request.json()

    pr = payload.get("pull_request") or {}
    pr_url = pr.get("html_url")
    if not pr_url:
        return {"ok": True, "matched": 0}

    cr = db.execute(
        select(ChangeRequest).where(ChangeRequest.pr_url == pr_url)
    ).scalar_one_or_none()
    if cr is None:
        return {"ok": True, "matched": 0}

    action = payload.get("action")
    merged = bool(pr.get("merged"))
    state = pr.get("state")

    if action == "closed" and merged:
        cr.status = "merged"
        cr.merged_at = datetime.now(timezone.utc)
    elif action == "closed" and not merged:
        cr.status = "rejected"
    elif state == "open":
        # Keep status if already issued_pr; otherwise normalise.
        if cr.status != "issued_pr":
            cr.status = "issued_pr"
    db.commit()
    return {"ok": True, "matched": 1, "status": cr.status}
