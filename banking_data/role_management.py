"""Provision the least-privilege PostgreSQL analytical reader."""

from __future__ import annotations

import argparse
import os
from typing import Sequence

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from banking_data.database import database_url


READER_USER_ENV = "BANKING_READER_USER"
READER_PASSWORD_ENV = "BANKING_READER_PASSWORD"


class RoleConfigurationError(RuntimeError):
    """Raised when safe role provisioning cannot proceed."""


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RoleConfigurationError(f"{name} must be set")
    return value


def _psycopg_connection_string(sqlalchemy_url: str) -> str:
    url = make_url(sqlalchemy_url).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


def provision_reader_role(owner_url: str, reader_user: str, reader_password: str) -> dict[str, str]:
    """Create or harden the reader and grant SELECT-only banking access."""
    if not reader_user or not reader_password:
        raise RoleConfigurationError("reader user and password must be non-empty")

    with psycopg.connect(_psycopg_connection_string(owner_url), autocommit=True) as connection:
        database_name, owner_user = connection.execute(
            "SELECT current_database(), current_user"
        ).fetchone()
        if reader_user == owner_user:
            raise RoleConfigurationError("reader role must differ from the owner/loader role")

        exists = connection.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)",
            (reader_user,),
        ).fetchone()[0]
        role_identifier = sql.Identifier(reader_user)
        password_literal = sql.Literal(reader_password)
        if exists:
            connection.execute(
                sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                    role_identifier, password_literal
                )
            )
        else:
            connection.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    role_identifier, password_literal
                )
            )

        connection.execute(
            sql.SQL(
                "ALTER ROLE {} NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOREPLICATION NOBYPASSRLS"
            ).format(role_identifier)
        )
        connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database_name), role_identifier
            )
        )
        connection.execute(
            sql.SQL("REVOKE CREATE ON DATABASE {} FROM {}").format(
                sql.Identifier(database_name), role_identifier
            )
        )
        connection.execute("REVOKE ALL ON ALL TABLES IN SCHEMA banking FROM PUBLIC")
        connection.execute("REVOKE CREATE ON SCHEMA banking FROM PUBLIC")
        connection.execute(
            sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA banking FROM {}").format(
                role_identifier
            )
        )
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA banking TO {}").format(role_identifier)
        )
        connection.execute(
            sql.SQL("REVOKE CREATE ON SCHEMA banking FROM {}").format(role_identifier)
        )
        connection.execute(
            sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA banking TO {}").format(
                role_identifier
            )
        )

    return {
        "database": database_name,
        "owner_role": owner_user,
        "reader_role": reader_user,
        "status": "provisioned",
    }


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description=__doc__)


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    result = provision_reader_role(
        database_url(),
        _required_environment(READER_USER_ENV),
        _required_environment(READER_PASSWORD_ENV),
    )
    print(
        f"Reader role {result['reader_role']} provisioned for "
        f"database {result['database']} with SELECT-only banking access."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
