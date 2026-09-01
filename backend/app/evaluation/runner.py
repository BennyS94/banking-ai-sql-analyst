"""Evaluation orchestration through the complete safe query pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import subprocess
import time
from typing import Any, Protocol, Sequence
from uuid import uuid4

from backend.app.ai.benchmark import BenchmarkCase
from backend.app.ai.groq_client import (
    GenerationMetadata,
    GroqConfigurationError,
    GroqRateLimitError,
    GroqRequestError,
    GroqTimeoutError,
    GroqUnavailableError,
    InvalidStructuredResponseError,
    ReasoningEffort,
)
from backend.app.ai.service import NLToSQLGenerationResult
from backend.app.db.query_executor import (
    QueryDatabaseError,
    QueryExecutionError,
    QueryResult,
    QueryTimeoutError,
)
from backend.app.evaluation.comparison import compare_query_results
from backend.app.evaluation.models import EvaluationCaseResult, EvaluationRunMetadata
from backend.app.query.service import (
    QueryRepairError,
    QuerySafetyError,
    SafeQueryService,
)


_PROVIDER_ERRORS = (
    GroqConfigurationError,
    GroqRateLimitError,
    GroqRequestError,
    GroqTimeoutError,
    GroqUnavailableError,
    InvalidStructuredResponseError,
)


class EvaluationReferenceError(RuntimeError):
    """Raised when trusted benchmark SQL cannot produce a reference result."""


class _GenerationService(Protocol):
    def generate(self, question: str) -> NLToSQLGenerationResult: ...

    def repair(
        self, question: str, previous_sql: str, sanitized_error: str
    ) -> NLToSQLGenerationResult: ...


class _Validator(Protocol):
    def validate(self, value: Any) -> Any: ...


class _Executor(Protocol):
    def execute(self, statement: str) -> QueryResult: ...


class RecordingGenerationService:
    """Transparent recorder around the real generation and repair boundary."""

    def __init__(self, service: _GenerationService) -> None:
        self._service = service
        self.initial: NLToSQLGenerationResult | None = None
        self.repair_result: NLToSQLGenerationResult | None = None
        self.repair_attempted = False

    def reset(self) -> None:
        self.initial = None
        self.repair_result = None
        self.repair_attempted = False

    def generate(self, question: str) -> NLToSQLGenerationResult:
        self.initial = self._service.generate(question)
        return self.initial

    def repair(
        self, question: str, previous_sql: str, sanitized_error: str
    ) -> NLToSQLGenerationResult:
        self.repair_attempted = True
        self.repair_result = self._service.repair(
            question, previous_sql, sanitized_error
        )
        return self.repair_result


class EvaluationRunner:
    """Score benchmark cases without bypassing production safety orchestration."""

    def __init__(
        self,
        generation_service: _GenerationService,
        structural_validator: _Validator,
        access_policy: _Validator,
        generated_executor: _Executor,
        reference_executor: _Executor,
    ) -> None:
        self._generation = RecordingGenerationService(generation_service)
        self._safe_query = SafeQueryService(
            self._generation,
            structural_validator,
            access_policy,
            generated_executor,
        )
        self._reference_executor = reference_executor

    def run_case(self, case: BenchmarkCase) -> EvaluationCaseResult:
        self._generation.reset()
        reference_result = self._reference_result(case)
        started = time.perf_counter()
        if case.expected_status != "answerable":
            return self._run_semantic_case(case, started)
        try:
            safe_result = self._safe_query.query(case.question)
        except _PROVIDER_ERRORS as exc:
            return self._failure(case, started, "provider_error", type(exc).__name__)
        except QuerySafetyError as exc:
            return self._failure(
                case,
                started,
                "safety_rejected",
                exc.reason_code.value,
                safety_reason=exc.reason_code.value,
            )
        except QueryTimeoutError:
            return self._failure(case, started, "timeout", "query_timeout")
        except QueryDatabaseError:
            return self._failure(case, started, "database_error", "database_error")
        except QueryExecutionError:
            return self._failure(case, started, "execution_error", "execution_error")
        except QueryRepairError:
            return self._failure(case, started, "repair_error", "repair_error")

        metadata = _combined_metadata(self._generation)
        generated_status = safe_result.status
        status_matched = generated_status == case.expected_status
        if generated_status != "answerable":
            return EvaluationCaseResult(
                **self._base_fields(case, started, metadata),
                generated_status=generated_status,
                generation_success=True,
                status_matched=status_matched,
                safety_outcome="not_applicable",
                repair_attempted=False,
                repair_used=False,
                execution_outcome="not_applicable",
                case_correct=status_matched,
                failure_reason=None if status_matched else "status_mismatch",
            )

        if safe_result.query_result is None or reference_result is None:
            raise RuntimeError("answerable evaluation did not produce both results")
        generated_result = safe_result.query_result
        if generated_result.truncated or reference_result.truncated:
            comparison_matched = False
            comparison_reason = "truncated_result"
        else:
            comparison = compare_query_results(
                generated_result.rows,
                reference_result.rows,
                case.comparison_mode or "unordered_rows",
                generated_column_count=len(generated_result.columns),
                reference_column_count=len(reference_result.columns),
                numeric_tolerance=case.numeric_tolerance,
            )
            comparison_matched = comparison.matched
            comparison_reason = comparison.reason
        case_correct = status_matched and comparison_matched
        return EvaluationCaseResult(
            **self._base_fields(case, started, metadata),
            generated_status=generated_status,
            generation_success=True,
            status_matched=status_matched,
            generated_sql=safe_result.sql,
            safety_outcome="accepted",
            repair_attempted=self._generation.repair_attempted,
            repair_used=safe_result.repair_used,
            execution_outcome="success",
            comparison_matched=comparison_matched,
            comparison_reason=comparison_reason,
            case_correct=case_correct,
            execution_latency_ms=generated_result.execution_ms,
            failure_reason=None if case_correct else comparison_reason,
        )

    def _run_semantic_case(
        self, case: BenchmarkCase, started: float
    ) -> EvaluationCaseResult:
        """Score expected non-SQL statuses without executing model-selected SQL."""
        try:
            generation = self._generation.generate(case.question)
        except _PROVIDER_ERRORS as exc:
            return self._failure(case, started, "provider_error", type(exc).__name__)
        metadata = _combined_metadata(self._generation)
        status = generation.output.status
        status_matched = status == case.expected_status
        return EvaluationCaseResult(
            **self._base_fields(case, started, metadata),
            generated_status=status,
            generation_success=True,
            status_matched=status_matched,
            generated_sql=generation.output.sql,
            safety_outcome="not_applicable",
            execution_outcome="not_applicable",
            case_correct=status_matched,
            failure_reason=None if status_matched else "status_mismatch",
        )

    def _reference_result(self, case: BenchmarkCase) -> QueryResult | None:
        if case.expected_status != "answerable":
            return None
        try:
            return self._reference_executor.execute(case.reference_sql or "")
        except (QueryTimeoutError, QueryDatabaseError, QueryExecutionError) as exc:
            raise EvaluationReferenceError(
                f"reference SQL failed for {case.id}"
            ) from exc

    def _failure(
        self,
        case: BenchmarkCase,
        started: float,
        outcome: str,
        failure_reason: str,
        *,
        safety_reason: str | None = None,
    ) -> EvaluationCaseResult:
        metadata = _combined_metadata(self._generation)
        initial = self._generation.initial
        latest = self._generation.repair_result or initial
        status = initial.output.status if initial is not None else None
        generated_sql = latest.output.sql if latest is not None else None
        safety_outcome = (
            "rejected"
            if outcome == "safety_rejected"
            else "accepted"
            if status == "answerable"
            else "not_applicable"
        )
        return EvaluationCaseResult(
            **self._base_fields(case, started, metadata),
            generated_status=status,
            generation_success=initial is not None,
            status_matched=status == case.expected_status,
            generated_sql=generated_sql,
            safety_outcome=safety_outcome,
            safety_reason=safety_reason,
            repair_attempted=self._generation.repair_attempted,
            repair_used=False,
            execution_outcome=outcome,
            comparison_matched=False if case.expected_status == "answerable" else None,
            comparison_reason=failure_reason,
            case_correct=False,
            failure_reason=failure_reason,
        )

    def _base_fields(
        self,
        case: BenchmarkCase,
        started: float,
        metadata: GenerationMetadata | None,
    ) -> dict[str, Any]:
        return {
            "benchmark_id": case.id,
            "category": case.category,
            "difficulty": case.difficulty,
            "language": case.language,
            "expected_status": case.expected_status,
            "comparison_mode": case.comparison_mode,
            "reference_sql": case.reference_sql,
            "generation_latency_ms": metadata.latency_ms if metadata else None,
            "end_to_end_latency_ms": (time.perf_counter() - started) * 1_000,
            "input_tokens": metadata.input_tokens if metadata else None,
            "output_tokens": metadata.output_tokens if metadata else None,
        }


def build_run_metadata(
    cases: Sequence[BenchmarkCase],
    *,
    model: str,
    reasoning_effort: ReasoningEffort,
    prompt_context_fingerprint: str,
    generation_configuration: dict[str, Any],
    run_id: str | None = None,
) -> EvaluationRunMetadata:
    benchmark_payload = [case.model_dump(mode="json") for case in cases]
    benchmark_fingerprint = _fingerprint(benchmark_payload)
    compatibility = {
        "model": model,
        "reasoning_effort": reasoning_effort,
        "benchmark_fingerprint": benchmark_fingerprint,
        "prompt_context_fingerprint": prompt_context_fingerprint,
        "generation_configuration": generation_configuration,
    }
    return EvaluationRunMetadata(
        run_id=run_id or uuid4().hex,
        model=model,
        reasoning_effort=reasoning_effort,
        started_at=datetime.now(UTC).isoformat(),
        benchmark_case_count=len(cases),
        benchmark_fingerprint=benchmark_fingerprint,
        git_commit_sha=_git_commit_sha(),
        prompt_context_fingerprint=prompt_context_fingerprint,
        generation_configuration=generation_configuration,
        configuration_fingerprint=_fingerprint(compatibility),
    )


def fingerprint_prompt(prompt_messages: Sequence[dict[str, str]]) -> str:
    return _fingerprint(list(prompt_messages))


def _combined_metadata(
    recorder: RecordingGenerationService,
) -> GenerationMetadata | None:
    values = [
        result.metadata
        for result in (recorder.initial, recorder.repair_result)
        if result is not None
    ]
    if not values:
        return None
    latest = values[-1]
    return latest.model_copy(
        update={
            "latency_ms": sum(value.latency_ms for value in values),
            "input_tokens": _optional_sum(value.input_tokens for value in values),
            "output_tokens": _optional_sum(value.output_tokens for value in values),
        }
    )


def _optional_sum(values: Any) -> int | None:
    items = list(values)
    return sum(items) if items and all(item is not None for item in items) else None


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _git_commit_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None
