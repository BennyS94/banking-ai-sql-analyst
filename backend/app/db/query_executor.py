"""Execution of trusted, upstream-approved analytical SQL."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import logging
import math
import time

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError


logger = logging.getLogger(__name__)
NormalizedValue = int | float | str | bool | None


class QueryExecutionError(RuntimeError):
    """Raised when PostgreSQL rejects an approved internal query."""


class QueryResultNormalizationError(RuntimeError):
    """Raised when a result contains a value without an explicit JSON mapping."""


class QueryResult(BaseModel):
    """Serializable rows and execution metadata from an approved query."""

    model_config = ConfigDict(frozen=True)

    columns: tuple[str, ...]
    rows: tuple[tuple[NormalizedValue, ...], ...]
    row_count: int
    execution_ms: float


class ReadOnlyQueryExecutor:
    """Execute SQL already approved by an upstream safety boundary.

    This component deliberately does not parse, validate or approve SQL. Its
    engine must be created through the read-only runtime database boundary.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def execute(self, statement: str) -> QueryResult:
        started = time.perf_counter()
        try:
            with self._engine.connect() as connection:
                result = connection.execute(text(statement))
                columns = tuple(result.keys())
                rows = tuple(
                    tuple(_normalize_value(value) for value in row)
                    for row in result.fetchall()
                )
        except SQLAlchemyError as exc:
            logger.exception("Approved query execution failed")
            raise QueryExecutionError("Database query execution failed") from exc

        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            execution_ms=(time.perf_counter() - started) * 1_000,
        )


def _normalize_value(value: object) -> NormalizedValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        if not math.isfinite(value):
            raise QueryResultNormalizationError(
                "Non-finite floating-point query result cannot be serialized"
            )
        return value
    raise QueryResultNormalizationError(
        f"Unsupported query result type: {type(value).__name__}"
    )
