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
FORBIDDEN_TABLE_PRIVILEGES = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)


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


def _role_oid(connection: psycopg.Connection, role_name: str) -> int | None:
    row = connection.execute(
        "SELECT oid FROM pg_roles WHERE rolname = %s", (role_name,)
    ).fetchone()
    return None if row is None else row[0]


def _role_memberships(connection: psycopg.Connection, role_oid: int) -> list[str]:
    return [
        row[0]
        for row in connection.execute(
            """
            SELECT parent.rolname
            FROM pg_auth_members AS membership
            JOIN pg_roles AS parent ON parent.oid = membership.roleid
            WHERE membership.member = %s
            ORDER BY parent.rolname
            """,
            (role_oid,),
        )
    ]


def _owned_project_objects(
    connection: psycopg.Connection, role_oid: int
) -> list[str]:
    owned: list[str] = []
    database = connection.execute(
        "SELECT datname FROM pg_database WHERE datname = current_database() AND datdba = %s",
        (role_oid,),
    ).fetchone()
    if database is not None:
        owned.append(f"database {database[0]}")

    owned.extend(
        f"schema {row[0]}"
        for row in connection.execute(
            "SELECT nspname FROM pg_namespace WHERE nspname = 'banking' AND nspowner = %s",
            (role_oid,),
        )
    )
    owned.extend(
        f"relation {row[0]}.{row[1]}"
        for row in connection.execute(
            """
            SELECT namespace.nspname, class.relname
            FROM pg_class AS class
            JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
            WHERE class.relowner = %s
              AND (
                  namespace.nspname = 'banking'
                  OR (namespace.nspname = 'public' AND class.relname = 'alembic_version')
              )
            ORDER BY namespace.nspname, class.relname
            """,
            (role_oid,),
        )
    )
    return owned


def _ensure_existing_role_is_safe(
    connection: psycopg.Connection, role_name: str, role_oid: int
) -> None:
    memberships = _role_memberships(connection, role_oid)
    if memberships:
        raise RoleConfigurationError(
            f"existing reader role {role_name!r} has role memberships: "
            f"{', '.join(memberships)}; provisioning refused"
        )

    owned = _owned_project_objects(connection, role_oid)
    if owned:
        raise RoleConfigurationError(
            f"existing reader role {role_name!r} owns relevant objects: "
            f"{', '.join(owned)}; provisioning refused"
        )


def _verify_effective_privilege_boundary(
    connection: psycopg.Connection,
    database_name: str,
    reader_user: str,
) -> None:
    role_oid = _role_oid(connection, reader_user)
    if role_oid is None:
        raise RoleConfigurationError("reader role disappeared during provisioning")

    flags = connection.execute(
        """
        SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
        FROM pg_roles
        WHERE oid = %s
        """,
        (role_oid,),
    ).fetchone()
    if flags != (False, False, False, False, False):
        raise RoleConfigurationError("reader role retains elevated role attributes")
    if _role_memberships(connection, role_oid):
        raise RoleConfigurationError("reader role retains inherited role privileges")
    if _owned_project_objects(connection, role_oid):
        raise RoleConfigurationError("reader role owns relevant project objects")

    database_privileges = connection.execute(
        "SELECT has_database_privilege(%s, %s, 'CONNECT'), "
        "has_database_privilege(%s, %s, 'CREATE'), "
        "has_database_privilege(%s, %s, 'TEMPORARY')",
        (
            reader_user,
            database_name,
            reader_user,
            database_name,
            reader_user,
            database_name,
        ),
    ).fetchone()
    if database_privileges != (True, False, False):
        raise RoleConfigurationError("reader database privilege boundary is unsafe")

    schema_privileges = connection.execute(
        "SELECT has_schema_privilege(%s, 'banking', 'USAGE'), "
        "has_schema_privilege(%s, 'banking', 'CREATE')",
        (reader_user, reader_user),
    ).fetchone()
    if schema_privileges != (True, False):
        raise RoleConfigurationError("reader schema privilege boundary is unsafe")
    if connection.execute(
        "SELECT has_schema_privilege(%s, 'public', 'CREATE')", (reader_user,)
    ).fetchone()[0]:
        raise RoleConfigurationError("reader can create objects in the public schema")

    relations = connection.execute(
        """
        SELECT class.oid, class.relname
        FROM pg_class AS class
        JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
        WHERE namespace.nspname = 'banking'
          AND class.relkind IN ('r', 'p', 'v', 'm', 'f')
        ORDER BY class.relname
        """
    ).fetchall()
    if not relations:
        raise RoleConfigurationError("banking schema has no relations to protect")
    for relation_oid, relation_name in relations:
        if not connection.execute(
            "SELECT has_table_privilege(%s, %s, 'SELECT')",
            (reader_user, relation_oid),
        ).fetchone()[0]:
            raise RoleConfigurationError(
                f"reader lacks SELECT on banking.{relation_name}"
            )
        for privilege in FORBIDDEN_TABLE_PRIVILEGES:
            if connection.execute(
                "SELECT has_table_privilege(%s, %s, %s)",
                (reader_user, relation_oid, privilege),
            ).fetchone()[0]:
                raise RoleConfigurationError(
                    f"reader effectively has {privilege} on banking.{relation_name}"
                )

    unsafe_sequences = connection.execute(
        """
        SELECT class.relname
        FROM pg_class AS class
        JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
        WHERE namespace.nspname = 'banking'
          AND class.relkind = 'S'
          AND (
              has_sequence_privilege(%s, class.oid, 'USAGE')
              OR has_sequence_privilege(%s, class.oid, 'SELECT')
              OR has_sequence_privilege(%s, class.oid, 'UPDATE')
          )
        ORDER BY class.relname
        """,
        (reader_user, reader_user, reader_user),
    ).fetchall()
    if unsafe_sequences:
        names = ", ".join(row[0] for row in unsafe_sequences)
        raise RoleConfigurationError(
            f"reader effectively has sequence privileges on: {names}"
        )


def provision_reader_role(owner_url: str, reader_user: str, reader_password: str) -> dict[str, str]:
    """Create or harden the reader and grant SELECT-only banking access."""
    if not reader_user or not reader_password:
        raise RoleConfigurationError("reader user and password must be non-empty")

    with psycopg.connect(_psycopg_connection_string(owner_url)) as connection:
        database_name, owner_user = connection.execute(
            "SELECT current_database(), current_user"
        ).fetchone()
        if reader_user == owner_user:
            raise RoleConfigurationError("reader role must differ from the owner/loader role")

        existing_role_oid = _role_oid(connection, reader_user)
        if existing_role_oid is not None:
            _ensure_existing_role_is_safe(connection, reader_user, existing_role_oid)
        role_identifier = sql.Identifier(reader_user)
        password_literal = sql.Literal(reader_password)
        if existing_role_oid is not None:
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
        connection.execute(
            sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(database_name)
            )
        )
        connection.execute(
            sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM {}").format(
                sql.Identifier(database_name), role_identifier
            )
        )
        connection.execute("REVOKE ALL ON ALL TABLES IN SCHEMA banking FROM PUBLIC")
        connection.execute("REVOKE CREATE ON SCHEMA banking FROM PUBLIC")
        connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
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
            sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(role_identifier)
        )
        connection.execute(
            sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA banking TO {}").format(
                role_identifier
            )
        )
        connection.execute(
            sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA banking FROM {}").format(
                role_identifier
            )
        )
        _verify_effective_privilege_boundary(connection, database_name, reader_user)

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
