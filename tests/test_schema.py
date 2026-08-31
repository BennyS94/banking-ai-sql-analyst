from __future__ import annotations

import os
from unittest import TestCase, mock

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from database_test_support import temporary_database


EXPECTED_TABLES = {
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

EXPECTED_NON_UNIQUE_INDEXES = {
    "ix_customers_address_id",
    "ix_accounts_customer_id",
    "ix_loans_account_id",
    "ix_transactions_account_origin_id_transaction_date",
    "ix_transactions_account_destination_id_transaction_date",
    "ix_transactions_transaction_date",
    "ix_transactions_branch_id",
}


class BankingSchemaIntegrationTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_context = temporary_database()
        cls.database_url = cls.database_context.__enter__()
        cls.environment = mock.patch.dict(os.environ, {"DATABASE_URL": cls.database_url})
        cls.environment.start()
        cls.alembic_config = Config("alembic.ini")
        command.upgrade(cls.alembic_config, "head")
        cls.engine = create_engine(cls.database_url)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()
        cls.environment.stop()
        cls.database_context.__exit__(None, None, None)

    def test_approved_tables_foreign_keys_and_indexes_exist(self) -> None:
        inspector = inspect(self.engine)
        self.assertEqual(set(inspector.get_table_names(schema="banking")), EXPECTED_TABLES)

        foreign_key_count = sum(
            len(inspector.get_foreign_keys(table, schema="banking"))
            for table in EXPECTED_TABLES
        )
        self.assertEqual(foreign_key_count, 12)

        indexes = {
            index["name"]
            for table in EXPECTED_TABLES
            for index in inspector.get_indexes(table, schema="banking")
            if not index.get("unique")
        }
        self.assertEqual(indexes, EXPECTED_NON_UNIQUE_INDEXES)

    def test_database_enforces_key_check_and_unique_constraints(self) -> None:
        with self.assertRaises(IntegrityError), self.engine.begin() as connection:
            connection.execute(
                text("INSERT INTO banking.addresses (address_id) VALUES (-1)")
            )

        with self.assertRaises(IntegrityError), self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO banking.branches "
                    "(branch_id, address_id) VALUES (1, 999)"
                )
            )

        with self.assertRaises(IntegrityError), self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO banking.account_statuses "
                    "(account_status_id, status_name) VALUES (1, 'Active'), (2, 'Active')"
                )
            )

    def test_migration_can_downgrade_and_rebuild_from_empty(self) -> None:
        command.downgrade(self.alembic_config, "base")
        self.assertNotIn("banking", inspect(self.engine).get_schema_names())
        command.upgrade(self.alembic_config, "head")
        self.assertEqual(
            set(inspect(self.engine).get_table_names(schema="banking")), EXPECTED_TABLES
        )
