"""Typed request and response models for natural-language banking queries."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.app.ai.groq_client import GenerationMetadata
from backend.app.db.query_executor import NormalizedValue
from backend.app.query.service import SafeQueryResult


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str


class ExecutionMetadataResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    execution_ms: float
    statement_timeout_ms: int


class QueryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["answerable", "unanswerable", "ambiguous"]
    sql: str | None
    message: str | None
    columns: tuple[str, ...]
    rows: tuple[tuple[NormalizedValue, ...], ...]
    returned_row_count: int
    truncated: bool
    generation: GenerationMetadata
    execution: ExecutionMetadataResponse | None

    @classmethod
    def from_result(cls, result: SafeQueryResult) -> "QueryResponse":
        query_result = result.query_result
        return cls(
            status=result.status,
            sql=result.sql,
            message=result.message,
            columns=query_result.columns if query_result else (),
            rows=query_result.rows if query_result else (),
            returned_row_count=query_result.row_count if query_result else 0,
            truncated=query_result.truncated if query_result else False,
            generation=result.generation_metadata,
            execution=(
                ExecutionMetadataResponse(
                    execution_ms=query_result.execution_ms,
                    statement_timeout_ms=query_result.statement_timeout_ms,
                )
                if query_result
                else None
            ),
        )
