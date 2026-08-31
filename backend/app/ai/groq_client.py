"""Small Groq boundary for validated structured NL-to-SQL output."""

from __future__ import annotations

import json
import time
from typing import Any, Literal, Protocol, Sequence, cast

import groq
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from backend.app.core.config import Settings


GenerationStatus = Literal["answerable", "unanswerable", "ambiguous"]
ReasoningEffort = Literal["low", "medium", "high"]


class StructuredGeneration(BaseModel):
    """Semantic model output, independent of provider metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: GenerationStatus
    sql: str | None
    message: str | None

    @model_validator(mode="after")
    def enforce_status_contract(self) -> "StructuredGeneration":
        sql = self.sql.strip() if self.sql is not None else None
        message = self.message.strip() if self.message is not None else None

        if self.status == "answerable":
            if not sql or message is not None:
                raise ValueError(
                    "answerable output requires non-empty SQL and a null message"
                )
        elif sql is not None or not message:
            raise ValueError(
                "non-answerable output requires null SQL and a non-empty message"
            )
        return self


class GenerationMetadata(BaseModel):
    """Operational metadata retained from a provider completion."""

    model_config = ConfigDict(frozen=True)

    model: str
    reasoning_effort: ReasoningEffort
    latency_ms: float = Field(ge=0)
    provider_request_id: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    finish_reason: str | None = None


class ProviderGenerationResult(BaseModel):
    """Validated semantic output plus non-semantic provider metadata."""

    model_config = ConfigDict(frozen=True)

    output: StructuredGeneration
    metadata: GenerationMetadata


class GroqConfigurationError(RuntimeError):
    """Raised when Groq configuration is missing or unsupported."""


class GroqTimeoutError(RuntimeError):
    """Raised when the Groq request times out."""


class GroqRateLimitError(RuntimeError):
    """Raised when Groq rejects a request due to rate limiting."""


class GroqUnavailableError(RuntimeError):
    """Raised when Groq cannot be reached or has a server failure."""


class GroqRequestError(RuntimeError):
    """Raised when Groq rejects a non-transient request."""


class InvalidStructuredResponseError(RuntimeError):
    """Raised when provider output violates the project contract."""


class _ChatCompletions(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class _Chat(Protocol):
    completions: _ChatCompletions


class _GroqClient(Protocol):
    chat: _Chat


def structured_generation_json_schema() -> dict[str, Any]:
    """Return the strict Groq JSON Schema for the semantic output contract."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "nl_to_sql_generation",
            "strict": True,
            "schema": StructuredGeneration.model_json_schema(),
        },
    }


class GroqStructuredGenerationClient:
    """Submit text messages to Groq and return project-owned response models."""

    def __init__(
        self,
        settings: Settings,
        provider_client: _GroqClient | None = None,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        api_key = (
            settings.groq_api_key.get_secret_value()
            if settings.groq_api_key is not None
            else ""
        )
        model = settings.groq_model.strip()
        effort = settings.groq_reasoning_effort.strip().lower()

        if not api_key:
            raise GroqConfigurationError("GROQ_API_KEY must be set")
        if not model:
            raise GroqConfigurationError("GROQ_MODEL must be non-empty")
        if effort not in {"low", "medium", "high"}:
            raise GroqConfigurationError(
                "GROQ_REASONING_EFFORT must be low, medium or high"
            )
        if timeout_seconds <= 0:
            raise GroqConfigurationError("Groq timeout must be positive")

        self._model = model
        self._reasoning_effort = cast(ReasoningEffort, effort)
        self._client = provider_client or groq.Groq(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=1,
        )

    def generate(self, messages: Sequence[dict[str, str]]) -> ProviderGenerationResult:
        """Request one strict structured completion without tools or execution."""
        started = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=list(messages),
                reasoning_effort=self._reasoning_effort,
                reasoning_format="hidden",
                response_format=structured_generation_json_schema(),
                stream=False,
            )
        except groq.APITimeoutError as exc:
            raise GroqTimeoutError("Groq request timed out") from exc
        except groq.RateLimitError as exc:
            raise GroqRateLimitError("Groq request was rate limited") from exc
        except (groq.APIConnectionError, groq.InternalServerError) as exc:
            raise GroqUnavailableError("Groq service is unavailable") from exc
        except groq.APIError as exc:
            raise GroqRequestError("Groq rejected the generation request") from exc

        latency_ms = (time.perf_counter() - started) * 1_000
        try:
            choice = response.choices[0]
            content = choice.message.content
            if not isinstance(content, str):
                raise TypeError("completion content is not text")
            output = StructuredGeneration.model_validate(json.loads(content))
        except (
            AttributeError,
            IndexError,
            TypeError,
            json.JSONDecodeError,
            ValidationError,
        ) as exc:
            raise InvalidStructuredResponseError(
                "Groq returned an invalid structured response"
            ) from exc

        usage = getattr(response, "usage", None)
        metadata = GenerationMetadata(
            model=getattr(response, "model", None) or self._model,
            reasoning_effort=self._reasoning_effort,
            latency_ms=latency_ms,
            provider_request_id=getattr(response, "id", None),
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            finish_reason=getattr(choice, "finish_reason", None),
        )
        return ProviderGenerationResult(output=output, metadata=metadata)
