"""Helpers for isolated PostgreSQL integration tests."""

from __future__ import annotations

from contextlib import contextmanager
import os
from typing import Iterator
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


TEST_OWNER_DATABASE_URL_ENV = "BANKING_TEST_OWNER_DATABASE_URL"


class TestDatabaseConfigurationError(RuntimeError):
    """Raised when the isolated PostgreSQL test target is not configured."""


def configured_test_owner_url() -> URL:
    """Return only the explicitly configured banking-project test owner URL."""
    value = os.environ.get(TEST_OWNER_DATABASE_URL_ENV)
    if not value:
        raise TestDatabaseConfigurationError(
            f"{TEST_OWNER_DATABASE_URL_ENV} must be set for PostgreSQL integration tests"
        )
    try:
        url = make_url(value)
    except (ArgumentError, TypeError, ValueError) as exc:
        raise TestDatabaseConfigurationError(
            f"{TEST_OWNER_DATABASE_URL_ENV} must be a valid SQLAlchemy URL"
        ) from exc
    if url.drivername != "postgresql+psycopg":
        raise TestDatabaseConfigurationError(
            f"{TEST_OWNER_DATABASE_URL_ENV} must use postgresql+psycopg"
        )
    return url


@contextmanager
def temporary_database() -> Iterator[str]:
    source_url = configured_test_owner_url()
    database_name = f"banking_test_{uuid4().hex}"
    admin_kwargs = _connection_kwargs(source_url, "postgres")

    with psycopg.connect(**admin_kwargs, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))

    test_url = source_url.set(database=database_name).render_as_string(hide_password=False)
    try:
        yield test_url
    finally:
        with psycopg.connect(**admin_kwargs, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(database_name)
                )
            )


def _connection_kwargs(url: URL, database: str) -> dict[str, object]:
    return {
        "dbname": database,
        "user": url.username,
        "password": url.password,
        "host": url.host,
        "port": url.port,
    }
