from __future__ import annotations

from pathlib import Path
from unittest import TestCase

import httpx
from streamlit.testing.v1 import AppTest

from frontend.api_client import (
    APIClientConfig,
    APIClientConfigurationError,
    BackendConnectionError,
    BackendResponseError,
    BackendTimeoutError,
    BankingAPIClient,
)


class APIClientConfigTests(TestCase):
    def test_configuration_reads_frontend_environment(self) -> None:
        config = APIClientConfig.from_env(
            {
                "API_BASE_URL": "https://backend.example.test/",
                "API_REQUEST_TIMEOUT_SECONDS": "4.5",
                "API_CONNECT_TIMEOUT_SECONDS": "0.75",
                "DATABASE_URL": "must-not-be-read",
                "GROQ_API_KEY": "must-not-be-read",
            }
        )

        self.assertEqual(config.base_url, "https://backend.example.test")
        self.assertEqual(config.timeout_seconds, 4.5)
        self.assertEqual(config.connect_timeout_seconds, 0.75)

    def test_invalid_url_and_timeout_are_rejected(self) -> None:
        with self.assertRaises(APIClientConfigurationError):
            APIClientConfig(base_url="localhost:8000")
        with self.assertRaises(APIClientConfigurationError):
            APIClientConfig(timeout_seconds=0)
        with self.assertRaises(APIClientConfigurationError):
            APIClientConfig(connect_timeout_seconds=0)
        with self.assertRaises(APIClientConfigurationError):
            APIClientConfig.from_env(
                {"API_REQUEST_TIMEOUT_SECONDS": "not-a-number"}
            )


class BankingAPIClientTests(TestCase):
    def _client(self, handler) -> BankingAPIClient:
        return BankingAPIClient(
            APIClientConfig(base_url="http://backend.test"),
            transport=httpx.MockTransport(handler),
        )

    def test_health_accepts_successful_backend_contract(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/health")
            return httpx.Response(200, json={"status": "ok"})

        with self._client(handler) as client:
            client.health()

    def test_connection_failure_has_safe_client_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("socket internals", request=request)

        with self._client(handler) as client:
            with self.assertRaises(BackendConnectionError) as caught:
                client.health()

        self.assertNotIn("socket", caught.exception.user_message)

    def test_timeout_has_safe_client_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timeout internals", request=request)

        with self._client(handler) as client:
            with self.assertRaises(BackendTimeoutError) as caught:
                client.health()

        self.assertNotIn("internals", caught.exception.user_message)

    def test_http_and_invalid_json_failures_are_translated(self) -> None:
        cases = (
            lambda _: httpx.Response(503, text="sensitive detail"),
            lambda _: httpx.Response(200, text="not-json"),
            lambda _: httpx.Response(200, json={"status": "unexpected"}),
        )
        for handler in cases:
            with self.subTest(handler=handler):
                with self._client(handler) as client:
                    with self.assertRaises(BackendResponseError) as caught:
                        client.health()
                self.assertNotIn("sensitive", caught.exception.user_message)


class StreamlitFoundationTests(TestCase):
    def test_page_foundation_renders_when_backend_is_unavailable(self) -> None:
        app_path = Path(__file__).parents[1] / "frontend" / "app.py"
        app = AppTest.from_file(str(app_path)).run()

        self.assertEqual(app.title[0].value, "Banking AI SQL Analyst")
        self.assertEqual(app.text_area[0].label, "Banking question")
        self.assertEqual(app.button[0].label, "Analyze")
        self.assertEqual(len(app.exception), 0)

    def test_empty_submission_is_handled_cleanly(self) -> None:
        app_path = Path(__file__).parents[1] / "frontend" / "app.py"
        app = AppTest.from_file(str(app_path)).run()

        app.button[0].click().run()

        self.assertIn("Enter a banking analytics question", app.warning[0].value)
        self.assertEqual(len(app.exception), 0)
