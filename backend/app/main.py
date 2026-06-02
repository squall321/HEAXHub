"""FastAPI application entrypoint for HEAXHub backend."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import ws as ws_module
from app.api.v1.router import api_router
from app.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logger import get_logger, setup_logging
from app.core.rate_limit import RateLimitMiddleware
from app.services import integration_workspaces
from app.services import secret_manager as _sm

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    settings = get_settings()

    # JWT secret hardening: refuse to boot in non-dev envs if the secret is
    # still the placeholder (or empty). In development we only warn so local
    # workflows keep functioning out of the box.
    _placeholder_jwt_secrets = ("", "change-me-to-a-strong-random-secret")
    if settings.jwt_secret in _placeholder_jwt_secrets:
        msg = (
            "JWT_SECRET is unset or still the placeholder value. "
            "Generate a strong secret with: "
            "`openssl rand -hex 32` "
            "and set JWT_SECRET in your environment/.env before booting."
        )
        if settings.app_env != "development":
            raise RuntimeError(msg)
        logger.warning(msg)

    # Ensure storage roots exist
    settings.job_storage_root.mkdir(parents=True, exist_ok=True)
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Starting HEAXHub backend env=%s workspace=%s storage=%s",
        settings.app_env,
        settings.workspace_root,
        settings.job_storage_root,
    )

    # Secret-manager fail-safe: warn (not error) so boot still works for
    # non-secret features. Any set_secret/get_secret call will raise.
    try:
        if not _sm.is_configured():
            logger.warning(
                "SECRET_ENCRYPTION_KEY is empty — secret_manager will refuse "
                "set/get calls. Generate a key with `python -c \"from "
                "cryptography.fernet import Fernet; print(Fernet.generate_key()"
                ".decode())\"` and add it to .env."
            )
    except Exception:  # noqa: BLE001
        logger.exception("secret_manager startup check failed")

    # Pre-clone integration repos so operators always have a local copy to
    # rebuild / repackage. Best-effort: failures are logged but do not block boot.
    try:
        results = integration_workspaces.ensure_all_cloned()
        for r in results:
            if r.error:
                logger.warning("integration repo %s not ready: %s", r.repo_url, r.error)
            elif r.cloned:
                logger.info(
                    "integration repo %s present at %s (commit=%s)",
                    r.repo_url, r.upstream, (r.commit_sha or "?")[:8],
                )
    except Exception:  # noqa: BLE001
        logger.exception("ensure_all_cloned failed at startup")

    yield
    logger.info("HEAXHub backend shutting down")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="HEAXHub Backend",
        description="Internal automation portal — registers, builds, and runs apps.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Order matters: add_middleware prepends, so the LAST one added wraps
    # everything else. We want CORS to be outermost so OPTIONS preflights are
    # served without consuming rate-limit budget.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    # REST
    app.include_router(api_router)
    # WebSocket (no prefix; path is /ws/jobs/{job_id}/logs)
    app.include_router(ws_module.router)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", tags=["meta"])
    def root() -> dict[str, str]:
        return {
            "name": "HEAXHub Backend",
            "version": "0.1.0",
            "docs": "/docs",
        }

    return app


app = create_app()
