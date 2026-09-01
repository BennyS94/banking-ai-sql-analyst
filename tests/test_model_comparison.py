from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from backend.app.evaluation.model_comparison import (
    CANDIDATE_MODELS,
    ModelComparisonError,
    compare_model_runs,
    render_model_comparison,
    write_model_comparison,
    _recommend,
)
from backend.app.evaluation.models import (
    EvaluationCaseResult,
    EvaluationRun,
    EvaluationRunMetadata,
)


def _case(
    benchmark_id: str,
    *,
    language: str,
    difficulty: str,
    category: str,
    correct: bool,
    latency: float,
) -> EvaluationCaseResult:
    return EvaluationCaseResult.model_validate(
        {
            "benchmark_id": benchmark_id,
            "category": category,
            "difficulty": difficulty,
            "language": language,
            "expected_status": "answerable",
            "generated_status": "answerable",
            "comparison_mode": "scalar",
            "generation_success": True,
            "status_matched": True,
            "safety_outcome": "accepted",
            "execution_outcome": "success",
            "comparison_matched": correct,
            "comparison_reason": "matched" if correct else "result_mismatch",
            "case_correct": correct,
            "generation_latency_ms": latency,
            "execution_latency_ms": 10,
            "end_to_end_latency_ms": latency + 10,
            "input_tokens": 100,
            "output_tokens": 20,
            "failure_reason": None if correct else "result_mismatch",
        }
    )


def _run(model: str, *, weaker: bool = False) -> EvaluationRun:
    cases = (
        _case(
            "easy_en",
            language="en",
            difficulty="easy",
            category="aggregation",
            correct=True,
            latency=50 if weaker else 100,
        ),
        _case(
            "medium_en",
            language="en",
            difficulty="medium",
            category="temporal",
            correct=True,
            latency=60 if weaker else 110,
        ),
        _case(
            "hard_ro",
            language="ro",
            difficulty="hard",
            category="multi_table_join",
            correct=not weaker,
            latency=70 if weaker else 120,
        ),
        _case(
            "hard_en",
            language="en",
            difficulty="hard",
            category="window_function",
            correct=True,
            latency=80 if weaker else 130,
        ),
    )
    return EvaluationRun(
        metadata=EvaluationRunMetadata(
            run_id=model.rsplit("-", 1)[-1],
            model=model,
            reasoning_effort="medium",
            started_at="2026-09-01T00:00:00+00:00",
            benchmark_case_count=4,
            benchmark_fingerprint="same-benchmark",
            git_commit_sha="abc",
            prompt_context_fingerprint="same-prompt",
            generation_configuration={"case_ids": [case.benchmark_id for case in cases]},
            configuration_fingerprint=f"configuration-{model}",
        ),
        cases=cases,
    )


class ModelComparisonTests(TestCase):
    def setUp(self) -> None:
        self.run_20b = _run(CANDIDATE_MODELS[0], weaker=True)
        self.run_120b = _run(CANDIDATE_MODELS[1])

    def test_metrics_labels_language_breakdown_and_recommendation_inputs(self) -> None:
        comparison = compare_model_runs((self.run_120b, self.run_20b))

        self.assertEqual(set(comparison.models), set(CANDIDATE_MODELS))
        self.assertEqual(
            comparison.models[CANDIDATE_MODELS[0]].end_to_end_accuracy.rate_pct,
            75,
        )
        self.assertEqual(
            comparison.language_accuracy["ro"][CANDIDATE_MODELS[0]].rate_pct,
            0,
        )
        self.assertEqual(
            comparison.language_accuracy["ro"][CANDIDATE_MODELS[1]].rate_pct,
            100,
        )
        self.assertEqual(comparison.recommended_model, CANDIDATE_MODELS[1])
        self.assertIn(
            "romanian_accuracy_pct",
            comparison.recommendation_inputs[CANDIDATE_MODELS[1]],
        )

    def test_incomplete_or_uncontrolled_runs_are_rejected(self) -> None:
        incomplete = self.run_20b.model_copy(
            update={"cases": self.run_20b.cases[:-1]}
        )
        with self.assertRaises(ModelComparisonError):
            compare_model_runs((incomplete, self.run_120b))

        mismatched = self.run_20b.model_copy(
            update={
                "metadata": self.run_20b.metadata.model_copy(
                    update={"prompt_context_fingerprint": "different"}
                )
            }
        )
        with self.assertRaises(ModelComparisonError):
            compare_model_runs((mismatched, self.run_120b))

    def test_repeated_subset_stability_requires_paired_model_coverage(self) -> None:
        comparison = compare_model_runs(
            (self.run_20b, self.run_120b),
            (self.run_20b, self.run_120b),
        )
        self.assertEqual(comparison.stability[CANDIDATE_MODELS[0]].repeat_count, 1)
        with self.assertRaises(ModelComparisonError):
            compare_model_runs(
                (self.run_20b, self.run_120b), (self.run_20b,)
            )

    def test_near_tied_accuracy_prefers_materially_lower_latency(self) -> None:
        common = {
            "result_accuracy_pct": 71.05,
            "semantic_status_accuracy_pct": 100.0,
            "romanian_accuracy_pct": 64.71,
            "hard_accuracy_pct": 50.0,
            "generation_success_pct": 100.0,
            "repair_rate_pct": 0.0,
            "p95_generation_latency_ms": 20_000.0,
            "safety_rejections": 1,
        }
        inputs = {
            CANDIDATE_MODELS[0]: {
                **common,
                "end_to_end_accuracy_pct": 73.08,
                "median_generation_latency_ms": 948.0,
            },
            CANDIDATE_MODELS[1]: {
                **common,
                "end_to_end_accuracy_pct": 75.0,
                "median_generation_latency_ms": 7_489.0,
            },
        }

        self.assertEqual(_recommend(inputs), CANDIDATE_MODELS[0])

    def test_comparison_table_and_files_are_deterministic(self) -> None:
        comparison = compare_model_runs((self.run_20b, self.run_120b))
        report = render_model_comparison(comparison)

        self.assertIn("| End-to-end accuracy | 75.00% | 100.00% |", report)
        self.assertIn("## Language end-to-end accuracy", report)
        self.assertIn("The project default is unchanged", report)
        with TemporaryDirectory() as directory:
            json_path, markdown_path = write_model_comparison(
                comparison, Path(directory)
            )
            first_json = json_path.read_text(encoding="utf-8")
            first_markdown = markdown_path.read_text(encoding="utf-8")
            write_model_comparison(comparison, Path(directory))
            self.assertEqual(first_json, json_path.read_text(encoding="utf-8"))
            self.assertEqual(
                first_markdown, markdown_path.read_text(encoding="utf-8")
            )
