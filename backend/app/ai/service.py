"""Application orchestration for structured NL-to-SQL generation."""

from __future__ import annotations

from typing import Protocol, Sequence

from pydantic import BaseModel, ConfigDict

from backend.app.ai.groq_client import (
    GenerationMetadata,
    ProviderGenerationResult,
    StructuredGeneration,
)
from backend.app.ai.prompt import NLToSQLPromptBuilder


MAX_QUESTION_LENGTH = 2_000


class QuestionValidationError(ValueError):
    """Raised before provider invocation for an invalid standalone question."""


class NLToSQLGenerationResult(BaseModel):
    """Application-level semantic output and separate operational metadata."""

    model_config = ConfigDict(frozen=True)

    output: StructuredGeneration
    metadata: GenerationMetadata


class _ContextBuilder(Protocol):
    def build(self) -> str: ...


class _GenerationClient(Protocol):
    def generate(
        self, messages: Sequence[dict[str, str]]
    ) -> ProviderGenerationResult: ...


class NLToSQLService:
    """Generate untrusted SQL for one standalone question without executing it."""

    def __init__(
        self,
        context_builder: _ContextBuilder,
        generation_client: _GenerationClient,
    ) -> None:
        self._context_builder = context_builder
        self._generation_client = generation_client

    def generate(self, question: str) -> NLToSQLGenerationResult:
        """Validate, ground, prompt and invoke one structured generation call."""
        _validate_question(question)
        context = self._context_builder.build()
        messages = NLToSQLPromptBuilder(context).build(question)
        provider_result = self._generation_client.generate(messages)
        return NLToSQLGenerationResult(
            output=provider_result.output,
            metadata=provider_result.metadata,
        )


def _validate_question(question: str) -> None:
    if not isinstance(question, str) or not question.strip():
        raise QuestionValidationError("Question must be non-empty")
    if len(question) > MAX_QUESTION_LENGTH:
        raise QuestionValidationError(
            f"Question must contain at most {MAX_QUESTION_LENGTH} characters"
        )
