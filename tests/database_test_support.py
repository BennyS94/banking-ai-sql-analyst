"""Helpers for isolated PostgreSQL integration tests."""

from __future__ import annotations

from contextlib import contextmanager
import os
from typing import Iterator
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import URL, make_url


@contextmanager
def temporary_database() -> Iterator[str]:
    source = os.environ.get("DATABASE_URL")
    if not source:
        raise unittest.SkipTest("DATABASE_URL is required for PostgreSQL integration tests")

    source_url = make_url(source)
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
