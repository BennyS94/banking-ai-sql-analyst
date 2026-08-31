from __future__ import annotations

import os
from pathlib import Path
import tempfile
from unittest import IsolatedAsyncioTestCase, TestCase, mock
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
    GroqRateLimitError,
    GroqTimeoutError,
    InvalidStructuredResponseError,
    StructuredGeneration,
)
from backend.app.ai.service import NLToSQLGenerationResult, QuestionValidationError
from backend.app.api.dependencies import get_safe_query_service
from backend.app.core.config import Settings
from backend.app.db.engine import create_runtime_engine
from backend.app.db.query_executor import (
    QueryDatabaseError,
    QueryExecutionError,
    QueryResult,
    QueryTimeoutError,
    ReadOnlyQueryExecutor,
)
from backend.app.db.schema import ColumnSchema, DatabaseSchema, TableSchema
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


GENERATION_METADATA = GenerationMetadata(
    model="openai/gpt-oss-120b",
    reasoning_effort="medium",
    latency_ms=12.5,
    provider_request_id="request-1",
    input_tokens=100,
    output_tokens=25,
    finish_reason="stop",
)


def _table(name: str, *columns: str) -> TableSchema:
    return TableSchema(
        name=name,
        columns=tuple(
            ColumnSchema(name=column, data_type="text", nullable=True)
            for column in columns
        ),
        primary_key=(),
        foreign_keys=(),
    )


QUERY_SCHEMA = DatabaseSchema(
    schema_name="banking",
    tables=(
        _table("accounts", "account_id", "customer_id", "balance"),
        _table("customers", "customer_id", "first_name"),
    ),
)


def _generation(
    status: str = "answerable",
    *,
    sql: str | None = "SELECT account_id, balance FROM banking.accounts",
) -> NLToSQLGenerationResult:
    if status == "answerable":
        output = StructuredGeneration(
            status="answerable", sql=sql, message=None
        )
    else:
        output = StructuredGeneration(
            status=status,  # type: ignore[arg-type]
            sql=None,
            message="Please clarify the banking question",
        )
    return NLToSQLGenerationResult(
        output=output,
        metadata=GENERATION_METADATA,
    )


def _query_result(*, truncated: bool = False) -> QueryResult:
    return QueryResult(
        columns=("account_id", "balance"),
        rows=((1, "10.25"),),
        row_count=1,
        truncated=truncated,
        execution_ms=4.5,
        statement_timeout_ms=5_000,
    )


class QueryApiTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.generation_service = mock.Mock()
        self.generation_service.generate.return_value = _generation()
        self.executor = mock.Mock()
        self.executor.execute.return_value = _query_result()
        self.service = SafeQueryService(
            self.generation_service,
            SQLASTValidator(),
            BankingSQLAccessPolicy(QUERY_SCHEMA),
            self.executor,
        )
        self.application = create_app(Settings(_env_file=None))
        self.application.dependency_overrides[get_safe_query_service] = (
            lambda: self.service
        )
        self.transport = ASGITransport(app=self.application)

    async def _post(self, payload: dict[str, object]):
        async with AsyncClient(
            transport=self.transport, base_url="http://test"
        ) as client:
            return await client.post("/api/v1/query", json=payload)

    async def test_answerable_query_runs_complete_safety_path(self) -> None:
        response = await self._post({"question": "List account balances"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "answerable")
        self.assertEqual(
            payload["sql"], "SELECT account_id, balance FROM banking.accounts"
        )
        self.assertIsNone(payload["message"])
        self.assertEqual(payload["columns"], ["account_id", "balance"])
        self.assertEqual(payload["rows"], [[1, "10.25"]])
        self.assertEqual(payload["returned_row_count"], 1)
        self.assertFalse(payload["truncated"])
        self.assertEqual(payload["generation"]["model"], "openai/gpt-oss-120b")
        self.assertEqual(
            payload["execution"],
            {"execution_ms": 4.5, "statement_timeout_ms": 5_000},
        )
        self.generation_service.generate.assert_called_once_with(
            "List account balances"
        )
        self.executor.execute.assert_called_once_with(
            "SELECT account_id, balance FROM banking.accounts"
        )

    async def test_allowed_cte_and_truncation_are_serialized(self) -> None:
        cte_sql = (
            "WITH totals AS (SELECT customer_id, SUM(balance) AS total "
            "FROM banking.accounts GROUP BY customer_id) "
            "SELECT customer_id, total FROM totals"
        )
        self.generation_service.generate.return_value = _generation(sql=cte_sql)
        self.executor.execute.return_value = _query_result(truncated=True)

        response = await self._post({"question": "Totals by customer"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sql"], cte_sql)
        self.assertTrue(response.json()["truncated"])
        self.executor.execute.assert_called_once_with(cte_sql)

    async def test_non_answerable_statuses_never_execute_sql(self) -> None:
        for semantic_status in ("unanswerable", "ambiguous"):
            with self.subTest(status=semantic_status):
                self.generation_service.generate.return_value = _generation(
                    semantic_status, sql=None
                )
                self.executor.reset_mock()

                response = await self._post({"question": "Question"})

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["status"], semantic_status)
                self.assertIsNone(payload["sql"])
                self.assertEqual(payload["columns"], [])
                self.assertEqual(payload["rows"], [])
                self.assertEqual(payload["returned_row_count"], 0)
                self.assertIsNone(payload["execution"])
                self.executor.execute.assert_not_called()

    async def test_unsafe_and_unknown_generated_sql_never_executes(self) -> None:
        cases = (
            "DELETE FROM banking.accounts",
            "SELECT * FROM banking.secrets",
            "SELECT secret_value FROM banking.accounts",
        )
        for generated_sql in cases:
            with self.subTest(sql=generated_sql):
                self.generation_service.generate.return_value = _generation(
                    sql=generated_sql
                )
                self.executor.reset_mock()

                response = await self._post({"question": "Unsafe request"})

                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.json()["detail"]["category"], "safety_rejection"
                )
                self.assertNotIn("secret_value", response.text)
                self.executor.execute.assert_not_called()

    async def test_provider_and_protocol_failures_remain_distinct(self) -> None:
        cases = (
            (
                GroqTimeoutError("sensitive timeout detail"),
                504,
                "provider_timeout",
            ),
            (
                GroqRateLimitError("sensitive limit detail"),
                503,
                "provider_rate_limit",
            ),
            (
                InvalidStructuredResponseError("sensitive protocol detail"),
                502,
                "invalid_generation_protocol",
            ),
        )
        for error, expected_status, category in cases:
            with self.subTest(category=category):
                self.generation_service.generate.side_effect = error

                response = await self._post({"question": "Question"})

                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.json()["detail"]["category"], category)
                self.assertNotIn("sensitive", response.text)

    async def test_database_failure_categories_remain_distinct(self) -> None:
        cases = (
            (QueryTimeoutError("sensitive"), 504, "database_timeout"),
            (
                QueryExecutionError("sensitive", repair_detail="safe detail"),
                422,
                "query_execution_error",
            ),
            (QueryDatabaseError("sensitive"), 503, "database_failure"),
        )
        for error, expected_status, category in cases:
            with self.subTest(category=category):
                self.generation_service.generate.side_effect = None
                self.generation_service.generate.return_value = _generation()
                self.executor.execute.side_effect = error

                response = await self._post({"question": "Question"})

                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.json()["detail"]["category"], category)
                self.assertNotIn("sensitive", response.text)

    async def test_question_validation_and_raw_sql_input_are_rejected(self) -> None:
        self.generation_service.generate.side_effect = QuestionValidationError(
            "Question must be non-empty"
        )
        invalid_question = await self._post({"question": ""})
        raw_sql = await self._post(
            {"question": "Count accounts", "sql": "DELETE FROM banking.accounts"}
        )

        self.assertEqual(invalid_question.status_code, 422)
        self.assertEqual(
            invalid_question.json()["detail"]["category"], "invalid_question"
        )
        self.assertEqual(raw_sql.status_code, 422)
        self.assertIn("extra_forbidden", raw_sql.text)


class QueryDependencyTests(TestCase):
    def test_composition_passes_resource_settings_to_hardened_executor(self) -> None:
        settings = Settings(
            groq_api_key="test-key",
            query_statement_timeout_ms=321,
            query_max_rows=17,
            _env_file=None,
        )
        engine = mock.sentinel.engine
        with (
            mock.patch(
                "backend.app.api.dependencies.GroqStructuredGenerationClient"
            ),
            mock.patch(
                "backend.app.api.dependencies.BankingSQLAccessPolicy.from_engine"
            ),
            mock.patch(
                "backend.app.api.dependencies.ReadOnlyQueryExecutor"
            ) as executor,
        ):
            service = get_safe_query_service(settings, engine)

        self.assertIsInstance(service, SafeQueryService)
        executor.assert_called_once_with(
            engine,
            statement_timeout_ms=321,
            max_rows=17,
        )


class QueryApiPostgresIntegrationTests(IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_context = temporary_database()
        cls.owner_url = cls.database_context.__enter__()
        cls.environment = mock.patch.dict(
            os.environ, {"DATABASE_URL": cls.owner_url}
        )
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
        provision_reader_role(
            cls.owner_url, cls.reader_user, cls.reader_password
        )
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

    async def test_answerable_request_executes_through_real_safety_boundary(self) -> None:
        generation_service = mock.Mock()
        generated_sql = (
            "SELECT account_id, balance FROM banking.accounts "
            "ORDER BY account_id"
        )
        generation_service.generate.return_value = _generation(sql=generated_sql)
        service = SafeQueryService(
            generation_service,
            SQLASTValidator(),
            BankingSQLAccessPolicy.from_engine(self.engine),
            ReadOnlyQueryExecutor(self.engine),
        )
        application = create_app(Settings(_env_file=None))
        application.dependency_overrides[get_safe_query_service] = lambda: service

        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/query", json={"question": "List account balances"}
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["sql"], generated_sql)
        self.assertEqual(payload["rows"], [[1, "-10.25"]])
        self.assertEqual(payload["returned_row_count"], 1)
        self.assertFalse(payload["truncated"])
        self.assertEqual(payload["execution"]["statement_timeout_ms"], 5_000)
