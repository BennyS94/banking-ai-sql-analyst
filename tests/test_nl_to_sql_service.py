from __future__ import annotations

from unittest import TestCase, mock

from backend.app.ai.groq_client import (
    GenerationMetadata,
    GroqRateLimitError,
    GroqTimeoutError,
    InvalidStructuredResponseError,
    ProviderGenerationResult,
    StructuredGeneration,
)
from backend.app.ai.service import (
    MAX_QUESTION_LENGTH,
    MAX_REPAIR_ERROR_LENGTH,
    NLToSQLService,
    QuestionValidationError,
)
from backend.app.db.query_executor import ReadOnlyQueryExecutor


CONTEXT = """DATABASE DIALECT
PostgreSQL

SCHEMA
banking

TABLE banking.accounts
- account_id: integer, primary key, not null

BUSINESS SEMANTICS
- Transaction count: Count qualifying transaction rows.

CONTROLLED DOMAIN VALUES
- Account statuses: Active, Closed, Inactive"""


def _provider_result(
    status: str = "answerable",
) -> ProviderGenerationResult:
    if status == "answerable":
        output = StructuredGeneration(
            status="answerable", sql="SELECT 1", message=None
        )
    else:
        output = StructuredGeneration(
            status=status,  # type: ignore[arg-type]
            sql=None,
            message="A concise explanation",
        )
    return ProviderGenerationResult(
        output=output,
        metadata=GenerationMetadata(
            model="openai/gpt-oss-120b",
            reasoning_effort="medium",
            latency_ms=15.5,
            provider_request_id="request-1",
            input_tokens=100,
            output_tokens=20,
            finish_reason="stop",
        ),
    )


class NLToSQLServiceTests(TestCase):
    def setUp(self) -> None:
        self.context_builder = mock.Mock()
        self.context_builder.build.return_value = CONTEXT
        self.client = mock.Mock()
        self.client.generate.return_value = _provider_result()
        self.service = NLToSQLService(self.context_builder, self.client)

    def test_orchestrates_answerable_generation_and_preserves_metadata(self) -> None:
        with mock.patch.object(
            ReadOnlyQueryExecutor,
            "execute",
            autospec=True,
        ) as execute:
            result = self.service.generate("How many accounts exist?")

        self.assertEqual(result.output.status, "answerable")
        self.assertEqual(result.output.sql, "SELECT 1")
        self.assertEqual(result.metadata.provider_request_id, "request-1")
        self.assertEqual(result.metadata.input_tokens, 100)
        self.context_builder.build.assert_called_once_with()
        self.client.generate.assert_called_once()
        execute.assert_not_called()

    def test_preserves_unanswerable_and_ambiguous_as_valid_results(self) -> None:
        for status in ("unanswerable", "ambiguous"):
            with self.subTest(status=status):
                self.client.generate.return_value = _provider_result(status)
                result = self.service.generate("Question")
                self.assertEqual(result.output.status, status)
                self.assertIsNone(result.output.sql)

    def test_rejects_empty_and_oversized_questions_before_dependencies(self) -> None:
        for question in ("", " \t\n", "x" * (MAX_QUESTION_LENGTH + 1)):
            with self.subTest(length=len(question)):
                with self.assertRaises(QuestionValidationError):
                    self.service.generate(question)

        self.context_builder.build.assert_not_called()
        self.client.generate.assert_not_called()

    def test_preserves_romanian_unicode_without_translation(self) -> None:
        question = "Câte conturi active sunt în București?"

        self.service.generate(question)

        messages = self.client.generate.call_args.args[0]
        self.assertIn(question, messages[1]["content"])

    def test_context_and_prompt_are_deterministic(self) -> None:
        question = "Count accounts."

        self.service.generate(question)
        first_messages = self.client.generate.call_args.args[0]
        self.client.reset_mock()
        self.service.generate(question)
        second_messages = self.client.generate.call_args.args[0]

        self.assertEqual(first_messages, second_messages)

    def test_provider_failures_propagate_as_errors_not_statuses(self) -> None:
        errors = (
            GroqTimeoutError("Groq request timed out"),
            GroqRateLimitError("Groq request was rate limited"),
            InvalidStructuredResponseError("Invalid structured response"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.client.generate.side_effect = error
                with self.assertRaises(type(error)):
                    self.service.generate("Question")

    def test_repair_builds_one_grounded_structured_request(self) -> None:
        result = self.service.repair(
            "How many accounts exist?",
            "SELECT unavailable FROM banking.accounts",
            "column unavailable does not exist",
        )

        self.assertEqual(result.output.status, "answerable")
        self.context_builder.build.assert_called_once_with()
        self.client.generate.assert_called_once()
        messages = self.client.generate.call_args.args[0]
        self.assertIn("only allowed correction attempt", messages[0]["content"])
        self.assertIn("column unavailable does not exist", messages[1]["content"])

    def test_repair_rejects_invalid_internal_context_before_dependencies(self) -> None:
        cases = (
            ("", "safe error"),
            ("SELECT 1", ""),
            ("SELECT 1", "x" * (MAX_REPAIR_ERROR_LENGTH + 1)),
        )
        for previous_sql, error in cases:
            with self.subTest(previous_sql=previous_sql, error_length=len(error)):
                with self.assertRaises(ValueError):
                    self.service.repair("Question", previous_sql, error)

        self.context_builder.build.assert_not_called()
        self.client.generate.assert_not_called()
