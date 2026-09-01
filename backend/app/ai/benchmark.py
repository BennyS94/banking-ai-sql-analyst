"""Validation and loading for the independent banking query benchmark."""

from __future__ import annotations

from decimal import Decimal
from importlib.resources import files
import json
from pathlib import Path
import re
from typing import Literal, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

from backend.app.ai.groq_client import GenerationStatus
from backend.app.ai.prompt import FewShotExample


BenchmarkCategory = Literal[
    "filter",
    "aggregation",
    "grouping",
    "single_join",
    "multi_table_join",
    "ranking",
    "temporal",
    "having",
    "subquery_cte",
    "window_function",
    "unanswerable",
    "ambiguous",
]
ComparisonMode = Literal["scalar", "ordered_rows", "unordered_rows"]


class BenchmarkValidationError(RuntimeError):
    """Raised when benchmark structure or separation is invalid."""


class BenchmarkCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    category: BenchmarkCategory
    difficulty: Literal["easy", "medium", "hard"]
    language: Literal["en", "ro"]
    question: str
    expected_status: GenerationStatus
    comparison_mode: ComparisonMode | None = None
    numeric_tolerance: Decimal | None = None
    reference_sql: str | None

    @field_validator("id", "question")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("benchmark text must be non-empty")
        return value.strip()

    @model_validator(mode="after")
    def enforce_reference_sql_contract(self) -> "BenchmarkCase":
        sql = self.reference_sql.strip() if self.reference_sql is not None else None
        if self.expected_status == "answerable" and not sql:
            raise ValueError("answerable benchmark cases require reference SQL")
        if self.expected_status == "answerable" and self.comparison_mode is None:
            raise ValueError("answerable benchmark cases require a comparison mode")
        if self.expected_status != "answerable" and self.reference_sql is not None:
            raise ValueError("non-answerable benchmark cases require null SQL")
        if self.expected_status != "answerable" and self.comparison_mode is not None:
            raise ValueError("non-answerable benchmark cases require no comparison mode")
        if self.expected_status != "answerable" and self.numeric_tolerance is not None:
            raise ValueError("non-answerable benchmark cases require no numeric tolerance")
        if self.numeric_tolerance is not None and self.numeric_tolerance < 0:
            raise ValueError("numeric tolerance must be non-negative")
        return self


def load_banking_benchmark(path: Path | None = None) -> tuple[BenchmarkCase, ...]:
    """Load benchmark cases and enforce stable IDs/questions and contracts."""
    resource = path or Path(
        str(files("backend.app.ai.resources").joinpath("banking_benchmark.json"))
    )
    try:
        payload = json.loads(resource.read_text(encoding="utf-8"))
        cases = tuple(BenchmarkCase.model_validate(item) for item in payload)
    except (OSError, TypeError, json.JSONDecodeError, ValidationError) as exc:
        raise BenchmarkValidationError(
            "Banking benchmark is missing or invalid"
        ) from exc

    if not 20 <= len(cases) <= 25:
        raise BenchmarkValidationError("Banking benchmark must contain 20 to 25 cases")
    ids = [case.id for case in cases]
    questions = [_normalize_question(case.question) for case in cases]
    if len(ids) != len(set(ids)):
        raise BenchmarkValidationError("Benchmark IDs must be unique")
    if len(questions) != len(set(questions)):
        raise BenchmarkValidationError("Benchmark questions must be unique")
    return cases


def validate_few_shot_separation(
    cases: Sequence[BenchmarkCase], examples: Sequence[FewShotExample]
) -> None:
    """Reject normalized exact question overlap with prompt examples."""
    benchmark_questions = {_normalize_question(case.question) for case in cases}
    few_shot_questions = {
        _normalize_question(example.question) for example in examples
    }
    if benchmark_questions & few_shot_questions:
        raise BenchmarkValidationError(
            "Benchmark questions must not overlap few-shot examples"
        )


def _normalize_question(question: str) -> str:
    return re.sub(r"\s+", " ", question).strip().casefold()
