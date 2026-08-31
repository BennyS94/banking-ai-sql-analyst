"""Safe natural-language to SQL execution orchestration."""

from __future__ import annotations

import logging
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from backend.app.ai.groq_client import GenerationMetadata
from backend.app.ai.service import NLToSQLGenerationResult
from backend.app.db.query_executor import QueryResult
from backend.app.safety.sql_validator import (
    SQLSafetyReasonCode,
    SQLValidationResult,
)


logger = logging.getLogger(__name__)


class QuerySafetyError(RuntimeError):
    """Raised when generated SQL fails a deterministic safety policy."""

    def __init__(self, reason_code: SQLSafetyReasonCode) -> None:
        super().__init__("Generated SQL did not pass the safety policy")
        self.reason_code = reason_code


class SafeQueryResult(BaseModel):
    """Internal semantic, generation and optional execution result."""

    model_config = ConfigDict(frozen=True)

    status: Literal["answerable", "unanswerable", "ambiguous"]
    sql: str | None
    message: str | None
    generation_metadata: GenerationMetadata
    query_result: QueryResult | None = None


class _GenerationService(Protocol):
    def generate(self, question: str) -> NLToSQLGenerationResult: ...


class _StructuralValidator(Protocol):
    def validate(self, sql: str) -> SQLValidationResult: ...


class _AccessPolicy(Protocol):
    def validate(
        self, structural_result: SQLValidationResult
    ) -> SQLValidationResult: ...


class _QueryExecutor(Protocol):
    def execute(self, statement: str) -> QueryResult: ...


class SafeQueryService:
    """Run answerable generated SQL only after every deterministic control."""

    def __init__(
        self,
        generation_service: _GenerationService,
        structural_validator: _StructuralValidator,
        access_policy: _AccessPolicy,
        query_executor: _QueryExecutor,
    ) -> None:
        self._generation_service = generation_service
        self._structural_validator = structural_validator
        self._access_policy = access_policy
        self._query_executor = query_executor

    def query(self, question: str) -> SafeQueryResult:
        generation = self._generation_service.generate(question)
        output = generation.output
        if output.status != "answerable":
            logger.info(
                "Banking query completed without SQL execution",
                extra={
                    "semantic_status": output.status,
                    "generation_model": generation.metadata.model,
                },
            )
            return SafeQueryResult(
                status=output.status,
                sql=None,
                message=output.message,
                generation_metadata=generation.metadata,
            )

        sql = output.sql
        if sql is None:  # The Phase 3 structured contract makes this unreachable.
            raise RuntimeError("Answerable generation did not contain SQL")

        structural_result = self._structural_validator.validate(sql)
        self._require_safe(structural_result, generation.metadata)
        access_result = self._access_policy.validate(structural_result)
        self._require_safe(access_result, generation.metadata)

        query_result = self._query_executor.execute(sql)
        logger.info(
            "Banking query executed safely",
            extra={
                "semantic_status": output.status,
                "generation_model": generation.metadata.model,
                "execution_ms": query_result.execution_ms,
                "returned_row_count": query_result.row_count,
                "result_truncated": query_result.truncated,
            },
        )
        return SafeQueryResult(
            status=output.status,
            sql=sql,
            message=None,
            generation_metadata=generation.metadata,
            query_result=query_result,
        )

    @staticmethod
    def _require_safe(
        result: SQLValidationResult, metadata: GenerationMetadata
    ) -> None:
        if result.accepted:
            return
        reason_code = result.reason_code or SQLSafetyReasonCode.UNSUPPORTED_STATEMENT
        logger.warning(
            "Generated SQL rejected by safety policy",
            extra={
                "generation_model": metadata.model,
                "safety_reason_code": reason_code.value,
            },
        )
        raise QuerySafetyError(reason_code)
