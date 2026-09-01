"""Machine-readable models for evaluation runs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.ai.benchmark import BenchmarkCategory, ComparisonMode
from backend.app.ai.groq_client import GenerationStatus, ReasoningEffort
from backend.app.evaluation.safety_metrics import SafetyEvaluation


SafetyOutcome = Literal["not_applicable", "accepted", "rejected"]
ExecutionOutcome = Literal[
    "not_applicable",
    "success",
    "provider_error",
    "safety_rejected",
    "timeout",
    "execution_error",
    "database_error",
    "repair_error",
]


class EvaluationRunMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    model: str
    reasoning_effort: ReasoningEffort
    started_at: str
    benchmark_case_count: int = Field(ge=1)
    benchmark_fingerprint: str
    git_commit_sha: str | None
    prompt_context_fingerprint: str
    generation_configuration: dict[str, Any]
    configuration_fingerprint: str


class EvaluationCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    benchmark_id: str
    category: BenchmarkCategory
    difficulty: Literal["easy", "medium", "hard"]
    language: Literal["en", "ro"]
    expected_status: GenerationStatus
    generated_status: GenerationStatus | None = None
    comparison_mode: ComparisonMode | None = None
    generation_success: bool
    status_matched: bool
    generated_sql: str | None = None
    reference_sql: str | None = None
    safety_outcome: SafetyOutcome
    safety_reason: str | None = None
    repair_attempted: bool = False
    repair_used: bool = False
    execution_outcome: ExecutionOutcome
    comparison_matched: bool | None = None
    comparison_reason: str | None = None
    case_correct: bool
    generation_latency_ms: float | None = Field(default=None, ge=0)
    execution_latency_ms: float | None = Field(default=None, ge=0)
    end_to_end_latency_ms: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    failure_reason: str | None = None


class EvaluationRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metadata: EvaluationRunMetadata
    safety_evaluation: SafetyEvaluation | None = None
    cases: tuple[EvaluationCaseResult, ...] = ()
