"""Controlled GPT-OSS evaluation-run comparison and recommendation inputs."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
from typing import Sequence

from pydantic import BaseModel, ConfigDict

from backend.app.evaluation.models import EvaluationRun
from backend.app.evaluation.persistence import EvaluationRunStore
from backend.app.evaluation.reporting import (
    EvaluationSummary,
    Metric,
    build_evaluation_summary,
)


CANDIDATE_MODELS = ("openai/gpt-oss-20b", "openai/gpt-oss-120b")


class ModelComparisonError(RuntimeError):
    """Raised when model runs cannot support a fair comparison."""


class ModelMetricSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    generation_success: Metric
    semantic_status_accuracy: Metric
    execution_success: Metric
    result_accuracy: Metric
    end_to_end_accuracy: Metric
    repair_rate: Metric
    repair_success_rate: Metric
    safety_rejections: int
    average_generation_latency_ms: float | None
    median_generation_latency_ms: float | None
    p95_generation_latency_ms: float | None
    total_tokens: int | None
    failures: dict[str, int]


class StabilitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repeat_count: int
    case_count_per_repeat: int
    average_end_to_end_accuracy_pct: float
    minimum_end_to_end_accuracy_pct: float
    maximum_end_to_end_accuracy_pct: float


class ModelComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    benchmark_fingerprint: str
    reasoning_effort: str
    benchmark_case_count: int
    models: dict[str, ModelMetricSnapshot]
    language_accuracy: dict[str, dict[str, Metric]]
    difficulty_accuracy: dict[str, dict[str, Metric]]
    category_accuracy: dict[str, dict[str, Metric]]
    stability: dict[str, StabilitySummary]
    recommendation_inputs: dict[str, dict[str, float | int | None]]
    recommended_model: str
    technical_recommendation: str


def compare_model_runs(
    runs: Sequence[EvaluationRun],
    stability_runs: Sequence[EvaluationRun] = (),
) -> ModelComparison:
    by_model = _candidate_runs(runs)
    _validate_controlled_runs(tuple(by_model.values()))
    summaries = {
        model: build_evaluation_summary(run) for model, run in by_model.items()
    }
    snapshots = {
        model: _snapshot(summary) for model, summary in summaries.items()
    }
    recommendation_inputs = {
        model: _recommendation_inputs(summary)
        for model, summary in summaries.items()
    }
    recommended = _recommend(recommendation_inputs)
    other = next(model for model in CANDIDATE_MODELS if model != recommended)
    recommendation = _recommendation_text(
        recommended,
        other,
        recommendation_inputs[recommended],
        recommendation_inputs[other],
    )
    first_run = by_model[CANDIDATE_MODELS[0]]
    return ModelComparison(
        benchmark_fingerprint=first_run.metadata.benchmark_fingerprint,
        reasoning_effort=first_run.metadata.reasoning_effort,
        benchmark_case_count=first_run.metadata.benchmark_case_count,
        models=snapshots,
        language_accuracy=_aligned_breakdown(summaries, "language"),
        difficulty_accuracy=_aligned_breakdown(summaries, "difficulty"),
        category_accuracy=_aligned_breakdown(summaries, "category"),
        stability=_stability(stability_runs),
        recommendation_inputs=recommendation_inputs,
        recommended_model=recommended,
        technical_recommendation=recommendation,
    )


def render_model_comparison(comparison: ModelComparison) -> str:
    model_20b, model_120b = CANDIDATE_MODELS
    left = comparison.models[model_20b]
    right = comparison.models[model_120b]
    lines = [
        "# GPT-OSS Model Comparison",
        "",
        f"- Benchmark cases: {comparison.benchmark_case_count}",
        f"- Reasoning effort: `{comparison.reasoning_effort}`",
        f"- Benchmark fingerprint: `{comparison.benchmark_fingerprint}`",
        "",
        f"| Metric | `{model_20b}` | `{model_120b}` |",
        "|---|---:|---:|",
        _comparison_metric_row("Generation success", left.generation_success, right.generation_success),
        _comparison_metric_row("Semantic status accuracy", left.semantic_status_accuracy, right.semantic_status_accuracy),
        _comparison_metric_row("Execution success", left.execution_success, right.execution_success),
        _comparison_metric_row("Result accuracy", left.result_accuracy, right.result_accuracy),
        _comparison_metric_row("End-to-end accuracy", left.end_to_end_accuracy, right.end_to_end_accuracy),
        _comparison_metric_row("Repair rate", left.repair_rate, right.repair_rate),
        _comparison_metric_row("Repair success rate", left.repair_success_rate, right.repair_success_rate),
        _comparison_value_row("Generated safety rejections", left.safety_rejections, right.safety_rejections),
        _comparison_value_row("Average generation latency", left.average_generation_latency_ms, right.average_generation_latency_ms, " ms"),
        _comparison_value_row("Median generation latency", left.median_generation_latency_ms, right.median_generation_latency_ms, " ms"),
        _comparison_value_row("p95 generation latency", left.p95_generation_latency_ms, right.p95_generation_latency_ms, " ms"),
        _comparison_value_row("Total tokens", left.total_tokens, right.total_tokens),
    ]
    for title, values in (
        ("Language", comparison.language_accuracy),
        ("Difficulty", comparison.difficulty_accuracy),
        ("Category", comparison.category_accuracy),
    ):
        lines.extend(("", f"## {title} end-to-end accuracy", ""))
        lines.extend(_breakdown_table(values))
    lines.extend(("", "## Observed failure groups", ""))
    failure_names = sorted(set(left.failures) | set(right.failures))
    if failure_names:
        lines.extend(
            (
                f"| Failure | `{model_20b}` | `{model_120b}` |",
                "|---|---:|---:|",
            )
        )
        lines.extend(
            f"| `{name}` | {left.failures.get(name, 0)} | "
            f"{right.failures.get(name, 0)} |"
            for name in failure_names
        )
    else:
        lines.append("Neither completed run recorded an incorrect case.")
    if comparison.stability:
        lines.extend(("", "## Repeated-subset stability", ""))
        for model in CANDIDATE_MODELS:
            item = comparison.stability[model]
            lines.append(
                f"- `{model}`: {item.repeat_count} repeats, "
                f"average {item.average_end_to_end_accuracy_pct:.2f}%, "
                f"range {item.minimum_end_to_end_accuracy_pct:.2f}%–"
                f"{item.maximum_end_to_end_accuracy_pct:.2f}%"
            )
    lines.extend(
        (
            "",
            "## Technical recommendation",
            "",
            comparison.technical_recommendation,
            "",
            "This report does not mutate the configured project default.",
        )
    )
    return "\n".join(lines) + "\n"


def write_model_comparison(
    comparison: ModelComparison, output_directory: Path
) -> tuple[Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "model_comparison.json"
    markdown_path = output_directory / "model_comparison.md"
    json_path.write_text(
        json.dumps(
            comparison.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    markdown_path.write_text(
        render_model_comparison(comparison), encoding="utf-8", newline="\n"
    )
    return json_path, markdown_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare controlled GPT-OSS runs")
    parser.add_argument("--run", action="append", type=Path, required=True)
    parser.add_argument("--stability-run", action="append", type=Path, default=[])
    parser.add_argument(
        "--output-dir", type=Path, default=Path("evaluation/results/comparison")
    )
    arguments = parser.parse_args(argv)
    runs = [EvaluationRunStore(path).load() for path in arguments.run]
    stability = [
        EvaluationRunStore(path).load() for path in arguments.stability_run
    ]
    comparison = compare_model_runs(runs, stability)
    json_path, markdown_path = write_model_comparison(
        comparison, arguments.output_dir
    )
    print(f"Machine comparison: {json_path}")
    print(f"Markdown comparison: {markdown_path}")
    print(comparison.technical_recommendation)
    return 0


def _candidate_runs(runs: Sequence[EvaluationRun]) -> dict[str, EvaluationRun]:
    if len(runs) != len(CANDIDATE_MODELS):
        raise ModelComparisonError("exactly one complete run per candidate is required")
    by_model = {run.metadata.model: run for run in runs}
    if set(by_model) != set(CANDIDATE_MODELS):
        raise ModelComparisonError("runs must use the two configured candidate models")
    return by_model


def _validate_controlled_runs(runs: Sequence[EvaluationRun]) -> None:
    for run in runs:
        if len(run.cases) != run.metadata.benchmark_case_count:
            raise ModelComparisonError("incomplete runs cannot be compared")
        if len({case.benchmark_id for case in run.cases}) != len(run.cases):
            raise ModelComparisonError("run contains duplicate benchmark IDs")
    first, second = runs
    conditions = (
        first.metadata.benchmark_fingerprint == second.metadata.benchmark_fingerprint,
        first.metadata.prompt_context_fingerprint == second.metadata.prompt_context_fingerprint,
        first.metadata.reasoning_effort == second.metadata.reasoning_effort,
        first.metadata.generation_configuration == second.metadata.generation_configuration,
        first.metadata.git_commit_sha == second.metadata.git_commit_sha,
        {case.benchmark_id for case in first.cases}
        == {case.benchmark_id for case in second.cases},
    )
    if not all(conditions):
        raise ModelComparisonError("model runs do not share controlled conditions")


def _snapshot(summary: EvaluationSummary) -> ModelMetricSnapshot:
    generation_latency = summary.latency["generation"]
    return ModelMetricSnapshot(
        generation_success=summary.generation_success,
        semantic_status_accuracy=summary.semantic_status_accuracy,
        execution_success=summary.execution_success,
        result_accuracy=summary.result_accuracy,
        end_to_end_accuracy=summary.end_to_end_accuracy,
        repair_rate=summary.repair.repair_rate,
        repair_success_rate=summary.repair.repair_success_rate,
        safety_rejections=summary.generated_safety_rejections,
        average_generation_latency_ms=generation_latency.average_ms,
        median_generation_latency_ms=generation_latency.median_ms,
        p95_generation_latency_ms=generation_latency.p95_ms,
        total_tokens=summary.tokens.total_tokens,
        failures=summary.failures,
    )


def _aligned_breakdown(
    summaries: dict[str, EvaluationSummary], dimension: str
) -> dict[str, dict[str, Metric]]:
    keys = {
        key
        for summary in summaries.values()
        for key in summary.breakdowns[dimension]
    }
    return {
        key: {
            model: summaries[model].breakdowns[dimension][key]
            for model in CANDIDATE_MODELS
        }
        for key in sorted(keys)
    }


def _recommendation_inputs(
    summary: EvaluationSummary,
) -> dict[str, float | int | None]:
    def rate(dimension: str, value: str) -> float | None:
        metric = summary.breakdowns[dimension].get(value)
        return metric.rate_pct if metric is not None else None

    return {
        "end_to_end_accuracy_pct": summary.end_to_end_accuracy.rate_pct,
        "result_accuracy_pct": summary.result_accuracy.rate_pct,
        "semantic_status_accuracy_pct": summary.semantic_status_accuracy.rate_pct,
        "romanian_accuracy_pct": rate("language", "ro"),
        "hard_accuracy_pct": rate("difficulty", "hard"),
        "generation_success_pct": summary.generation_success.rate_pct,
        "repair_rate_pct": summary.repair.repair_rate.rate_pct,
        "median_generation_latency_ms": summary.latency["generation"].median_ms,
        "p95_generation_latency_ms": summary.latency["generation"].p95_ms,
        "safety_rejections": summary.generated_safety_rejections,
    }


def _recommend(
    inputs: dict[str, dict[str, float | int | None]]
) -> str:
    def descending(value: float | int | None) -> float:
        return float(value) if value is not None else float("-inf")

    def ascending(value: float | int | None) -> float:
        return -float(value) if value is not None else float("-inf")

    def score(model: str) -> tuple[float, ...]:
        item = inputs[model]
        return (
            descending(item["end_to_end_accuracy_pct"]),
            descending(item["result_accuracy_pct"]),
            descending(item["romanian_accuracy_pct"]),
            descending(item["hard_accuracy_pct"]),
            descending(item["semantic_status_accuracy_pct"]),
            descending(item["generation_success_pct"]),
            ascending(item["repair_rate_pct"]),
            ascending(item["median_generation_latency_ms"]),
        )

    left = inputs[CANDIDATE_MODELS[0]]
    right = inputs[CANDIDATE_MODELS[1]]
    if (
        _difference(left["end_to_end_accuracy_pct"], right["end_to_end_accuracy_pct"])
        <= 2.0
        and _difference(left["result_accuracy_pct"], right["result_accuracy_pct"])
        <= 0.5
        and _difference(left["romanian_accuracy_pct"], right["romanian_accuracy_pct"])
        <= 0.5
    ):
        return min(
            CANDIDATE_MODELS,
            key=lambda model: float(
                inputs[model]["median_generation_latency_ms"] or float("inf")
            ),
        )
    return max(CANDIDATE_MODELS, key=score)


def _recommendation_text(
    recommended: str,
    other: str,
    winner: dict[str, float | int | None],
    loser: dict[str, float | int | None],
) -> str:
    latency_ratio = _ratio_text(
        loser["median_generation_latency_ms"],
        winner["median_generation_latency_ms"],
    )
    return (
        f"Recommend `{recommended}` for Phase 6 Review. It measured "
        f"{_format_value(winner['end_to_end_accuracy_pct'], '%')} end-to-end, "
        f"{_format_value(winner['result_accuracy_pct'], '%')} result, "
        f"{_format_value(winner['romanian_accuracy_pct'], '%')} Romanian, and "
        f"{_format_value(winner['hard_accuracy_pct'], '%')} hard-case accuracy, "
        f"with {_format_value(winner['median_generation_latency_ms'], ' ms')} median "
        f"generation latency. `{other}` measured "
        f"{_format_value(loser['end_to_end_accuracy_pct'], '%')}, "
        f"{_format_value(loser['result_accuracy_pct'], '%')}, "
        f"{_format_value(loser['romanian_accuracy_pct'], '%')}, and "
        f"{_format_value(loser['hard_accuracy_pct'], '%')} respectively, with "
        f"{_format_value(loser['median_generation_latency_ms'], ' ms')} median latency"
        f"{latency_ratio}. The rule favors lower latency only when result and Romanian "
        "accuracy are effectively tied and end-to-end accuracy is within two percentage "
        "points; optional repeated-subset runs would strengthen the final stability decision."
    )


def _stability(runs: Sequence[EvaluationRun]) -> dict[str, StabilitySummary]:
    if not runs:
        return {}
    grouped: dict[str, list[EvaluationRun]] = defaultdict(list)
    for run in runs:
        if run.metadata.model not in CANDIDATE_MODELS:
            raise ModelComparisonError("stability run has an unknown model label")
        grouped[run.metadata.model].append(run)
    if set(grouped) != set(CANDIDATE_MODELS):
        raise ModelComparisonError("stability runs must cover both candidate models")
    counts = {model: len(items) for model, items in grouped.items()}
    if len(set(counts.values())) != 1:
        raise ModelComparisonError("stability repeat counts must match")
    all_runs = [run for items in grouped.values() for run in items]
    reference = all_runs[0]
    reference_ids = {case.benchmark_id for case in reference.cases}
    for run in all_runs:
        if len(run.cases) != run.metadata.benchmark_case_count:
            raise ModelComparisonError("incomplete stability run")
        if (
            {case.benchmark_id for case in run.cases} != reference_ids
            or run.metadata.benchmark_fingerprint != reference.metadata.benchmark_fingerprint
            or run.metadata.prompt_context_fingerprint
            != reference.metadata.prompt_context_fingerprint
            or run.metadata.reasoning_effort != reference.metadata.reasoning_effort
            or run.metadata.generation_configuration
            != reference.metadata.generation_configuration
            or run.metadata.git_commit_sha != reference.metadata.git_commit_sha
        ):
            raise ModelComparisonError("stability subsets are not comparable")
    output = {}
    for model in CANDIDATE_MODELS:
        rates = [
            build_evaluation_summary(run).end_to_end_accuracy.rate_pct or 0
            for run in grouped[model]
        ]
        output[model] = StabilitySummary(
            repeat_count=len(rates),
            case_count_per_repeat=len(reference_ids),
            average_end_to_end_accuracy_pct=round(statistics.fmean(rates), 4),
            minimum_end_to_end_accuracy_pct=min(rates),
            maximum_end_to_end_accuracy_pct=max(rates),
        )
    return output


def _comparison_metric_row(label: str, left: Metric, right: Metric) -> str:
    return (
        f"| {label} | {_format_value(left.rate_pct, '%')} | "
        f"{_format_value(right.rate_pct, '%')} |"
    )


def _comparison_value_row(
    label: str,
    left: float | int | None,
    right: float | int | None,
    suffix: str = "",
) -> str:
    return (
        f"| {label} | {_format_value(left, suffix)} | "
        f"{_format_value(right, suffix)} |"
    )


def _breakdown_table(values: dict[str, dict[str, Metric]]) -> list[str]:
    left, right = CANDIDATE_MODELS
    rows = [
        f"| Value | `{left}` | `{right}` |",
        "|---|---:|---:|",
    ]
    rows.extend(
        f"| {value} | {_format_value(metrics[left].rate_pct, '%')} | "
        f"{_format_value(metrics[right].rate_pct, '%')} |"
        for value, metrics in values.items()
    )
    return rows


def _format_value(value: float | int | None, suffix: str = "") -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.2f}{suffix}"
    return f"{value}{suffix}"


def _difference(left: float | int | None, right: float | int | None) -> float:
    if left is None or right is None:
        return float("inf")
    return abs(float(left) - float(right))


def _ratio_text(
    other_latency: float | int | None,
    recommended_latency: float | int | None,
) -> str:
    if (
        other_latency is None
        or recommended_latency is None
        or float(recommended_latency) <= 0
    ):
        return ""
    ratio = float(other_latency) / float(recommended_latency)
    return f" ({ratio:.1f}x the recommended model)"


if __name__ == "__main__":
    raise SystemExit(main())
