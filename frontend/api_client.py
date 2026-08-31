"""Synchronous HTTP boundary between Streamlit and FastAPI."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlsplit

import httpx


DEFAULT_API_BASE_URL = "http://localhost:8000"
DEFAULT_API_REQUEST_TIMEOUT_SECONDS = 30.0
DEFAULT_API_CONNECT_TIMEOUT_SECONDS = 1.0


class APIClientConfigurationError(ValueError):
    """Raised when frontend-only HTTP configuration is invalid."""


class APIClientError(RuntimeError):
    """Base error with a safe message suitable for the Streamlit UI."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class BackendConnectionError(APIClientError):
    """Raised when the FastAPI backend cannot be reached."""


class BackendTimeoutError(APIClientError):
    """Raised when an HTTP request to FastAPI times out."""


class BackendResponseError(APIClientError):
    """Raised when FastAPI returns an unusable response."""


class BackendAPIError(APIClientError):
    """Raised for a controlled error response from the FastAPI application."""

    def __init__(self, status_code: int, category: str, user_message: str) -> None:
        super().__init__(user_message)
        self.status_code = status_code
        self.category = category


@dataclass(frozen=True)
class APIClientConfig:
    """Frontend-safe HTTP settings loaded from environment variables."""

    base_url: str = DEFAULT_API_BASE_URL
    timeout_seconds: float = DEFAULT_API_REQUEST_TIMEOUT_SECONDS
    connect_timeout_seconds: float = DEFAULT_API_CONNECT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        normalized_url = self.base_url.strip().rstrip("/")
        parsed_url = urlsplit(normalized_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise APIClientConfigurationError(
                "API_BASE_URL must be an absolute HTTP or HTTPS URL"
            )
        if self.timeout_seconds <= 0:
            raise APIClientConfigurationError(
                "API_REQUEST_TIMEOUT_SECONDS must be greater than zero"
            )
        if self.connect_timeout_seconds <= 0:
            raise APIClientConfigurationError(
                "API_CONNECT_TIMEOUT_SECONDS must be greater than zero"
            )
        object.__setattr__(self, "base_url", normalized_url)

    @classmethod
    def from_env(
        cls, environment: Mapping[str, str] | None = None
    ) -> "APIClientConfig":
        """Load only the configuration needed by the frontend HTTP client."""
        values = environment if environment is not None else os.environ
        timeout_value = values.get(
            "API_REQUEST_TIMEOUT_SECONDS",
            str(DEFAULT_API_REQUEST_TIMEOUT_SECONDS),
        )
        connect_timeout_value = values.get(
            "API_CONNECT_TIMEOUT_SECONDS",
            str(DEFAULT_API_CONNECT_TIMEOUT_SECONDS),
        )
        try:
            timeout_seconds = float(timeout_value)
            connect_timeout_seconds = float(connect_timeout_value)
        except ValueError as exc:
            raise APIClientConfigurationError(
                "Frontend API timeout settings must be numbers"
            ) from exc
        return cls(
            base_url=values.get("API_BASE_URL", DEFAULT_API_BASE_URL),
            timeout_seconds=timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
        )


class BankingAPIClient:
    """Small project-owned client for the public FastAPI surface."""

    def __init__(
        self,
        config: APIClientConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=config.base_url,
            timeout=httpx.Timeout(
                config.timeout_seconds,
                connect=config.connect_timeout_seconds,
            ),
            transport=transport,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._client.close()

    def health(self) -> None:
        """Verify that the FastAPI process responds with its health contract."""
        payload = self._request_json("GET", "/health")
        if payload != {"status": "ok"}:
            raise BackendResponseError(
                "The application backend returned an unexpected health response."
            )

    def examples(self) -> tuple[dict[str, str], ...]:
        """Fetch the canonical banking example questions from FastAPI."""
        payload = self._request_json("GET", "/api/v1/examples")
        if not isinstance(payload, list):
            raise BackendResponseError(
                "The application backend returned invalid example questions."
            )
        examples: list[dict[str, str]] = []
        for item in payload:
            if not isinstance(item, dict):
                raise BackendResponseError(
                    "The application backend returned invalid example questions."
                )
            example_id = item.get("id")
            language = item.get("language")
            question = item.get("question")
            if (
                not isinstance(example_id, str)
                or language not in {"en", "ro"}
                or not isinstance(question, str)
                or not question.strip()
            ):
                raise BackendResponseError(
                    "The application backend returned invalid example questions."
                )
            examples.append(
                {
                    "id": example_id,
                    "language": language,
                    "question": question,
                }
            )
        return tuple(examples)

    def query(self, question: str) -> dict[str, Any]:
        """Submit one standalone natural-language question to FastAPI."""
        payload = self._request_json(
            "POST", "/api/v1/query", json_body={"question": question}
        )
        if not isinstance(payload, dict):
            raise BackendResponseError(
                "The application backend returned an invalid query response."
            )
        return payload

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = self._client.request(method, path, json=json_body)
        except httpx.TimeoutException as exc:
            raise BackendTimeoutError(
                "The application backend did not respond in time."
            ) from exc
        except httpx.RequestError as exc:
            raise BackendConnectionError(
                "Cannot connect to the application backend."
            ) from exc

        if not response.is_success:
            self._raise_for_error_response(response)
        try:
            return response.json()
        except ValueError as exc:
            raise BackendResponseError(
                "The application backend returned an invalid response."
            ) from exc

    @staticmethod
    def _raise_for_error_response(response: httpx.Response) -> None:
        try:
            payload = response.json()
        except ValueError as exc:
            raise BackendResponseError(
                "The application backend could not complete the request."
            ) from exc
        if not isinstance(payload, dict) or not isinstance(
            payload.get("detail"), dict
        ):
            raise BackendResponseError(
                "The application backend could not complete the request."
            )
        category = payload["detail"].get("category")
        message = payload["detail"].get("message")
        if not isinstance(category, str) or not isinstance(message, str):
            raise BackendResponseError(
                "The application backend could not complete the request."
            )
        raise BackendAPIError(response.status_code, category, message)
