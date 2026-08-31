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
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from backend.app.core.config import Settings
from backend.app.db.engine import create_runtime_engine
from backend.app.db.query_executor import QueryExecutionError, ReadOnlyQueryExecutor
from banking_data.loading import load_processed_data
from banking_data.role_management import (
    _psycopg_connection_string,
    provision_reader_role,
)
from database_test_support import temporary_database
from test_loading import write_processed_fixture


class ReadOnlyQueryExecutorTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_context = temporary_database()
        cls.owner_url = cls.database_context.__enter__()
        cls.environment = mock.patch.dict(os.environ, {"DATABASE_URL": cls.owner_url})
        cls.environment.start()
        command.upgrade(Config("alembic.ini"), "head")
        with tempfile.TemporaryDirectory() as directory:
            processed_dir = Path(directory)
            write_processed_fixture(processed_dir)
            owner_engine = create_engine(cls.owner_url)
            try:
                load_processed_data(owner_engine, processed_dir)
            finally:
                owner_engine.dispose()

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
        cls.executor = ReadOnlyQueryExecutor(cls.engine)

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

    def test_simple_select_preserves_column_order_and_normalizes_values(self) -> None:
        result = self.executor.execute(
            "SELECT account_id, balance, opening_date "
            "FROM banking.accounts ORDER BY account_id"
        )

        self.assertEqual(result.columns, ("account_id", "balance", "opening_date"))
        self.assertEqual(result.rows, ((1, "-10.25", "2020-01-03"),))
        self.assertEqual(result.row_count, 1)
        self.assertGreaterEqual(result.execution_ms, 0.0)
        self.assertEqual(
            result.model_dump(mode="json")["rows"],
            [[1, "-10.25", "2020-01-03"]],
        )

    def test_join_and_aggregation_queries_return_expected_results(self) -> None:
        joined = self.executor.execute(
            "SELECT c.first_name, at.type_name "
            "FROM banking.customers AS c "
            "JOIN banking.accounts AS a ON a.customer_id = c.customer_id "
            "JOIN banking.account_types AS at "
            "ON at.account_type_id = a.account_type_id"
        )
        aggregated = self.executor.execute(
            "SELECT count(*) AS transaction_count, sum(amount) AS total_amount "
            "FROM banking.transactions"
        )

        self.assertEqual(joined.rows, (("Ana", "Checking"),))
        self.assertEqual(aggregated.columns, ("transaction_count", "total_amount"))
        self.assertEqual(aggregated.rows, ((1, "5.00"),))

    def test_null_boolean_timestamp_and_string_values_are_normalized(self) -> None:
        result = self.executor.execute(
            "SELECT transaction_date, description, NULL::text AS missing, "
            "true AS approved FROM banking.transactions"
        )

        self.assertEqual(
            result.rows,
            (("2023-01-01T12:30:00", "Test", None, True),),
        )

    def test_zero_row_result_retains_column_metadata(self) -> None:
        result = self.executor.execute(
            "SELECT customer_id, first_name FROM banking.customers WHERE false"
        )

        self.assertEqual(result.columns, ("customer_id", "first_name"))
        self.assertEqual(result.rows, ())
        self.assertEqual(result.row_count, 0)

    def test_executor_uses_reader_identity_and_releases_connection(self) -> None:
        self.assertEqual(self.engine.pool.checkedout(), 0)
        result = self.executor.execute("SELECT current_user AS database_user")

        self.assertEqual(result.rows, ((self.reader_user,),))
        self.assertEqual(self.engine.pool.checkedout(), 0)

    def test_postgresql_error_is_sanitized_and_connection_is_released(self) -> None:
        with mock.patch("backend.app.db.query_executor.logger.exception"):
            with self.assertRaises(QueryExecutionError) as caught:
                self.executor.execute(
                    "SELECT unavailable_column FROM banking.customers"
                )

        self.assertEqual(str(caught.exception), "Database query execution failed")
        self.assertNotIn("unavailable_column", str(caught.exception))
        self.assertEqual(self.engine.pool.checkedout(), 0)
