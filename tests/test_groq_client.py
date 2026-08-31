from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import TestCase, mock

import groq
import httpx
from pydantic import ValidationError

from backend.app.ai.groq_client import (
    GroqConfigurationError,
    GroqRateLimitError,
    GroqRequestError,
    GroqStructuredGenerationClient,
    GroqTimeoutError,
    GroqUnavailableError,
    InvalidStructuredResponseError,
    StructuredGeneration,
    structured_generation_json_schema,
)
from backend.app.core.config import Settings


def _response(payload: object) -> SimpleNamespace:
    return SimpleNamespace(
        id="request-123",
        model="openai/gpt-oss-120b",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(payload)),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30),
    )


class GroqStructuredGenerationClientTests(TestCase):
    def setUp(self) -> None:
        self.completions = mock.Mock()
        provider = SimpleNamespace(
            chat=SimpleNamespace(completions=self.completions)
        )
        self.client = GroqStructuredGenerationClient(
            Settings(groq_api_key="test-placeholder"),
            provider_client=provider,
        )

    def test_model_and_reasoning_effort_are_environment_configurable(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                "GROQ_API_KEY": "environment-placeholder",
                "GROQ_MODEL": "openai/gpt-oss-20b",
                "GROQ_REASONING_EFFORT": "high",
            },
        ):
            settings = Settings(_env_file=None)
        client = GroqStructuredGenerationClient(
            settings,
            provider_client=SimpleNamespace(
                chat=SimpleNamespace(completions=self.completions)
            ),
        )
        self.completions.create.return_value = _response(
            {"status": "answerable", "sql": "SELECT 1", "message": None}
        )

        client.generate([])

        request = self.completions.create.call_args.kwargs
        self.assertEqual(request["model"], "openai/gpt-oss-20b")
        self.assertEqual(request["reasoning_effort"], "high")

    def test_accepts_answerable_response_and_requests_strict_schema(self) -> None:
        self.completions.create.return_value = _response(
            {"status": "answerable", "sql": "SELECT 1", "message": None}
        )

        result = self.client.generate([{"role": "user", "content": "question"}])

        self.assertEqual(result.output.sql, "SELECT 1")
        request = self.completions.create.call_args.kwargs
        self.assertEqual(request["model"], "openai/gpt-oss-120b")
        self.assertEqual(request["reasoning_effort"], "medium")
        self.assertEqual(request["reasoning_format"], "hidden")
        self.assertNotIn("tools", request)
        self.assertTrue(request["response_format"]["json_schema"]["strict"])

    def test_accepts_unanswerable_and_ambiguous_responses(self) -> None:
        for status in ("unanswerable", "ambiguous"):
            with self.subTest(status=status):
                self.completions.create.return_value = _response(
                    {"status": status, "sql": None, "message": "Clarification"}
                )
                self.assertEqual(self.client.generate([]).output.status, status)

    def test_contract_rejects_invalid_combinations(self) -> None:
        invalid_payloads = (
            {"status": "invalid", "sql": None, "message": "No"},
            {"status": "answerable", "sql": None, "message": None},
            {"status": "unanswerable", "sql": "SELECT 1", "message": "No"},
            {"status": "ambiguous", "sql": None, "message": None},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    StructuredGeneration.model_validate(payload)

    def test_rejects_malformed_and_contract_invalid_provider_content(self) -> None:
        for content in ("not json", json.dumps({"status": "answerable"})):
            with self.subTest(content=content):
                response = _response({})
                response.choices[0].message.content = content
                self.completions.create.return_value = response
                with self.assertRaises(InvalidStructuredResponseError):
                    self.client.generate([])

    def test_extracts_provider_metadata(self) -> None:
        self.completions.create.return_value = _response(
            {"status": "answerable", "sql": "SELECT 1", "message": None}
        )

        metadata = self.client.generate([]).metadata

        self.assertEqual(metadata.provider_request_id, "request-123")
        self.assertEqual(metadata.input_tokens, 120)
        self.assertEqual(metadata.output_tokens, 30)
        self.assertEqual(metadata.finish_reason, "stop")
        self.assertGreaterEqual(metadata.latency_ms, 0)

    def test_translates_timeout_rate_limit_network_and_request_errors(self) -> None:
        request = httpx.Request("POST", "https://api.groq.com")
        rate_limit_response = httpx.Response(429, request=request)
        bad_request_response = httpx.Response(400, request=request)
        cases = (
            (groq.APITimeoutError(request=request), GroqTimeoutError),
            (
                groq.RateLimitError(
                    "limited", response=rate_limit_response, body=None
                ),
                GroqRateLimitError,
            ),
            (groq.APIConnectionError(request=request), GroqUnavailableError),
            (
                groq.BadRequestError(
                    "bad", response=bad_request_response, body=None
                ),
                GroqRequestError,
            ),
        )
        for provider_error, project_error in cases:
            with self.subTest(project_error=project_error):
                self.completions.create.side_effect = provider_error
                with self.assertRaises(project_error):
                    self.client.generate([])

    def test_configuration_and_errors_do_not_expose_api_key(self) -> None:
        secret = "never-print-this-key"
        with self.assertRaises(GroqConfigurationError):
            GroqStructuredGenerationClient(Settings(groq_api_key=None))

        request = httpx.Request("POST", "https://api.groq.com")
        self.completions.create.side_effect = groq.APIConnectionError(
            message=secret,
            request=request,
        )
        with self.assertRaises(GroqUnavailableError) as raised:
            self.client.generate([])
        self.assertNotIn(secret, str(raised.exception))

    def test_schema_requires_all_fields_and_forbids_extras(self) -> None:
        schema = structured_generation_json_schema()["json_schema"]["schema"]
        self.assertEqual(set(schema["required"]), {"status", "sql", "message"})
        self.assertFalse(schema["additionalProperties"])
