from __future__ import annotations

import os
from pathlib import Path
import tempfile
from unittest import IsolatedAsyncioTestCase, mock
from uuid import uuid4

from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
import psycopg
from psycopg import sql as psycopg_sql
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from backend.app.ai.groq_client import (
    GenerationMetadata,
    GroqTimeoutError,
    GroqUnavailableError,
    InvalidStructuredResponseError,
    StructuredGeneration,
)
from backend.app.ai.service import NLToSQLGenerationResult
from backend.app.api.dependencies import get_safe_query_service
from backend.app.core.config import Settings
from backend.app.db.engine import create_runtime_engine
from backend.app.db.query_executor import (
    QueryDatabaseError,
    QueryTimeoutError,
    ReadOnlyQueryExecutor,
)
from backend.app.main import create_app
from backend.app.query.service import SafeQueryService
from backend.app.safety.access_policy import BankingSQLAccessPolicy
from backend.app.safety.sql_validator import SQLASTValidator
from banking_data.loading import load_processed_data
from banking_data.role_management import (
    _psycopg_connection_string,
    provision_reader_role,
)
from database_test_support import temporary_database
from test_loading import write_processed_fixture


_METADATA = GenerationMetadata(
    model="fake-regression-model",
    reasoning_effort="medium",
    latency_ms=1,
    input_tokens=10,
    output_tokens=5,
)


def _generation(
    sql: str | None = None,
    *,
    status: str = "answerable",
) -> NLToSQLGenerationResult:
    if status == "answerable":
        output = StructuredGeneration(status="answerable", sql=sql, message=None)
    else:
        output = StructuredGeneration(
            status=status,  # type: ignore[arg-type]
            sql=None,
            message="The question requires clarification",
        )
    return NLToSQLGenerationResult(output=output, metadata=_METADATA)


class EndToEndQueryRegressionTests(IsolatedAsyncioTestCase):
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
        cls.reader_user = f"banking_regression_reader_{uuid4().hex}"
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
                _env_file=None,
            )
        )
        cls.policy = BankingSQLAccessPolicy.from_engine(cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()
        with psycopg.connect(
            _psycopg_connection_string(cls.owner_url), autocommit=True
        ) as connection:
            role = psycopg_sql.Identifier(cls.reader_user)
            connection.execute(psycopg_sql.SQL("DROP OWNED BY {}").format(role))
            connection.execute(psycopg_sql.SQL("DROP ROLE {}").format(role))
        cls.environment.stop()
        cls.database_context.__exit__(None, None, None)

    async def _post(
        self,
        generation_service: mock.Mock,
        question: str,
        *,
        executor: object | None = None,
    ):
        service = SafeQueryService(
            generation_service,
            SQLASTValidator(),
            self.policy,
            executor or ReadOnlyQueryExecutor(self.engine),
        )
        application = create_app(Settings(_env_file=None))
        application.dependency_overrides[get_safe_query_service] = lambda: service
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            return await client.post(
                "/api/v1/query", json={"question": question}
            )

    async def test_successful_analytical_shapes_use_real_safety_and_postgres(self) -> None:
        statements = {
            "simple": "SELECT account_id, balance FROM banking.accounts",
            "join": (
                "SELECT c.customer_id, a.account_id FROM banking.customers AS c "
                "JOIN banking.accounts AS a ON a.customer_id = c.customer_id"
            ),
            "aggregation": "SELECT COUNT(*) AS account_count FROM banking.accounts",
            "cte": (
                "WITH balances AS (SELECT customer_id, SUM(balance) AS total_balance "
                "FROM banking.accounts GROUP BY customer_id) "
                "SELECT customer_id, total_balance FROM balances"
            ),
            "window": (
                "SELECT account_id, ROW_NUMBER() OVER (ORDER BY balance DESC) "
                "AS balance_rank FROM banking.accounts"
            ),
        }
        for shape, statement in statements.items():
            with self.subTest(shape=shape):
                generation = mock.Mock()
                generation.generate.return_value = _generation(statement)

                response = await self._post(generation, f"Run {shape} analytics")

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["sql"], statement)
                self.assertEqual(response.json()["returned_row_count"], 1)

    async def test_romanian_and_semantic_status_paths_never_depend_on_live_provider(self) -> None:
        romanian = "Arată soldurile conturilor."
        generation = mock.Mock()
        generation.generate.return_value = _generation(
            "SELECT account_id, balance FROM banking.accounts"
        )
        response = await self._post(generation, romanian)
        self.assertEqual(response.status_code, 200)
        generation.generate.assert_called_once_with(romanian)

        for status in ("unanswerable", "ambiguous"):
            with self.subTest(status=status):
                semantic_generation = mock.Mock()
                semantic_generation.generate.return_value = _generation(status=status)
                executor = mock.Mock(wraps=ReadOnlyQueryExecutor(self.engine))

                semantic_response = await self._post(
                    semantic_generation, "Question", executor=executor
                )

                self.assertEqual(semantic_response.status_code, 200)
                self.assertEqual(semantic_response.json()["status"], status)
                executor.execute.assert_not_called()

    async def test_unsafe_sql_variants_and_reviewed_row_locks_never_execute(self) -> None:
        statements = (
            "DELETE FROM banking.accounts",
            "SELECT * FROM public.accounts",
            "SELECT pg_sleep(1)",
            "SELECT account_id FROM banking.accounts; SELECT 1",
            "SELECT account_id FROM banking.accounts FOR UPDATE",
        )
        for statement in statements:
            with self.subTest(statement=statement):
                generation = mock.Mock()
                generation.generate.return_value = _generation(statement)
                executor = mock.Mock(wraps=ReadOnlyQueryExecutor(self.engine))

                response = await self._post(
                    generation, "Unsafe request", executor=executor
                )

                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.json()["detail"]["category"], "safety_rejection"
                )
                executor.execute.assert_not_called()

    async def test_zero_rows_truncation_and_typed_serialization_are_preserved(self) -> None:
        generation = mock.Mock()
        generation.generate.return_value = _generation(
            "SELECT customer_id, first_name FROM banking.customers WHERE false"
        )
        zero = await self._post(generation, "Return no customers")
        self.assertEqual(zero.status_code, 200)
        self.assertEqual(zero.json()["columns"], ["customer_id", "first_name"])
        self.assertEqual(zero.json()["rows"], [])

        repeated = (
            "SELECT account_id FROM banking.accounts "
            "UNION ALL SELECT account_id FROM banking.accounts "
            "UNION ALL SELECT account_id FROM banking.accounts"
        )
        generation.generate.return_value = _generation(repeated)
        truncated = await self._post(
            generation,
            "Return repeated accounts",
            executor=ReadOnlyQueryExecutor(self.engine, max_rows=2),
        )
        self.assertEqual(truncated.status_code, 200)
        self.assertEqual(truncated.json()["rows"], [[1], [1]])
        self.assertTrue(truncated.json()["truncated"])

        typed_sql = (
            "SELECT a.balance, a.opening_date, tr.transaction_date, "
            "NULL::text AS missing FROM banking.accounts AS a "
            "JOIN banking.transactions AS tr "
            "ON tr.account_origin_id = a.account_id"
        )
        generation.generate.return_value = _generation(typed_sql)
        typed = await self._post(generation, "Return typed values")
        self.assertEqual(typed.status_code, 200)
        self.assertEqual(
            typed.json()["rows"],
            [["-10.25", "2020-01-03", "2023-01-01T12:30:00", None]],
        )

    async def test_repair_failures_revalidate_and_never_attempt_a_second_repair(self) -> None:
        initial = (
            "SELECT balance + opening_date AS invalid_value "
            "FROM banking.accounts"
        )
        repaired_sql = "SELECT account_id, balance FROM banking.accounts"
        successful_generation = mock.Mock()
        successful_generation.generate.return_value = _generation(initial)
        successful_generation.repair.return_value = _generation(repaired_sql)
        successful_executor = mock.Mock(wraps=ReadOnlyQueryExecutor(self.engine))
        successful = await self._post(
            successful_generation, "Repair successfully", executor=successful_executor
        )
        self.assertEqual(successful.status_code, 200)
        self.assertTrue(successful.json()["repair_used"])
        self.assertEqual(successful.json()["sql"], repaired_sql)
        successful_generation.repair.assert_called_once()
        self.assertEqual(successful_executor.execute.call_count, 2)

        cases = (
            (_generation(status="unanswerable"), "query_repair_failed", 1),
            (_generation("DELETE FROM banking.accounts"), "safety_rejection", 1),
            (_generation(initial), "query_execution_error", 2),
        )
        for repaired, category, execution_count in cases:
            with self.subTest(category=category):
                generation = mock.Mock()
                generation.generate.return_value = _generation(initial)
                generation.repair.return_value = repaired
                executor = mock.Mock(wraps=ReadOnlyQueryExecutor(self.engine))

                response = await self._post(
                    generation, "Repair the query", executor=executor
                )

                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["detail"]["category"], category)
                generation.repair.assert_called_once()
                self.assertEqual(executor.execute.call_count, execution_count)

    async def test_provider_protocol_timeout_and_database_failures_are_sanitized(self) -> None:
        generation_failures = (
            (GroqTimeoutError("secret"), "provider_timeout", 504),
            (GroqUnavailableError("secret"), "provider_unavailable", 503),
            (
                InvalidStructuredResponseError("secret"),
                "invalid_generation_protocol",
                502,
            ),
        )
        for error, category, status_code in generation_failures:
            with self.subTest(category=category):
                generation = mock.Mock()
                generation.generate.side_effect = error
                response = await self._post(generation, "Question")
                self.assertEqual(response.status_code, status_code)
                self.assertEqual(response.json()["detail"]["category"], category)
                self.assertNotIn("secret", response.text)

        generation = mock.Mock()
        generation.generate.return_value = _generation(
            "SELECT account_id FROM banking.accounts"
        )
        database_executor = mock.Mock()
        database_executor.execute.side_effect = QueryDatabaseError("secret")
        database = await self._post(
            generation, "Question", executor=database_executor
        )
        self.assertEqual(database.status_code, 503)
        self.assertEqual(database.json()["detail"]["category"], "database_failure")
        self.assertNotIn("secret", database.text)

        timeout_executor = mock.Mock()
        timeout_executor.execute.side_effect = QueryTimeoutError("secret")
        timeout = await self._post(
            generation, "Question", executor=timeout_executor
        )
        self.assertEqual(timeout.status_code, 504)
        self.assertEqual(timeout.json()["detail"]["category"], "database_timeout")
        self.assertNotIn("secret", timeout.text)
