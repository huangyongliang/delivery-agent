"""Application entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from delivery_agent import __version__
from delivery_agent.api import register_routes
from delivery_agent.config import Settings, get_settings
from delivery_agent.runtime import Runtime

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    _configure_logging(resolved.log_level)
    runtime = Runtime.build(resolved)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await runtime.aclose()

    app = FastAPI(
        title="交付代理",
        summary="面向 GitLab、Jenkins 与 Docker 的产品交付代理",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    register_routes(app)
    return app


def main() -> None:
    settings = get_settings()
    _configure_logging(settings.log_level)
    uvicorn.run(
        "delivery_agent.main:create_app",
        host=settings.agent_host,
        port=settings.agent_port,
        factory=True,
        log_level=settings.log_level.lower(),
    )


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.debug("logging configured at %s", level)
