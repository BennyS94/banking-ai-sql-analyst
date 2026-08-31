"""Shared PostgreSQL connection configuration."""

from __future__ import annotations

import os

from sqlalchemy import Engine, create_engine


DATABASE_URL_ENV = "DATABASE_URL"


def database_url() -> str:
    """Return the configured database URL without supplying unsafe defaults."""
    value = os.environ.get(DATABASE_URL_ENV)
    if not value:
        raise RuntimeError(f"{DATABASE_URL_ENV} must be set")
    return value


def create_database_engine(*, echo: bool = False) -> Engine:
    """Create a SQLAlchemy 2.x engine for the configured PostgreSQL database."""
    return create_engine(database_url(), echo=echo)
