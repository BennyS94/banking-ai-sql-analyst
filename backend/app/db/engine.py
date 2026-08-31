"""Least-privilege PostgreSQL engine and connectivity boundary."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import logging

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from backend.app.core.config import Settings, get_settings


logger = logging.getLogger(__name__)
SUPPORTED_RUNTIME_DRIVER = "postgresql+psycopg"


class DatabaseConfigurationError(RuntimeError):
    """Raised when read-only runtime database configuration is unusable."""


class DatabaseConnectionError(RuntimeError):
    """Raised when the runtime PostgreSQL database cannot be reached safely."""


@dataclass(frozen=True)
class DatabaseConnectionInfo:
    user: str
    database: str


def create_runtime_engine(settings: Settings) -> Engine:
    """Create a synchronous engine configured only for the analytical reader."""
    raw_url = settings.banking_reader_database_url
    if not raw_url:
        raise DatabaseConfigurationError(
            "BANKING_READER_DATABASE_URL must be set for runtime database access"
        )

    try:
        url = make_url(raw_url)
    except (ArgumentError, TypeError, ValueError) as exc:
        raise DatabaseConfigurationError(
            "BANKING_READER_DATABASE_URL must be a valid SQLAlchemy URL"
        ) from exc

    if url.drivername != SUPPORTED_RUNTIME_DRIVER:
        raise DatabaseConfigurationError(
            "BANKING_READER_DATABASE_URL must use postgresql+psycopg"
        )
    if url.username != settings.banking_reader_user:
        raise DatabaseConfigurationError(
            "BANKING_READER_DATABASE_URL user must match BANKING_READER_USER"
        )

    try:
        return create_engine(url, pool_pre_ping=True)
    except (SQLAlchemyError, ImportError, TypeError, ValueError) as exc:
        raise DatabaseConfigurationError(
            "BANKING_READER_DATABASE_URL cannot configure the runtime engine"
        ) from exc


@lru_cache
def get_runtime_engine() -> Engine:
    """Return the process-wide synchronous runtime engine."""
    return create_runtime_engine(get_settings())


def dispose_runtime_engine() -> None:
    """Dispose the cached engine and clear its configuration-bound cache."""
    if get_runtime_engine.cache_info().currsize:
        get_runtime_engine().dispose()
        get_runtime_engine.cache_clear()


def check_database_connection(engine: Engine) -> DatabaseConnectionInfo:
    """Run a trivial read and return the effective PostgreSQL identity."""
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT current_user, current_database()")
            ).one()
    except SQLAlchemyError as exc:
        logger.exception("Runtime PostgreSQL connectivity check failed")
        raise DatabaseConnectionError(
            "Unable to connect to the runtime PostgreSQL database"
        ) from exc

    return DatabaseConnectionInfo(user=row[0], database=row[1])


def runtime_connection(engine: Engine | None = None) -> Connection:
    """Open a runtime connection for FastAPI's yield-based dependency lifecycle."""
    return (engine or get_runtime_engine()).connect()
