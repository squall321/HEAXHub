"""Aggregate all v1 routers."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    agents,
    apps,
    auth,
    change_requests,
    installers,
    jobs,
    submissions,
    users,
    webhooks,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(apps.router)
api_router.include_router(jobs.router)
api_router.include_router(submissions.router)
api_router.include_router(admin.router)
api_router.include_router(webhooks.router)
api_router.include_router(change_requests.router)
api_router.include_router(agents.router)
api_router.include_router(agents.admin_router)
api_router.include_router(installers.router)
