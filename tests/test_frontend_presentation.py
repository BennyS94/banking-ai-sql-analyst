from __future__ import annotations

from pathlib import Path
from unittest import TestCase

from streamlit.testing.v1 import AppTest

from frontend.presentation import (
    QueryPresentationError,
    build_query_presentation,
    format_duration,
)


def _response(**overrides):
    response = {
        "status": "answerable",
        "sql": "SELECT account_id, balance FROM banking.accounts",
        "message": None,
        "columns": ["account_id", "balance"],
        "rows": [[1, "10.25"], [2, None]],
        "returned_row_count": 2,
        "truncated": False,
        "repair_used": False,
        "generation": {
            "model": "openai/gpt-oss-120b",
            "reasoning_effort": "medium",
            "latency_ms": 417.25,
            "provider_request_id": "request-1",
            "input_tokens": 100,
            "output_tokens": 25,
            "finish_reason": "stop",
        },
        "execution": {
            "execution_ms": 23.5,
            "statement_timeout_ms": 5_000,
        },
    }
    response.update(overrides)
    return response


class QueryPresentationTests(TestCase):
    def test_normal_rows_and_metadata_are_presented_from_backend(self) -> None:
        presentation = build_query_presentation(_response())

        self.assertEqual(
            presentation.dataframe.to_dict(orient="records"),
            [
                {"account_id": 1, "balance": "10.25"},
                {"account_id": 2, "balance": None},
            ],
        )
        self.assertEqual(presentation.returned_row_count, 2)
        self.assertEqual(presentation.execution_ms, 23.5)
        self.assertEqual(presentation.generation_ms, 417.25)
        self.assertEqual(presentation.model, "openai/gpt-oss-120b")
        self.assertEqual(presentation.statement_timeout_ms, 5_000)
        self.assertEqual(presentation.input_tokens, 100)

    def test_zero_rows_preserve_result_columns(self) -> None:
        presentation = build_query_presentation(
            _response(rows=[], returned_row_count=0)
        )

        self.assertTrue(presentation.dataframe.empty)
        self.assertEqual(
            list(presentation.dataframe.columns), ["account_id", "balance"]
        )
        self.assertEqual(presentation.returned_row_count, 0)

    def test_truncation_and_repair_flags_are_not_inferred(self) -> None:
        presentation = build_query_presentation(
            _response(truncated=True, repair_used=True, returned_row_count=500)
        )

        self.assertTrue(presentation.truncated)
        self.assertTrue(presentation.repair_used)
        self.assertEqual(presentation.returned_row_count, 500)

    def test_missing_optional_metadata_remains_absent(self) -> None:
        presentation = build_query_presentation(
            _response(generation=None, execution=None)
        )

        self.assertIsNone(presentation.execution_ms)
        self.assertIsNone(presentation.generation_ms)
        self.assertIsNone(presentation.model)
        self.assertEqual(format_duration(None), "Not provided")

    def test_decimal_date_timestamp_and_null_values_are_not_rewritten(self) -> None:
        values = [
            "1234567890.12",
            "2026-08-31",
            "2026-08-31T15:01:42",
            None,
        ]
        presentation = build_query_presentation(
            _response(
                columns=["amount", "date", "timestamp", "description"],
                rows=[values],
                returned_row_count=1,
            )
        )

        self.assertEqual(presentation.dataframe.iloc[0].tolist(), values)

    def test_malformed_answerable_response_is_rejected(self) -> None:
        cases = (
            _response(sql=None),
            _response(columns=["one"], rows=[[1, 2]]),
            _response(returned_row_count=True),
            _response(truncated="yes"),
        )
        for response in cases:
            with self.subTest(response=response):
                with self.assertRaises(QueryPresentationError):
                    build_query_presentation(response)


class QueryPresentationSmokeTests(TestCase):
    def test_answerable_result_renders_sql_table_and_metadata(self) -> None:
        app_path = Path(__file__).parents[1] / "frontend" / "app.py"
        app = AppTest.from_file(str(app_path))
        app.session_state["question_input"] = "List account balances"
        app.session_state["example_questions"] = ()
        app.session_state["latest_query"] = {
            "question": "List account balances",
            "response": _response(repair_used=True, truncated=True),
        }
        app.session_state["recent_questions"] = []

        app.run(timeout=10)

        self.assertEqual(app.code[0].value, _response()["sql"])
        self.assertEqual(len(app.dataframe), 1)
        self.assertEqual(len(app.metric), 4)
        self.assertTrue(
            any("application limit" in item.value for item in app.warning)
        )
        self.assertTrue(
            any("automatically corrected" in item.value for item in app.info)
        )
        self.assertEqual(len(app.exception), 0)

    def test_zero_row_success_is_explicit_and_keeps_sql_visible(self) -> None:
        app_path = Path(__file__).parents[1] / "frontend" / "app.py"
        app = AppTest.from_file(str(app_path))
        app.session_state["question_input"] = "Find impossible balance"
        app.session_state["example_questions"] = ()
        app.session_state["latest_query"] = {
            "question": "Find impossible balance",
            "response": _response(rows=[], returned_row_count=0),
        }
        app.session_state["latest_error"] = None
        app.session_state["recent_questions"] = []

        app.run(timeout=10)

        self.assertTrue(
            any("returned no rows" in item.value for item in app.info)
        )
        self.assertEqual(len(app.code), 1)
        self.assertEqual(len(app.exception), 0)
