from __future__ import annotations

import json
from unittest import TestCase

import httpx

from frontend.api_client import (
    APIClientConfig,
    BackendAPIError,
    BankingAPIClient,
)
from frontend.state import add_recent_question


class BankingAPIWorkflowClientTests(TestCase):
    def _client(self, handler) -> BankingAPIClient:
        return BankingAPIClient(
            APIClientConfig(base_url="http://backend.test"),
            transport=httpx.MockTransport(handler),
        )

    def test_query_submits_english_and_romanian_unicode_questions(self) -> None:
        questions = (
            "How many active loans are recorded?",
            "Câte împrumuturi active sunt înregistrate?",
        )
        for question in questions:
            with self.subTest(question=question):
                def handler(request: httpx.Request) -> httpx.Response:
                    self.assertEqual(request.method, "POST")
                    self.assertEqual(request.url.path, "/api/v1/query")
                    self.assertEqual(
                        json.loads(request.content.decode("utf-8")),
                        {"question": question},
                    )
                    return httpx.Response(200, json={"status": "answerable"})

                with self._client(handler) as client:
                    response = client.query(question)

                self.assertEqual(response["status"], "answerable")

    def test_examples_are_loaded_through_public_api(self) -> None:
        payload = [
            {"id": "one", "language": "en", "question": "Count accounts"},
            {"id": "two", "language": "ro", "question": "Câte conturi există?"},
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/v1/examples")
            return httpx.Response(200, json=payload)

        with self._client(handler) as client:
            examples = client.examples()

        self.assertEqual(examples, tuple(payload))

    def test_controlled_backend_error_reaches_ui_boundary(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                503,
                json={
                    "detail": {
                        "category": "provider_unavailable",
                        "message": "The query generation service is unavailable",
                    }
                },
            )

        with self._client(handler) as client:
            with self.assertRaises(BackendAPIError) as caught:
                client.query("Question")

        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.category, "provider_unavailable")
        self.assertEqual(
            caught.exception.user_message,
            "The query generation service is unavailable",
        )


class RecentQuestionStateTests(TestCase):
    def test_recent_questions_are_deduplicated_and_bounded(self) -> None:
        recent: list[str] = []
        for index in range(7):
            recent = add_recent_question(recent, f"Question {index}")

        self.assertEqual(
            recent,
            [
                "Question 6",
                "Question 5",
                "Question 4",
                "Question 3",
                "Question 2",
            ],
        )
        self.assertEqual(
            add_recent_question(recent, " Question 4 "),
            ["Question 4", "Question 6", "Question 5", "Question 3", "Question 2"],
        )

    def test_empty_question_does_not_enter_history(self) -> None:
        self.assertEqual(add_recent_question(["Existing"], "  "), ["Existing"])
