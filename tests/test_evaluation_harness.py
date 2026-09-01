from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from backend.app.ai.benchmark import BenchmarkCase
from backend.app.ai.groq_client import (
    GenerationMetadata,
    StructuredGeneration,
)
from backend.app.ai.service import NLToSQLGenerationResult
from backend.app.db.query_executor import QueryExecutionError, QueryResult
from backend.app.core.config import Settings
from backend.app.db.engine import create_runtime_engine
from backend.app.db.query_executor import ReadOnlyQueryExecutor
from backend.app.evaluation.comparison import (
    compare_query_results,
    normalize_value,
)
from backend.app.evaluation.models import EvaluationCaseResult
from backend.app.evaluation.persistence import (
    EvaluationPersistenceError,
    EvaluationRunStore,
)
from backend.app.evaluation.runner import EvaluationRunner, build_run_metadata
from backend.app.safety.sql_validator import SQLASTValidator
from backend.app.safety.access_policy import BankingSQLAccessPolicy
from banking_data.role_management import (
    _psycopg_connection_string,
    provision_reader_role,
)
from database_test_support import temporary_database


def _query_result(*rows: tuple[object, ...]) -> QueryResult:
    return QueryResult(
        columns=tuple(f"column_{index}" for index in range(len(rows[0]) if rows else 1)),
        rows=rows,
        row_count=len(rows),
        truncated=False,
        execution_ms=2.5,
        statement_timeout_ms=5_000,
    )


def _generation(
    status: str = "answerable",
    sql: str | None = "SELECT 1",
    message: str | None = None,
    *,
    latency_ms: float = 4.0,
    input_tokens: int | None = 10,
    output_tokens: int | None = 5,
) -> NLToSQLGenerationResult:
    return NLToSQLGenerationResult(
        output=StructuredGeneration(status=status, sql=sql, message=message),
        metadata=GenerationMetadata(
            model="fake-model",
            reasoning_effort="medium",
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
    )


def _case(**updates: object) -> BenchmarkCase:
    values: dict[str, object] = {
        "id": "case_001",
        "category": "aggregation",
        "difficulty": "easy",
        "language": "en",
        "question": "How many accounts are there?",
        "expected_status": "answerable",
        "comparison_mode": "scalar",
        "reference_sql": "SELECT COUNT(*) FROM banking.accounts",
    }
    values.update(updates)
    return BenchmarkCase.model_validate(values)


class ResultComparisonTests(TestCase):
    def test_scalar_comparison_uses_exact_numeric_values(self) -> None:
        result = compare_query_results(
            ((Decimal("10.00"),),), ((10,),), "scalar"
        )
        self.assertTrue(result.matched)

    def test_ordered_rows_preserve_order(self) -> None:
        result = compare_query_results(((1,), (2,)), ((2,), (1,)), "ordered_rows")
        self.assertFalse(result.matched)

    def test_unordered_rows_ignore_order_but_preserve_duplicates(self) -> None:
        self.assertTrue(
            compare_query_results(
                ((1,), (1,), (2,)), ((2,), (1,), (1,)), "unordered_rows"
            ).matched
        )
        self.assertFalse(
            compare_query_results(
                ((1,), (1,), (2,)), ((2,), (2,), (1,)), "unordered_rows"
            ).matched
        )

    def test_normalization_covers_decimal_null_date_and_timestamp(self) -> None:
        self.assertEqual(normalize_value(Decimal("10.5000")), ("number", "10.5"))
        self.assertEqual(normalize_value(None), ("null", None))
        self.assertEqual(normalize_value(date(2026, 9, 1)), ("date", "2026-09-01"))
        self.assertEqual(
            normalize_value(datetime(2026, 9, 1, 8, 30)),
            ("timestamp", "2026-09-01T08:30:00"),
        )

    def test_column_aliases_are_ignored_but_shape_is_not(self) -> None:
        self.assertTrue(
            compare_query_results(
                ((1,),),
                ((1,),),
                "scalar",
                generated_column_count=1,
                reference_column_count=1,
            ).matched
        )
        mismatch = compare_query_results(
            (),
            (),
            "scalar",
            generated_column_count=1,
            reference_column_count=2,
        )
        self.assertEqual(mismatch.reason, "column_count_mismatch")


class _FakeGenerationService:
    def __init__(
        self,
        initial: NLToSQLGenerationResult,
        repaired: NLToSQLGenerationResult | None = None,
    ) -> None:
        self.initial = initial
        self.repaired = repaired
        self.repair_calls = 0

    def generate(self, question: str) -> NLToSQLGenerationResult:
        return self.initial

    def repair(
        self, question: str, previous_sql: str, sanitized_error: str
    ) -> NLToSQLGenerationResult:
        self.repair_calls += 1
        if self.repaired is None:
            raise AssertionError("unexpected repair")
        return self.repaired


class _AcceptingPolicy:
    def validate(self, value: object) -> object:
        return value


class EvaluationRunnerTests(TestCase):
    def test_semantic_status_is_scored_without_generated_execution(self) -> None:
        generated_executor = mock.Mock()
        runner = EvaluationRunner(
            _FakeGenerationService(
                _generation("unanswerable", None, "The field is unavailable")
            ),
            SQLASTValidator(),
            _AcceptingPolicy(),
            generated_executor,
            mock.Mock(),
        )
        case = _case(
            id="unanswerable_001",
            category="unanswerable",
            expected_status="unanswerable",
            comparison_mode=None,
            reference_sql=None,
        )

        result = runner.run_case(case)

        self.assertTrue(result.case_correct)
        self.assertTrue(result.status_matched)
        self.assertEqual(result.execution_outcome, "not_applicable")
        generated_executor.execute.assert_not_called()

    def test_sql_for_expected_unanswerable_is_incorrect_and_not_executed(self) -> None:
        generated_executor = mock.Mock()
        runner = EvaluationRunner(
            _FakeGenerationService(_generation(sql="SELECT 1")),
            SQLASTValidator(),
            _AcceptingPolicy(),
            generated_executor,
            mock.Mock(),
        )
        case = _case(
            id="unanswerable_002",
            category="unanswerable",
            expected_status="unanswerable",
            comparison_mode=None,
            reference_sql=None,
        )

        result = runner.run_case(case)

        self.assertFalse(result.case_correct)
        self.assertEqual(result.generated_status, "answerable")
        self.assertEqual(result.failure_reason, "status_mismatch")
        generated_executor.execute.assert_not_called()

    def test_safety_rejection_is_not_a_correct_answerable_result(self) -> None:
        generated_executor = mock.Mock()
        reference_executor = mock.Mock()
        reference_executor.execute.return_value = _query_result((1,))
        runner = EvaluationRunner(
            _FakeGenerationService(_generation(sql="DELETE FROM banking.accounts")),
            SQLASTValidator(),
            _AcceptingPolicy(),
            generated_executor,
            reference_executor,
        )

        result = runner.run_case(_case())

        self.assertFalse(result.case_correct)
        self.assertEqual(result.safety_outcome, "rejected")
        self.assertEqual(result.execution_outcome, "safety_rejected")
        self.assertEqual(result.safety_reason, "mutation_statement")
        generated_executor.execute.assert_not_called()

    def test_successful_repair_combines_generation_metadata(self) -> None:
        generation = _FakeGenerationService(
            _generation(sql="SELECT bad", latency_ms=4, input_tokens=10, output_tokens=5),
            _generation(sql="SELECT 1", latency_ms=6, input_tokens=8, output_tokens=3),
        )
        generated_executor = mock.Mock()
        generated_executor.execute.side_effect = (
            QueryExecutionError(
                "failed", repair_detail="column does not exist", repair_eligible=True
            ),
            _query_result((1,)),
        )
        reference_executor = mock.Mock()
        reference_executor.execute.return_value = _query_result((1,))
        runner = EvaluationRunner(
            generation,
            SQLASTValidator(),
            _AcceptingPolicy(),
            generated_executor,
            reference_executor,
        )

        result = runner.run_case(_case())

        self.assertTrue(result.case_correct)
        self.assertTrue(result.repair_attempted)
        self.assertTrue(result.repair_used)
        self.assertEqual(result.generated_sql, "SELECT 1")
        self.assertEqual(result.generation_latency_ms, 10)
        self.assertEqual(result.input_tokens, 18)
        self.assertEqual(result.output_tokens, 8)


class EvaluationPersistenceTests(TestCase):
    def setUp(self) -> None:
        self.case = _case()
        self.metadata = build_run_metadata(
            (self.case,),
            model="fake-model",
            reasoning_effort="medium",
            prompt_context_fingerprint="prompt-hash",
            generation_configuration={"max_rows": 100},
            run_id="stable-run-id",
        )

    def test_result_file_serialization_and_resume(self) -> None:
        with TemporaryDirectory() as directory:
            store = EvaluationRunStore(Path(directory) / "run.json")
            store.create(self.metadata)
            result = EvaluationCaseResult(
                benchmark_id=self.case.id,
                category=self.case.category,
                difficulty=self.case.difficulty,
                language=self.case.language,
                expected_status=self.case.expected_status,
                comparison_mode=self.case.comparison_mode,
                generation_success=True,
                generated_status="answerable",
                status_matched=True,
                safety_outcome="accepted",
                execution_outcome="success",
                comparison_matched=True,
                comparison_reason="matched",
                case_correct=True,
                end_to_end_latency_ms=1,
            )
            store.append(result)

            resumed = store.resume(self.metadata)

            self.assertEqual(resumed.metadata.run_id, "stable-run-id")
            self.assertEqual([item.benchmark_id for item in resumed.cases], [self.case.id])
            with self.assertRaises(EvaluationPersistenceError):
                store.append(result)

    def test_resume_rejects_incompatible_configuration(self) -> None:
        incompatible = build_run_metadata(
            (self.case,),
            model="different-model",
            reasoning_effort="medium",
            prompt_context_fingerprint="prompt-hash",
            generation_configuration={"max_rows": 100},
        )
        with TemporaryDirectory() as directory:
            store = EvaluationRunStore(Path(directory) / "run.json")
            store.create(self.metadata)
            with self.assertRaises(EvaluationPersistenceError):
                store.resume(incompatible)

    def test_run_metadata_is_reproducible_and_contains_required_identity(self) -> None:
        equivalent = build_run_metadata(
            (self.case,),
            model="fake-model",
            reasoning_effort="medium",
            prompt_context_fingerprint="prompt-hash",
            generation_configuration={"max_rows": 100},
            run_id="another-run-id",
        )

        self.assertEqual(
            equivalent.configuration_fingerprint,
            self.metadata.configuration_fingerprint,
        )
        self.assertEqual(equivalent.benchmark_case_count, 1)
        self.assertEqual(equivalent.model, "fake-model")
        self.assertTrue(equivalent.benchmark_fingerprint)
        self.assertTrue(equivalent.started_at)


class EvaluationPostgresIntegrationTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_context = temporary_database()
        cls.owner_url = cls.database_context.__enter__()
        cls.environment = mock.patch.dict(os.environ, {"DATABASE_URL": cls.owner_url})
        cls.environment.start()
        command.upgrade(Config("alembic.ini"), "head")
        cls.reader_user = f"banking_evaluation_reader_{uuid4().hex}"
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

    def test_fake_generation_runs_through_real_safety_and_postgres(self) -> None:
        statement = "SELECT COUNT(*) AS generated_count FROM banking.accounts"
        executor = ReadOnlyQueryExecutor(self.engine)
        runner = EvaluationRunner(
            _FakeGenerationService(_generation(sql=statement)),
            SQLASTValidator(),
            BankingSQLAccessPolicy.from_engine(self.engine),
            executor,
            executor,
        )

        result = runner.run_case(_case())

        self.assertTrue(result.case_correct)
        self.assertEqual(result.safety_outcome, "accepted")
        self.assertEqual(result.execution_outcome, "success")
        self.assertTrue(result.comparison_matched)
