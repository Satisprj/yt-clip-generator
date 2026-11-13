"""FastAPI entry point.

Run:  uvicorn app.main:app --reload
"""

import logging
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        settings.jobs_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "%s v%s started (env=%s, storage=%s)",
            settings.app_name, __version__, settings.app_env, settings.storage_dir,
        )
        if shutil.which(settings.claude_cli_path) is None:
            logger.warning(
                "Claude CLI '%s' not found; highlight detection will fail.",
                settings.claude_cli_path,
            )
        yield

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url=f"{settings.api_prefix}/docs",
        redoc_url=None,
        openapi_url=f"{settings.api_prefix}/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
