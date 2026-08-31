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


@dataclass(frozen=True)
class APIClientConfig:
    """Frontend-safe HTTP settings loaded from environment variables."""

    base_url: str = DEFAULT_API_BASE_URL
    timeout_seconds: float = DEFAULT_API_REQUEST_TIMEOUT_SECONDS

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
        try:
            timeout_seconds = float(timeout_value)
        except ValueError as exc:
            raise APIClientConfigurationError(
                "API_REQUEST_TIMEOUT_SECONDS must be a number"
            ) from exc
        return cls(
            base_url=values.get("API_BASE_URL", DEFAULT_API_BASE_URL),
            timeout_seconds=timeout_seconds,
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
            timeout=config.timeout_seconds,
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

    def _request_json(self, method: str, path: str) -> Any:
        try:
            response = self._client.request(method, path)
        except httpx.TimeoutException as exc:
            raise BackendTimeoutError(
                "The application backend did not respond in time."
            ) from exc
        except httpx.RequestError as exc:
            raise BackendConnectionError(
                "Cannot connect to the application backend."
            ) from exc

        if not response.is_success:
            raise BackendResponseError(
                "The application backend could not complete the request."
            )
        try:
            return response.json()
        except ValueError as exc:
            raise BackendResponseError(
                "The application backend returned an invalid response."
            ) from exc
