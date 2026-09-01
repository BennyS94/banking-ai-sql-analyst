"""Deterministic machine-readable and Markdown evaluation reporting."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import statistics
from typing import Callable, Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from backend.app.evaluation.models import EvaluationCaseResult, EvaluationRun
from backend.app.evaluation.persistence import EvaluationRunStore
from backend.app.evaluation.safety_metrics import SafetyEvaluation


class Metric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    rate_pct: float | None


class LatencySummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_count: int = Field(ge=0)
    average_ms: float | None
    median_ms: float | None
    p95_ms: float | None


class TokenSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_sample_count: int = Field(ge=0)
    average_input_tokens: float | None
    total_input_tokens: int | None
    output_sample_count: int = Field(ge=0)
    average_output_tokens: float | None
    total_output_tokens: int | None
    total_tokens: int | None


class RepairSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repair_rate: Metric
    repair_success_rate: Metric
    attempted_failed_count: int = Field(ge=0)
    not_eligible_count: int = Field(ge=0)


class EvaluationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    model: str
    reasoning_effort: str
    benchmark_case_count: int
    completed_case_count: int
    run_complete: bool
    generation_success: Metric
    semantic_status_accuracy: Metric
    status_accuracy_by_expected: dict[str, Metric]
    execution_success: Metric
    result_accuracy: Metric
    end_to_end_accuracy: Metric
    generated_safety_rejections: int
    safety: SafetyEvaluation | None
    repair: RepairSummary
    latency: dict[str, LatencySummary]
    tokens: TokenSummary
    breakdowns: dict[str, dict[str, Metric]]
    failures: dict[str, int]


def build_evaluation_summary(run: EvaluationRun) -> EvaluationSummary:
    cases = run.cases
    generation_success = _metric(cases, lambda case: case.generation_success)
    status_accuracy = _metric(cases, lambda case: case.status_matched)
    answerable_safety_accepted = tuple(
        case
        for case in cases
        if case.expected_status == "answerable"
        and case.generated_status == "answerable"
        and case.safety_outcome == "accepted"
    )
    execution_success = _metric(
        answerable_safety_accepted,
        lambda case: case.execution_outcome == "success",
    )
    executed_answerable = tuple(
        case
        for case in cases
        if case.expected_status == "answerable"
        and case.execution_outcome == "success"
    )
    result_accuracy = _metric(
        executed_answerable,
        lambda case: case.comparison_matched is True,
    )
    answerable_generated_sql = tuple(
        case
        for case in cases
        if case.expected_status == "answerable"
        and case.generated_status == "answerable"
    )
    repair_attempts = tuple(case for case in cases if case.repair_attempted)
    repair_successes = sum(
        case.repair_used and case.execution_outcome == "success"
        for case in repair_attempts
    )
    failures = Counter(
        case.failure_reason or "incorrect_result"
        for case in cases
        if not case.case_correct
    )
    return EvaluationSummary(
        run_id=run.metadata.run_id,
        model=run.metadata.model,
        reasoning_effort=run.metadata.reasoning_effort,
        benchmark_case_count=run.metadata.benchmark_case_count,
        completed_case_count=len(cases),
        run_complete=len(cases) == run.metadata.benchmark_case_count,
        generation_success=generation_success,
        semantic_status_accuracy=status_accuracy,
        status_accuracy_by_expected=_breakdown(
            cases, lambda case: case.expected_status, lambda case: case.status_matched
        ),
        execution_success=execution_success,
        result_accuracy=result_accuracy,
        end_to_end_accuracy=_metric(cases, lambda case: case.case_correct),
        generated_safety_rejections=sum(
            case.safety_outcome == "rejected" for case in cases
        ),
        safety=run.safety_evaluation,
        repair=RepairSummary(
            repair_rate=_ratio(len(repair_attempts), len(answerable_generated_sql)),
            repair_success_rate=_ratio(repair_successes, len(repair_attempts)),
            attempted_failed_count=len(repair_attempts) - repair_successes,
            not_eligible_count=len(cases) - len(repair_attempts),
        ),
        latency={
            "generation": _latency(case.generation_latency_ms for case in cases),
            "execution": _latency(case.execution_latency_ms for case in cases),
            "end_to_end": _latency(case.end_to_end_latency_ms for case in cases),
        },
        tokens=_tokens(cases),
        breakdowns={
            "language": _breakdown(
                cases, lambda case: case.language, lambda case: case.case_correct
            ),
            "difficulty": _breakdown(
                cases, lambda case: case.difficulty, lambda case: case.case_correct
            ),
            "category": _breakdown(
                cases, lambda case: case.category, lambda case: case.case_correct
            ),
            "expected_status": _breakdown(
                cases,
                lambda case: case.expected_status,
                lambda case: case.case_correct,
            ),
        },
        failures=dict(sorted(failures.items())),
    )


def render_evaluation_report(summary: EvaluationSummary) -> str:
    completion = f"{summary.completed_case_count}/{summary.benchmark_case_count}"
    lines = [
        "# NL-to-SQL Evaluation Report",
        "",
        f"- Run ID: `{summary.run_id}`",
        f"- Model: `{summary.model}`",
        f"- Reasoning effort: `{summary.reasoning_effort}`",
        f"- Coverage: {completion} ({'complete' if summary.run_complete else 'partial'})",
        "",
        "## Core metrics",
        "",
        "| Metric | Result | Denominator |",
        "|---|---:|---:|",
        _metric_row("Generation success", summary.generation_success),
        _metric_row("Semantic status accuracy", summary.semantic_status_accuracy),
        _metric_row("Execution success after safety acceptance", summary.execution_success),
        _metric_row("Result accuracy after execution", summary.result_accuracy),
        _metric_row("End-to-end case accuracy", summary.end_to_end_accuracy),
        "",
        "## Status accuracy",
        "",
        *_metric_table(summary.status_accuracy_by_expected),
        "",
        "## Safety",
        "",
    ]
    if summary.safety is None:
        lines.append("No deterministic safety-corpus snapshot was attached to this run.")
    else:
        safety = summary.safety
        lines.extend(
            (
                "| Metric | Result | Denominator |",
                "|---|---:|---:|",
                _percentage_row(
                    "Adversarial attack block rate",
                    safety.adversarial_block_rate_pct,
                    safety.adversarial_total,
                ),
                _percentage_row(
                    "Legitimate SQL acceptance rate",
                    safety.legitimate_acceptance_rate_pct,
                    safety.legitimate_total,
                ),
                _percentage_row(
                    "Legitimate-query false-positive rejection rate",
                    safety.legitimate_false_positive_rate_pct,
                    safety.legitimate_total,
                ),
            )
        )
    lines.extend(
        (
            "",
            f"Generated benchmark SQL safety rejections: {summary.generated_safety_rejections}",
            "",
            "## Repair",
            "",
            "| Metric | Result | Denominator |",
            "|---|---:|---:|",
            _metric_row("Repair rate", summary.repair.repair_rate),
            _metric_row("Repair success rate", summary.repair.repair_success_rate),
            "",
            f"Failed repair attempts: {summary.repair.attempted_failed_count}",
            f"Cases where repair was not eligible: {summary.repair.not_eligible_count}",
            "",
            "## Latency",
            "",
            "| Stage | Samples | Average | Median | p95 |",
            "|---|---:|---:|---:|---:|",
        )
    )
    for name, latency in summary.latency.items():
        lines.append(
            f"| {name.replace('_', ' ').title()} | {latency.sample_count} | "
            f"{_milliseconds(latency.average_ms)} | {_milliseconds(latency.median_ms)} | "
            f"{_milliseconds(latency.p95_ms)} |"
        )
    lines.extend(
        (
            "",
            "## Token usage",
            "",
            f"- Average input tokens: {_number(summary.tokens.average_input_tokens)}",
            f"- Average output tokens: {_number(summary.tokens.average_output_tokens)}",
            f"- Total tokens: {summary.tokens.total_tokens if summary.tokens.total_tokens is not None else 'N/A'}",
        )
    )
    for dimension, values in summary.breakdowns.items():
        lines.extend(
            (
                "",
                f"## End-to-end accuracy by {dimension.replace('_', ' ')}",
                "",
                *_metric_table(values),
            )
        )
    lines.extend(("", "## Failure analysis", ""))
    if summary.failures:
        lines.extend(
            f"- `{reason}`: {count}" for reason, count in summary.failures.items()
        )
    else:
        lines.append("No incorrect completed cases.")
    return "\n".join(lines) + "\n"


def write_evaluation_reports(
    run: EvaluationRun, output_directory: Path
) -> tuple[Path, Path]:
    summary = build_evaluation_summary(run)
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "evaluation_summary.json"
    markdown_path = output_directory / "evaluation_report.md"
    json_path.write_text(
        json.dumps(
            summary.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    markdown_path.write_text(
        render_evaluation_report(summary), encoding="utf-8", newline="\n"
    )
    return json_path, markdown_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render an evaluation run report")
    parser.add_argument("run", type=Path)
    parser.add_argument("--output-dir", type=Path)
    arguments = parser.parse_args(argv)
    run = EvaluationRunStore(arguments.run).load()
    output = arguments.output_dir or arguments.run.with_suffix("")
    json_path, markdown_path = write_evaluation_reports(run, output)
    print(f"Machine summary: {json_path}")
    print(f"Markdown report: {markdown_path}")
    return 0


def _metric(
    cases: Iterable[EvaluationCaseResult],
    predicate: Callable[[EvaluationCaseResult], bool],
) -> Metric:
    values = tuple(cases)
    return _ratio(sum(predicate(case) for case in values), len(values))


def _ratio(numerator: int, denominator: int) -> Metric:
    return Metric(
        numerator=numerator,
        denominator=denominator,
        rate_pct=round(numerator / denominator * 100, 4) if denominator else None,
    )


def _breakdown(
    cases: Iterable[EvaluationCaseResult],
    key: Callable[[EvaluationCaseResult], str],
    predicate: Callable[[EvaluationCaseResult], bool],
) -> dict[str, Metric]:
    values = tuple(cases)
    return {
        name: _metric(
            (case for case in values if key(case) == name), predicate
        )
        for name in sorted({key(case) for case in values})
    }


def _latency(values: Iterable[float | None]) -> LatencySummary:
    samples = sorted(value for value in values if value is not None)
    if not samples:
        return LatencySummary(
            sample_count=0, average_ms=None, median_ms=None, p95_ms=None
        )
    p95_index = max(0, math.ceil(0.95 * len(samples)) - 1)
    return LatencySummary(
        sample_count=len(samples),
        average_ms=round(statistics.fmean(samples), 4),
        median_ms=round(statistics.median(samples), 4),
        p95_ms=round(samples[p95_index], 4),
    )


def _tokens(cases: Iterable[EvaluationCaseResult]) -> TokenSummary:
    values = tuple(cases)
    inputs = [case.input_tokens for case in values if case.input_tokens is not None]
    outputs = [case.output_tokens for case in values if case.output_tokens is not None]
    input_total = sum(inputs) if inputs else None
    output_total = sum(outputs) if outputs else None
    return TokenSummary(
        input_sample_count=len(inputs),
        average_input_tokens=round(statistics.fmean(inputs), 4) if inputs else None,
        total_input_tokens=input_total,
        output_sample_count=len(outputs),
        average_output_tokens=round(statistics.fmean(outputs), 4) if outputs else None,
        total_output_tokens=output_total,
        total_tokens=(
            input_total + output_total
            if input_total is not None and output_total is not None
            else None
        ),
    )


def _metric_row(label: str, metric: Metric) -> str:
    return _percentage_row(label, metric.rate_pct, metric.denominator)


def _percentage_row(label: str, value: float | None, denominator: int) -> str:
    return f"| {label} | {_percentage(value)} | {denominator} |"


def _metric_table(values: dict[str, Metric]) -> list[str]:
    rows = ["| Value | Accuracy | Correct / Total |", "|---|---:|---:|"]
    rows.extend(
        f"| {name} | {_percentage(metric.rate_pct)} | "
        f"{metric.numerator} / {metric.denominator} |"
        for name, metric in values.items()
    )
    return rows


def _percentage(value: float | None) -> str:
    return f"{value:.2f}%" if value is not None else "N/A"


def _milliseconds(value: float | None) -> str:
    return f"{value:.2f} ms" if value is not None else "N/A"


def _number(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "N/A"


if __name__ == "__main__":
    raise SystemExit(main())
