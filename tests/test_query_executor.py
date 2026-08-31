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
from sqlalchemy.exc import OperationalError

from backend.app.core.config import Settings
from backend.app.db.engine import create_runtime_engine
from backend.app.db.query_executor import (
    QueryDatabaseError,
    QueryExecutionError,
    QueryTimeoutError,
    ReadOnlyQueryExecutor,
)
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
        self.assertFalse(result.truncated)
        self.assertEqual(result.statement_timeout_ms, 5_000)
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
        self.assertFalse(result.truncated)

    def test_result_fetching_is_bounded_and_reports_truncation(self) -> None:
        two_rows = ReadOnlyQueryExecutor(self.engine, max_rows=2).execute(
            "SELECT value FROM (VALUES (1), (2)) AS items(value) ORDER BY value"
        )
        three_rows = ReadOnlyQueryExecutor(self.engine, max_rows=2).execute(
            "SELECT value FROM (VALUES (1), (2), (3)) AS items(value) ORDER BY value"
        )

        self.assertEqual(two_rows.rows, ((1,), (2,)))
        self.assertEqual(two_rows.row_count, 2)
        self.assertFalse(two_rows.truncated)
        self.assertEqual(three_rows.rows, ((1,), (2,)))
        self.assertEqual(three_rows.row_count, 2)
        self.assertTrue(three_rows.truncated)

    def test_transaction_is_read_only_and_runtime_settings_are_local(self) -> None:
        executor = ReadOnlyQueryExecutor(
            self.engine, statement_timeout_ms=1_234
        )
        result = executor.execute(
            "SELECT current_setting('transaction_read_only'), "
            "current_setting('statement_timeout'), current_setting('search_path')"
        )

        self.assertEqual(
            result.rows,
            (("on", "1234ms", "banking, pg_catalog, pg_temp"),),
        )
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(text("SHOW transaction_read_only")), "off"
            )
            self.assertNotEqual(
                connection.scalar(text("SHOW statement_timeout")), "1234ms"
            )

    def test_statement_timeout_is_controlled_and_connection_recovers(self) -> None:
        executor = ReadOnlyQueryExecutor(self.engine, statement_timeout_ms=10)
        workload = (
            "WITH RECURSIVE workload(n) AS ("
            "SELECT 1 UNION ALL SELECT n + 1 FROM workload WHERE n < 100000000"
            ") SELECT SUM(n) FROM workload"
        )

        with mock.patch("backend.app.db.query_executor.logger.exception"):
            with self.assertRaises(QueryTimeoutError) as caught:
                executor.execute(workload)

        self.assertEqual(str(caught.exception), "Database query timed out")
        self.assertEqual(self.engine.pool.checkedout(), 0)
        self.assertEqual(executor.execute("SELECT 1").rows, ((1,),))

    def test_direct_mutation_bypass_is_denied_and_data_is_unchanged(self) -> None:
        with mock.patch("backend.app.db.query_executor.logger.exception"):
            with self.assertRaises(QueryExecutionError) as caught:
                self.executor.execute(
                    "UPDATE banking.accounts SET balance = 0 WHERE account_id = 1"
                )

        self.assertFalse(caught.exception.repair_eligible)
        result = self.executor.execute(
            "SELECT balance FROM banking.accounts WHERE account_id = 1"
        )
        self.assertEqual(result.rows, (("-10.25",),))

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
        self.assertIn("unavailable_column", caught.exception.repair_detail)
        self.assertTrue(caught.exception.repair_eligible)
        self.assertEqual(self.engine.pool.checkedout(), 0)

    def test_connectivity_failure_has_a_distinct_sanitized_error(self) -> None:
        engine = mock.Mock()
        engine.connect.side_effect = OperationalError(
            "connect failed with secret", {}, Exception("driver secret")
        )
        executor = ReadOnlyQueryExecutor(engine)

        with mock.patch("backend.app.db.query_executor.logger.exception"):
            with self.assertRaises(QueryDatabaseError) as caught:
                executor.execute("SELECT 1")

        self.assertEqual(
            str(caught.exception), "Database query infrastructure failed"
        )
        self.assertNotIn("secret", str(caught.exception))

    def test_executor_configuration_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            ReadOnlyQueryExecutor(self.engine, statement_timeout_ms=0)
        with self.assertRaises(ValueError):
            ReadOnlyQueryExecutor(self.engine, max_rows=0)
