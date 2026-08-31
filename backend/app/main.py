"""FastAPI application initialization."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.app.api.routes.database_schema import router as database_schema_router
from backend.app.api.routes.health import router as health_router
from backend.app.api.routes.query import router as query_router
from backend.app.core.config import Settings, get_settings
from backend.app.db.engine import dispose_runtime_engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Release process-wide runtime resources when FastAPI shuts down."""
    try:
        yield
    finally:
        dispose_runtime_engine()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    application_settings = settings or get_settings()
    application = FastAPI(
        title=application_settings.app_title,
        version=application_settings.app_version,
        lifespan=lifespan,
    )
    application.include_router(health_router)
    application.include_router(database_schema_router)
    application.include_router(query_router)
    if settings is not None:
        application.dependency_overrides[get_settings] = lambda: application_settings
    return application


app = create_app()
