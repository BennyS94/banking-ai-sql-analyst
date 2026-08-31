"""FastAPI application initialization."""

from fastapi import FastAPI

from backend.app.api.routes.database_schema import router as database_schema_router
from backend.app.api.routes.health import router as health_router
from backend.app.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    application_settings = settings or get_settings()
    application = FastAPI(
        title=application_settings.app_title,
        version=application_settings.app_version,
    )
    application.include_router(health_router)
    application.include_router(database_schema_router)
    return application


app = create_app()
