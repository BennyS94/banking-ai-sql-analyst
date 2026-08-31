"""Execution of trusted, upstream-approved analytical SQL."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import logging
import math
import time

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, text
from sqlalchemy.exc import (
    DBAPIError,
    InterfaceError,
    OperationalError,
    SQLAlchemyError,
)

from backend.app.core.config import (
    DEFAULT_QUERY_MAX_ROWS,
    DEFAULT_QUERY_STATEMENT_TIMEOUT_MS,
)


logger = logging.getLogger(__name__)
NormalizedValue = int | float | str | bool | None


class QueryExecutorError(RuntimeError):
    """Base class for sanitized hardened-executor failures."""


class QueryTimeoutError(QueryExecutorError):
    """Raised when PostgreSQL cancels a query at statement_timeout."""


class QueryDatabaseError(QueryExecutorError):
    """Raised when the runtime database connection or boundary fails."""


class QueryExecutionError(QueryExecutorError):
    """Raised when PostgreSQL rejects an approved internal query."""

    def __init__(self, message: str, *, repair_detail: str) -> None:
        super().__init__(message)
        self.repair_detail = repair_detail


class QueryResultNormalizationError(RuntimeError):
    """Raised when a result contains a value without an explicit JSON mapping."""


class QueryResult(BaseModel):
    """Serializable rows and execution metadata from an approved query."""

    model_config = ConfigDict(frozen=True)

    columns: tuple[str, ...]
    rows: tuple[tuple[NormalizedValue, ...], ...]
    row_count: int
    truncated: bool
    execution_ms: float
    statement_timeout_ms: int


class ReadOnlyQueryExecutor:
    """Execute SQL already approved by an upstream safety boundary.

    This component deliberately does not parse, validate or approve SQL. Its
    engine must be created through the read-only runtime database boundary.
    """

    def __init__(
        self,
        engine: Engine,
        *,
        statement_timeout_ms: int = DEFAULT_QUERY_STATEMENT_TIMEOUT_MS,
        max_rows: int = DEFAULT_QUERY_MAX_ROWS,
    ) -> None:
        if statement_timeout_ms <= 0:
            raise ValueError("statement_timeout_ms must be positive")
        if max_rows <= 0:
            raise ValueError("max_rows must be positive")
        self._engine = engine
        self._statement_timeout_ms = statement_timeout_ms
        self._max_rows = max_rows

    def execute(self, statement: str) -> QueryResult:
        started = time.perf_counter()
        generated_query_started = False
        try:
            with self._engine.connect() as connection:
                with connection.begin():
                    connection.execute(text("SET TRANSACTION READ ONLY"))
                    connection.execute(
                        text(
                            "SELECT set_config("
                            "'statement_timeout', :timeout_ms, true)"
                        ),
                        {"timeout_ms": str(self._statement_timeout_ms)},
                    )
                    connection.execute(
                        text(
                            "SELECT set_config("
                            "'search_path', 'banking, pg_catalog, pg_temp', true)"
                        )
                    )
                    generated_query_started = True
                    result = connection.execute(text(statement))
                    columns = tuple(result.keys())
                    fetched_rows = result.fetchmany(self._max_rows + 1)
                    truncated = len(fetched_rows) > self._max_rows
                    rows = tuple(
                        tuple(_normalize_value(value) for value in row)
                        for row in fetched_rows[: self._max_rows]
                    )
        except SQLAlchemyError as exc:
            logger.exception("Approved query execution failed")
            if generated_query_started:
                _raise_classified_execution_error(exc)
            raise QueryDatabaseError("Database query infrastructure failed") from exc

        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            execution_ms=(time.perf_counter() - started) * 1_000,
            statement_timeout_ms=self._statement_timeout_ms,
        )


def _raise_classified_execution_error(exc: SQLAlchemyError) -> None:
    sqlstate = _sqlstate(exc)
    if sqlstate == "57014":
        raise QueryTimeoutError("Database query timed out") from exc
    if sqlstate.startswith("08") or (
        not sqlstate and isinstance(exc, (InterfaceError, OperationalError))
    ):
        raise QueryDatabaseError("Database query infrastructure failed") from exc
    raise QueryExecutionError(
        "Database query execution failed",
        repair_detail=_sanitized_repair_detail(exc),
    ) from exc


def _sqlstate(exc: SQLAlchemyError) -> str:
    if not isinstance(exc, DBAPIError):
        return ""
    value = getattr(exc.orig, "sqlstate", None)
    return value if isinstance(value, str) else ""


def _sanitized_repair_detail(exc: SQLAlchemyError) -> str:
    primary_message: object = None
    if isinstance(exc, DBAPIError):
        diagnostics = getattr(exc.orig, "diag", None)
        primary_message = getattr(diagnostics, "message_primary", None)
    if isinstance(primary_message, str) and primary_message.strip():
        return " ".join(primary_message.split())[:500]
    return "PostgreSQL rejected the approved query"


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
