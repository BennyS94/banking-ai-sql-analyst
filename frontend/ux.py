"""User-facing semantic and failure presentations for the query console."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from frontend.api_client import (
    APIClientError,
    BackendAPIError,
    BackendConnectionError,
    BackendResponseError,
    BackendTimeoutError,
)


@dataclass(frozen=True)
class SemanticPresentation:
    """Display text for a non-error semantic response."""

    level: Literal["info", "warning"]
    title: str
    message: str


@dataclass(frozen=True)
class ErrorPresentation:
    """Sanitized display text for one failed frontend request."""

    category: str
    title: str
    message: str


def semantic_presentation(
    status: object, backend_message: object
) -> SemanticPresentation | None:
    """Map backend semantic status directly to a non-error UI state."""
    message = (
        backend_message.strip()
        if isinstance(backend_message, str) and backend_message.strip()
        else None
    )
    if status == "ambiguous":
        return SemanticPresentation(
            level="warning",
            title="Your question needs clarification",
            message=message
            or "Add a more specific banking measure or filter and try again.",
        )
    if status == "unanswerable":
        return SemanticPresentation(
            level="info",
            title="This question cannot be answered from the available data",
            message=message
            or (
                "The synthetic banking dataset does not contain the required "
                "information."
            ),
        )
    return None


def client_error_presentation(error: APIClientError) -> ErrorPresentation:
    """Translate client and stable backend categories into sanitized UI copy."""
    if isinstance(error, BackendAPIError):
        return api_error_presentation(error.category, error.user_message)
    if isinstance(error, BackendTimeoutError):
        return ErrorPresentation(
            category="backend_timeout",
            title="Application backend timed out",
            message=(
                "The application backend did not respond in time. "
                "No result was returned."
            ),
        )
    if isinstance(error, BackendConnectionError):
        return ErrorPresentation(
            category="backend_unavailable",
            title="Cannot connect to the application backend",
            message=(
                "Check that the local FastAPI service is running and try again."
            ),
        )
    if isinstance(error, BackendResponseError):
        return ErrorPresentation(
            category="backend_response",
            title="Unexpected backend response",
            message="The application backend returned an unusable response.",
        )
    return ErrorPresentation(
        category="backend_failure",
        title="Banking analysis unavailable",
        message="The request could not be completed. Try again shortly.",
    )


def api_error_presentation(
    category: str, backend_message: str = ""
) -> ErrorPresentation:
    """Map FastAPI's stable public query taxonomy to restrained UI states."""
    if category == "safety_rejection":
        return ErrorPresentation(
            category=category,
            title="Query blocked by the safety policy",
            message=(
                "The generated query did not pass the application's SQL safety "
                "policy. No query was executed."
            ),
        )
    if category in {
        "provider_timeout",
        "provider_rate_limit",
        "provider_unavailable",
        "provider_request",
        "provider_configuration",
        "invalid_generation_protocol",
    }:
        return ErrorPresentation(
            category=category,
            title="AI generation service unavailable",
            message=(
                "The AI generation service is temporarily unavailable. "
                "Try again shortly."
            ),
        )
    if category == "database_timeout":
        return ErrorPresentation(
            category=category,
            title="Database execution timed out",
            message=(
                "The query exceeded the allowed database execution time. "
                "No result was returned."
            ),
        )
    if category == "database_failure":
        return ErrorPresentation(
            category=category,
            title="Banking data service unavailable",
            message=(
                "The banking data service is temporarily unavailable. "
                "Try again shortly."
            ),
        )
    if category in {"query_execution_error", "query_repair_failed"}:
        return ErrorPresentation(
            category=category,
            title="Query execution could not be completed",
            message=(
                "The generated query produced no result. "
                "Try rephrasing the question."
            ),
        )
    if category == "context_failure":
        return ErrorPresentation(
            category=category,
            title="Banking analysis unavailable",
            message="The banking generation context is temporarily unavailable.",
        )
    if category == "invalid_question":
        message = backend_message.strip() or "The question is not valid."
        return ErrorPresentation(
            category=category,
            title="Question could not be submitted",
            message=message,
        )
    return ErrorPresentation(
        category=category or "backend_failure",
        title="Banking analysis unavailable",
        message="The application backend could not complete the request.",
    )
