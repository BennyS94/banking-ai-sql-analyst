from __future__ import annotations

import os
from pathlib import Path
import tempfile
from unittest import TestCase, mock
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from banking_data.loading import load_processed_data
from banking_data.role_management import (
    RoleConfigurationError,
    _psycopg_connection_string,
    provision_reader_role,
)
from database_test_support import temporary_database
from test_loading import write_processed_fixture


class ReadOnlyRoleIntegrationTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_context = temporary_database()
        cls.owner_url = cls.database_context.__enter__()
        cls.environment = mock.patch.dict(os.environ, {"DATABASE_URL": cls.owner_url})
        cls.environment.start()
        command.upgrade(Config("alembic.ini"), "head")
        cls.owner_engine = create_engine(cls.owner_url)
        with tempfile.TemporaryDirectory() as directory:
            processed_dir = Path(directory)
            write_processed_fixture(processed_dir)
            load_processed_data(cls.owner_engine, processed_dir)

        cls.reader_user = f"banking_reader_test_{uuid4().hex}"
        cls.reader_password = f"test-{uuid4().hex}"
        provision_reader_role(cls.owner_url, cls.reader_user, cls.reader_password)
        cls.reader_url = make_url(cls.owner_url).set(
            username=cls.reader_user,
            password=cls.reader_password,
        )
        cls.reader_engine = create_engine(cls.reader_url)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.reader_engine.dispose()
        with psycopg.connect(
            _psycopg_connection_string(cls.owner_url), autocommit=True
        ) as connection:
            role = sql.Identifier(cls.reader_user)
            connection.execute(sql.SQL("DROP OWNED BY {}").format(role))
            connection.execute(sql.SQL("DROP ROLE {}").format(role))
        cls.owner_engine.dispose()
        cls.environment.stop()
        cls.database_context.__exit__(None, None, None)

    def test_reader_can_select_and_join_approved_tables(self) -> None:
        with self.reader_engine.connect() as connection:
            count = connection.scalar(text("SELECT count(*) FROM banking.accounts"))
            joined = connection.scalar(
                text(
                    "SELECT count(*) FROM banking.customers c "
                    "JOIN banking.accounts a ON a.customer_id = c.customer_id"
                )
            )
        self.assertEqual(count, 1)
        self.assertEqual(joined, 1)

    def test_reader_does_not_own_tables_or_have_elevated_role_flags(self) -> None:
        with self.owner_engine.connect() as connection:
            role_flags = connection.execute(
                text(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, "
                    "rolbypassrls FROM pg_roles WHERE rolname = :role"
                ),
                {"role": self.reader_user},
            ).one()
            owned_tables = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_tables "
                    "WHERE schemaname = 'banking' AND tableowner = :role"
                ),
                {"role": self.reader_user},
            )
        self.assertEqual(tuple(role_flags), (False, False, False, False, False))
        self.assertEqual(owned_tables, 0)

    def test_postgresql_denies_mutation_ddl_and_role_management(self) -> None:
        denied_statements = (
            "INSERT INTO banking.addresses (address_id) VALUES (2)",
            "UPDATE banking.addresses SET city = 'Changed' WHERE address_id = 1",
            "DELETE FROM banking.addresses WHERE address_id = 1",
            "TRUNCATE banking.transactions",
            "CREATE TABLE banking.forbidden_create (id integer)",
            "ALTER TABLE banking.addresses ADD COLUMN forbidden integer",
            "DROP TABLE banking.transactions",
            "CREATE ROLE forbidden_role",
        )
        for statement in denied_statements:
            with self.subTest(statement=statement):
                with self.assertRaises(DBAPIError), self.reader_engine.begin() as connection:
                    connection.execute(text(statement))

        with self.owner_engine.connect() as connection:
            self.assertEqual(
                connection.scalar(text("SELECT count(*) FROM banking.addresses")), 1
            )
            self.assertEqual(
                connection.scalar(text("SELECT count(*) FROM banking.transactions")), 1
            )

    def test_owner_role_cannot_be_reused_as_reader(self) -> None:
        owner_user = make_url(self.owner_url).username
        with self.assertRaisesRegex(RoleConfigurationError, "must differ"):
            provision_reader_role(self.owner_url, owner_user, "irrelevant-test-value")
