from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from backend.app.evaluation.models import (
    EvaluationCaseResult,
    EvaluationRun,
    EvaluationRunMetadata,
)
from backend.app.evaluation.reporting import (
    build_evaluation_summary,
    render_evaluation_report,
    write_evaluation_reports,
)
from backend.app.evaluation.safety_metrics import SafetyEvaluation


def _result(benchmark_id: str, **updates: object) -> EvaluationCaseResult:
    values: dict[str, object] = {
        "benchmark_id": benchmark_id,
        "category": "aggregation",
        "difficulty": "easy",
        "language": "en",
        "expected_status": "answerable",
        "generated_status": "answerable",
        "comparison_mode": "scalar",
        "generation_success": True,
        "status_matched": True,
        "safety_outcome": "accepted",
        "execution_outcome": "success",
        "comparison_matched": True,
        "comparison_reason": "matched",
        "case_correct": True,
        "generation_latency_ms": 100,
        "execution_latency_ms": 10,
        "end_to_end_latency_ms": 120,
        "input_tokens": 100,
        "output_tokens": 20,
    }
    values.update(updates)
    return EvaluationCaseResult.model_validate(values)


def _run() -> EvaluationRun:
    cases = (
        _result("correct_en"),
        _result(
            "repaired_ro",
            category="multi_table_join",
            difficulty="hard",
            language="ro",
            repair_attempted=True,
            repair_used=True,
            generation_latency_ms=200,
            execution_latency_ms=20,
            end_to_end_latency_ms=240,
            input_tokens=150,
            output_tokens=30,
        ),
        _result(
            "safety_rejected",
            difficulty="medium",
            safety_outcome="rejected",
            safety_reason="mutation_statement",
            execution_outcome="safety_rejected",
            comparison_matched=False,
            comparison_reason="mutation_statement",
            case_correct=False,
            execution_latency_ms=None,
            end_to_end_latency_ms=80,
            failure_reason="mutation_statement",
        ),
        _result(
            "unanswerable_ro",
            category="unanswerable",
            language="ro",
            expected_status="unanswerable",
            generated_status="unanswerable",
            comparison_mode=None,
            safety_outcome="not_applicable",
            execution_outcome="not_applicable",
            comparison_matched=None,
            comparison_reason=None,
            execution_latency_ms=None,
        ),
        _result(
            "ambiguous_wrong",
            category="ambiguous",
            difficulty="medium",
            expected_status="ambiguous",
            generated_status="unanswerable",
            comparison_mode=None,
            status_matched=False,
            safety_outcome="not_applicable",
            execution_outcome="not_applicable",
            comparison_matched=None,
            comparison_reason=None,
            case_correct=False,
            execution_latency_ms=None,
            failure_reason="status_mismatch",
        ),
        _result(
            "provider_failure",
            generation_success=False,
            generated_status=None,
            status_matched=False,
            safety_outcome="not_applicable",
            execution_outcome="provider_error",
            comparison_matched=False,
            comparison_reason="GroqTimeoutError",
            case_correct=False,
            generation_latency_ms=None,
            execution_latency_ms=None,
            input_tokens=None,
            output_tokens=None,
            failure_reason="GroqTimeoutError",
        ),
    )
    metadata = EvaluationRunMetadata(
        run_id="report-run",
        model="fake-model",
        reasoning_effort="medium",
        started_at="2026-09-01T00:00:00+00:00",
        benchmark_case_count=len(cases),
        benchmark_fingerprint="benchmark",
        git_commit_sha="abc123",
        prompt_context_fingerprint="prompt",
        generation_configuration={},
        configuration_fingerprint="configuration",
    )
    return EvaluationRun(
        metadata=metadata,
        safety_evaluation=SafetyEvaluation(
            adversarial_total=41,
            adversarial_blocked=41,
            legitimate_total=40,
            legitimate_accepted=39,
            legitimate_false_positive_rejections=("case_x",),
        ),
        cases=cases,
    )


class EvaluationReportingTests(TestCase):
    def test_core_metric_denominators_status_and_end_to_end_accuracy(self) -> None:
        summary = build_evaluation_summary(_run())

        self.assertEqual(
            (summary.generation_success.numerator, summary.generation_success.denominator),
            (5, 6),
        )
        self.assertEqual(
            (
                summary.semantic_status_accuracy.numerator,
                summary.semantic_status_accuracy.denominator,
            ),
            (4, 6),
        )
        self.assertEqual(
            (summary.execution_success.numerator, summary.execution_success.denominator),
            (2, 2),
        )
        self.assertEqual(
            (summary.result_accuracy.numerator, summary.result_accuracy.denominator),
            (2, 2),
        )
        self.assertEqual(
            (summary.end_to_end_accuracy.numerator, summary.end_to_end_accuracy.denominator),
            (3, 6),
        )
        self.assertEqual(summary.status_accuracy_by_expected["answerable"].denominator, 4)

    def test_repair_latency_tokens_breakdowns_and_safety_metrics(self) -> None:
        summary = build_evaluation_summary(_run())

        self.assertEqual(
            (summary.repair.repair_rate.numerator, summary.repair.repair_rate.denominator),
            (1, 3),
        )
        self.assertEqual(summary.repair.repair_success_rate.rate_pct, 100)
        self.assertEqual(summary.latency["generation"].sample_count, 5)
        self.assertEqual(summary.latency["generation"].median_ms, 100)
        self.assertEqual(summary.latency["generation"].p95_ms, 200)
        self.assertEqual(summary.tokens.input_sample_count, 5)
        self.assertEqual(summary.tokens.total_tokens, 660)
        self.assertEqual(summary.breakdowns["language"]["ro"].denominator, 2)
        self.assertEqual(summary.breakdowns["difficulty"]["hard"].rate_pct, 100)
        self.assertEqual(summary.generated_safety_rejections, 1)
        self.assertEqual(summary.safety.legitimate_false_positive_rate_pct, 2.5)

    def test_optional_metadata_and_partial_empty_run_are_supported(self) -> None:
        run = _run().model_copy(update={"cases": ()})
        summary = build_evaluation_summary(run)

        self.assertFalse(summary.run_complete)
        self.assertIsNone(summary.generation_success.rate_pct)
        self.assertIsNone(summary.latency["generation"].average_ms)
        self.assertIsNone(summary.tokens.total_tokens)
        self.assertIn("partial", render_evaluation_report(summary))

    def test_report_rendering_and_files_are_deterministic(self) -> None:
        run = _run()
        summary = build_evaluation_summary(run)
        first = render_evaluation_report(summary)
        second = render_evaluation_report(build_evaluation_summary(run))

        self.assertEqual(first, second)
        self.assertIn("End-to-end case accuracy | 50.00% | 6", first)
        self.assertIn("Legitimate-query false-positive rejection rate", first)
        self.assertIn("## End-to-end accuracy by category", first)
        self.assertIn("`status_mismatch`: 1", first)
        with TemporaryDirectory() as directory:
            json_path, markdown_path = write_evaluation_reports(
                run, Path(directory)
            )
            first_json = json_path.read_text(encoding="utf-8")
            first_markdown = markdown_path.read_text(encoding="utf-8")
            write_evaluation_reports(run, Path(directory))
            self.assertEqual(first_json, json_path.read_text(encoding="utf-8"))
            self.assertEqual(
                first_markdown, markdown_path.read_text(encoding="utf-8")
            )
