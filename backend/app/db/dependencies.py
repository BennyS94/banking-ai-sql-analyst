"""FastAPI dependencies for synchronous runtime database access."""

from collections.abc import Iterator

from sqlalchemy import Connection

from backend.app.db.engine import runtime_connection


def get_database_connection() -> Iterator[Connection]:
    """Yield one connection and return it to the engine pool after the request."""
    with runtime_connection() as connection:
        yield connection
