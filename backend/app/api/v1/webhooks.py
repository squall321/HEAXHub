"""Inbound webhooks (GitHub, Windows agent)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Request

from app.config import get_settings
from app.core.errors import UnauthorizedError
from app.core.security import verify_github_signature
from app.workers.webhook_tasks import handle_github_tag

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
) -> dict[str, Any]:
    settings = get_settings()
    body = await request.body()
    if not verify_github_signature(settings.github_webhook_secret, body, x_hub_signature_256):
        raise UnauthorizedError("Invalid signature")
    payload = await request.json()
    handle_github_tag.delay(payload)
    return {"ok": True}


@router.post("/windows-agent")
async def windows_agent_webhook(request: Request) -> dict[str, Any]:
    """Stub endpoint for future Windows Agent integration."""
    return {"ok": True, "note": "Windows agent webhook stub"}
