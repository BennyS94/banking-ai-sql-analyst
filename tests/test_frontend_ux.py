from __future__ import annotations

from pathlib import Path
from unittest import TestCase

from streamlit.testing.v1 import AppTest

from frontend.api_client import (
    APIClientError,
    BackendAPIError,
    BackendConnectionError,
    BackendResponseError,
    BackendTimeoutError,
)
from frontend.ux import (
    ErrorPresentation,
    api_error_presentation,
    client_error_presentation,
    semantic_presentation,
)


class SemanticPresentationTests(TestCase):
    def test_ambiguous_response_uses_backend_clarification(self) -> None:
        presentation = semantic_presentation(
            "ambiguous", "Do you mean transaction count or summed amount?"
        )

        self.assertIsNotNone(presentation)
        self.assertEqual(presentation.level, "warning")
        self.assertIn("clarification", presentation.title)
        self.assertIn("transaction count", presentation.message)

    def test_unanswerable_response_explains_dataset_limit(self) -> None:
        presentation = semantic_presentation(
            "unanswerable", "The banking dataset has no credit scores."
        )

        self.assertIsNotNone(presentation)
        self.assertEqual(presentation.level, "info")
        self.assertIn("available data", presentation.title)
        self.assertIn("no credit scores", presentation.message)


class ErrorPresentationTests(TestCase):
    def test_safety_rejection_states_that_no_query_executed(self) -> None:
        presentation = api_error_presentation("safety_rejection")

        self.assertIn("safety policy", presentation.title)
        self.assertIn("No query was executed", presentation.message)

    def test_provider_categories_share_sanitized_service_state(self) -> None:
        categories = (
            "provider_timeout",
            "provider_rate_limit",
            "provider_unavailable",
            "provider_request",
            "provider_configuration",
            "invalid_generation_protocol",
        )
        for category in categories:
            with self.subTest(category=category):
                presentation = api_error_presentation(
                    category, "sensitive provider detail"
                )
                self.assertIn("AI generation", presentation.title)
                self.assertNotIn("sensitive", presentation.message)

    def test_database_timeout_and_failure_remain_distinct(self) -> None:
        timeout = api_error_presentation("database_timeout")
        failure = api_error_presentation("database_failure")

        self.assertIn("timed out", timeout.title)
        self.assertIn("execution time", timeout.message)
        self.assertIn("unavailable", failure.title)
        self.assertNotEqual(timeout.category, failure.category)

    def test_network_timeout_response_and_generic_errors_are_sanitized(self) -> None:
        cases = (
            (BackendConnectionError("socket detail"), "backend_unavailable"),
            (BackendTimeoutError("timeout detail"), "backend_timeout"),
            (BackendResponseError("response detail"), "backend_response"),
            (APIClientError("unknown detail"), "backend_failure"),
        )
        for error, category in cases:
            with self.subTest(category=category):
                presentation = client_error_presentation(error)
                self.assertEqual(presentation.category, category)
                self.assertNotIn("detail", presentation.message)

    def test_backend_category_is_used_instead_of_backend_detail(self) -> None:
        error = BackendAPIError(
            422, "safety_rejection", "sensitive validator internals"
        )

        presentation = client_error_presentation(error)

        self.assertEqual(presentation.category, "safety_rejection")
        self.assertNotIn("internals", presentation.message)


class ResilientStreamlitSmokeTests(TestCase):
    def _app(self) -> AppTest:
        app_path = Path(__file__).parents[1] / "frontend" / "app.py"
        app = AppTest.from_file(str(app_path))
        app.session_state["question_input"] = "Question"
        app.session_state["example_questions"] = ()
        app.session_state["recent_questions"] = []
        app.session_state["latest_error"] = None
        return app

    def test_ambiguous_and_unanswerable_states_render_without_sql(self) -> None:
        cases = (
            ("ambiguous", "Please choose count or amount", "clarification"),
            ("unanswerable", "No credit score data", "available data"),
        )
        for status, message, expected_title in cases:
            with self.subTest(status=status):
                app = self._app()
                app.session_state["latest_query"] = {
                    "question": "Question",
                    "response": {"status": status, "message": message},
                }

                app.run()

                self.assertTrue(
                    any(expected_title in item.value for item in app.subheader)
                )
                self.assertEqual(len(app.code), 0)
                self.assertEqual(len(app.exception), 0)

    def test_sanitized_error_state_renders_without_traceback(self) -> None:
        app = self._app()
        app.session_state["latest_query"] = None
        app.session_state["latest_error"] = ErrorPresentation(
            category="safety_rejection",
            title="Query blocked by the safety policy",
            message="No query was executed.",
        )

        app.run()

        self.assertIn("No query was executed", app.error[0].value)
        self.assertEqual(len(app.exception), 0)
