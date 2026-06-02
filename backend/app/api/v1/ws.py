"""WebSocket endpoints (real-time log streaming)."""
from __future__ import annotations

import asyncio

import redis.asyncio as aioredis
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.config import get_settings
from app.core.logger import get_logger
from app.core.security import decode_token
from app.db.models.job import Job
from app.db.models.user import User, UserRole
from app.db.session import SessionLocal

router = APIRouter(tags=["ws"])
logger = get_logger(__name__)


def _authorize(token: str | None) -> str:
    if not token:
        raise PermissionError("Missing token")
    payload = decode_token(token, expected_type="access")
    return payload["sub"]


def _can_view(job_id: str, user_id: str) -> bool:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return False
        if str(job.executor_user_id) == user_id:
            return True
        user = db.get(User, user_id)
        return bool(user and user.role == UserRole.ADMIN)


@router.websocket("/ws/jobs/{job_id}/logs")
async def stream_job_logs(
    websocket: WebSocket,
    job_id: str,
    token: str | None = Query(default=None),
) -> None:
    try:
        user_id = _authorize(token)
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    if not _can_view(job_id, user_id):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    settings = get_settings()
    client = aioredis.from_url(settings.redis_url)
    pubsub = client.pubsub()
    channel = f"logs:{job_id}"
    await pubsub.subscribe(channel)

    try:
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg is None:
                # heartbeat / yield
                await asyncio.sleep(0.1)
                continue
            data = msg.get("data")
            if isinstance(data, bytes):
                data = data.decode("utf-8", errors="replace")
            if data is None:
                continue
            await websocket.send_text(str(data))
            if isinstance(data, str) and data.startswith("__exit__:"):
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("ws stream error job=%s", job_id)
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
            await client.aclose()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
