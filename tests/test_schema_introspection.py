from __future__ import annotations

import os
from unittest import TestCase, mock
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from backend.app.core.config import Settings
from backend.app.db.engine import create_runtime_engine
from backend.app.db.schema import introspect_banking_schema
from banking_data.role_management import (
    _psycopg_connection_string,
    provision_reader_role,
)
from database_test_support import temporary_database


class BankingSchemaIntrospectionTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_context = temporary_database()
        cls.owner_url = cls.database_context.__enter__()
        cls.environment = mock.patch.dict(os.environ, {"DATABASE_URL": cls.owner_url})
        cls.environment.start()
        command.upgrade(Config("alembic.ini"), "head")

        cls.reader_user = f"banking_reader_test_{uuid4().hex}"
        cls.reader_password = f"test-{uuid4().hex}"
        provision_reader_role(cls.owner_url, cls.reader_user, cls.reader_password)
        reader_url = make_url(cls.owner_url).set(
            username=cls.reader_user,
            password=cls.reader_password,
        )
        cls.engine = create_runtime_engine(
            Settings(
                banking_reader_user=cls.reader_user,
                banking_reader_database_url=reader_url.render_as_string(
                    hide_password=False
                ),
            )
        )
        cls.schema = introspect_banking_schema(cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()
        with psycopg.connect(
            _psycopg_connection_string(cls.owner_url), autocommit=True
        ) as connection:
            role = sql.Identifier(cls.reader_user)
            connection.execute(sql.SQL("DROP OWNED BY {}").format(role))
            connection.execute(sql.SQL("DROP ROLE {}").format(role))
        cls.environment.stop()
        cls.database_context.__exit__(None, None, None)

    def test_discovers_only_expected_banking_tables(self) -> None:
        expected_tables = {
            "account_statuses",
            "account_types",
            "accounts",
            "addresses",
            "branches",
            "customer_types",
            "customers",
            "loan_statuses",
            "loans",
            "transaction_types",
            "transactions",
        }

        discovered_tables = {table.name for table in self.schema.tables}
        self.assertEqual(self.schema.schema_name, "banking")
        self.assertEqual(discovered_tables, expected_tables)
        self.assertNotIn("alembic_version", discovered_tables)

    def test_column_and_primary_key_metadata_match_known_invariants(self) -> None:
        accounts = self._table("accounts")
        columns = {column.name: column for column in accounts.columns}

        self.assertEqual(accounts.primary_key, ("account_id",))
        self.assertEqual(columns["balance"].data_type, "numeric(14, 2)")
        self.assertFalse(columns["balance"].nullable)
        self.assertEqual(columns["opening_date"].data_type, "date")
        self.assertTrue(columns["opening_date"].nullable)

        transaction_columns = {
            column.name: column for column in self._table("transactions").columns
        }
        self.assertEqual(
            transaction_columns["transaction_date"].data_type,
            "timestamp without time zone",
        )

    def test_foreign_key_metadata_represents_important_relationships(self) -> None:
        transactions = self._table("transactions")
        relationships = {
            (
                foreign_key.columns,
                foreign_key.referenced_schema,
                foreign_key.referenced_table,
                foreign_key.referenced_columns,
            )
            for foreign_key in transactions.foreign_keys
        }

        self.assertIn(
            (("account_origin_id",), "banking", "accounts", ("account_id",)),
            relationships,
        )
        self.assertIn(
            (("branch_id",), "banking", "branches", ("branch_id",)),
            relationships,
        )
        self.assertEqual(len(relationships), 4)

    def test_metadata_ordering_is_deterministic(self) -> None:
        second_result = introspect_banking_schema(self.engine)

        self.assertEqual(second_result, self.schema)
        self.assertEqual(
            [table.name for table in self.schema.tables],
            sorted(table.name for table in self.schema.tables),
        )
        for table in self.schema.tables:
            self.assertEqual(
                list(table.foreign_keys),
                sorted(
                    table.foreign_keys,
                    key=lambda foreign_key: (
                        foreign_key.columns,
                        foreign_key.referenced_schema,
                        foreign_key.referenced_table,
                        foreign_key.referenced_columns,
                    ),
                ),
            )

    def _table(self, name: str):
        return next(table for table in self.schema.tables if table.name == name)
