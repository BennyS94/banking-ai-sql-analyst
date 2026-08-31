"""Response-driven helpers for analytical query result presentation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd


class QueryPresentationError(ValueError):
    """Raised when a successful backend response cannot be presented safely."""


@dataclass(frozen=True)
class QueryPresentation:
    """Display-ready view of one answerable FastAPI response."""

    sql: str
    dataframe: pd.DataFrame
    returned_row_count: int
    truncated: bool
    repair_used: bool
    execution_ms: float | None
    statement_timeout_ms: int | None
    generation_ms: float | None
    model: str | None
    reasoning_effort: str | None
    provider_request_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    finish_reason: str | None


def build_query_presentation(response: Mapping[str, Any]) -> QueryPresentation:
    """Build a dataframe and metadata without reinterpreting backend semantics."""
    if response.get("status") != "answerable":
        raise QueryPresentationError("Query response is not answerable")

    sql = response.get("sql")
    columns = response.get("columns")
    rows = response.get("rows")
    returned_row_count = response.get("returned_row_count")
    truncated = response.get("truncated")
    repair_used = response.get("repair_used")
    if not isinstance(sql, str) or not sql.strip():
        raise QueryPresentationError("Answerable response has no SQL")
    if not _is_sequence(columns) or not all(
        isinstance(column, str) for column in columns
    ):
        raise QueryPresentationError("Query response columns are invalid")
    if not _is_sequence(rows) or not all(_is_sequence(row) for row in rows):
        raise QueryPresentationError("Query response rows are invalid")
    if any(len(row) != len(columns) for row in rows):
        raise QueryPresentationError("Query response row width is invalid")
    if (
        not isinstance(returned_row_count, int)
        or isinstance(returned_row_count, bool)
        or returned_row_count < 0
    ):
        raise QueryPresentationError("Returned row count is invalid")
    if not isinstance(truncated, bool) or not isinstance(repair_used, bool):
        raise QueryPresentationError("Query response flags are invalid")

    generation = _optional_mapping(response.get("generation"))
    execution = _optional_mapping(response.get("execution"))
    return QueryPresentation(
        sql=sql,
        dataframe=pd.DataFrame(list(rows), columns=list(columns)),
        returned_row_count=returned_row_count,
        truncated=truncated,
        repair_used=repair_used,
        execution_ms=_optional_number(execution.get("execution_ms")),
        statement_timeout_ms=_optional_integer(
            execution.get("statement_timeout_ms")
        ),
        generation_ms=_optional_number(generation.get("latency_ms")),
        model=_optional_text(generation.get("model")),
        reasoning_effort=_optional_text(generation.get("reasoning_effort")),
        provider_request_id=_optional_text(
            generation.get("provider_request_id")
        ),
        input_tokens=_optional_integer(generation.get("input_tokens")),
        output_tokens=_optional_integer(generation.get("output_tokens")),
        finish_reason=_optional_text(generation.get("finish_reason")),
    )


def format_duration(milliseconds: float | None) -> str:
    """Format optional backend timing metadata for a concise metric."""
    return "Not provided" if milliseconds is None else f"{milliseconds:,.1f} ms"


def _is_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    )


def _optional_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _optional_integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
