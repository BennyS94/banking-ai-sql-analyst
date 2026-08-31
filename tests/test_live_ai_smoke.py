from __future__ import annotations

import io
import json
from unittest import TestCase, mock

from backend.app.ai.groq_client import GenerationMetadata, StructuredGeneration
from backend.app.ai.service import NLToSQLGenerationResult
from backend.app.ai.smoke import SMOKE_CASES, main
from backend.app.core.config import Settings
from backend.app.db.query_executor import ReadOnlyQueryExecutor


class LiveAISmokeCommandTests(TestCase):
    def test_smoke_set_covers_required_categories_and_languages(self) -> None:
        categories = {case.category for case in SMOKE_CASES}
        languages = {case.language for case in SMOKE_CASES}

        self.assertEqual(len(SMOKE_CASES), 9)
        self.assertTrue(
            {
                "filter",
                "aggregation",
                "join",
                "multi_table_join",
                "temporal",
                "unanswerable",
                "ambiguous",
                "robustness",
            }.issubset(categories)
        )
        self.assertEqual(languages, {"en", "ro"})

    @mock.patch("backend.app.ai.smoke.get_settings")
    def test_missing_api_key_fails_with_setup_message(self, get_settings) -> None:
        get_settings.return_value = Settings(
            groq_api_key=None,
            banking_reader_database_url=None,
        )
        stderr = io.StringIO()

        with mock.patch("sys.stderr", stderr):
            exit_code = main()

        self.assertEqual(exit_code, 2)
        self.assertIn("GROQ_API_KEY must be set", stderr.getvalue())

    @mock.patch("backend.app.ai.smoke.NLToSQLService.generate")
    @mock.patch("backend.app.ai.smoke.GroqStructuredGenerationClient")
    @mock.patch("backend.app.ai.smoke.create_runtime_engine")
    @mock.patch("backend.app.ai.smoke.get_settings")
    def test_command_reports_results_without_executing_sql(
        self,
        get_settings,
        create_engine,
        _client,
        generate,
    ) -> None:
        get_settings.return_value = Settings(
            groq_api_key="test-placeholder",
            banking_reader_database_url=(
                "postgresql+psycopg://banking_reader:test@localhost/banking_ai"
            ),
        )
        engine = mock.Mock()
        create_engine.return_value = engine
        generate.return_value = NLToSQLGenerationResult(
            output=StructuredGeneration(
                status="answerable",
                sql="SELECT 1",
                message=None,
            ),
            metadata=GenerationMetadata(
                model="openai/gpt-oss-120b",
                reasoning_effort="medium",
                latency_ms=12.345,
                input_tokens=10,
                output_tokens=5,
            ),
        )
        stdout = io.StringIO()

        with mock.patch.object(
            ReadOnlyQueryExecutor, "execute", autospec=True
        ) as execute, mock.patch("sys.stdout", stdout):
            exit_code = main()

        rows = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(rows), len(SMOKE_CASES))
        self.assertEqual(rows[0]["sql"], "SELECT 1")
        self.assertEqual(rows[0]["reasoning_effort"], "medium")
        self.assertEqual(generate.call_count, len(SMOKE_CASES))
        execute.assert_not_called()
        engine.dispose.assert_called_once_with()
