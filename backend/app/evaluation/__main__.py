"""Explicit opt-in live banking benchmark command."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence, cast

from backend.app.ai.benchmark import BenchmarkCase, load_banking_benchmark
from backend.app.ai.context import BankingAIContextBuilder
from backend.app.ai.groq_client import GroqStructuredGenerationClient, ReasoningEffort
from backend.app.ai.prompt import NLToSQLPromptBuilder
from backend.app.ai.service import NLToSQLService
from backend.app.core.config import Settings
from backend.app.db.engine import create_runtime_engine
from backend.app.db.query_executor import ReadOnlyQueryExecutor
from backend.app.evaluation.persistence import EvaluationRunStore
from backend.app.evaluation.runner import (
    EvaluationRunner,
    build_run_metadata,
    fingerprint_prompt,
)
from backend.app.safety.access_policy import BankingSQLAccessPolicy
from backend.app.safety.sql_validator import SQLASTValidator


DEFAULT_RESULTS_DIR = Path("evaluation/results")
DEFAULT_EVALUATION_MAX_ROWS = 100_000
_TRANSIENT_PROVIDER_FAILURES = frozenset(
    {"GroqRateLimitError", "GroqTimeoutError", "GroqUnavailableError"}
)


class _StaticContextBuilder:
    def __init__(self, context: str) -> None:
        self._context = context

    def build(self) -> str:
        return self._context


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    settings = Settings(
        groq_model=arguments.model,
        groq_reasoning_effort=arguments.reasoning_effort,
    )
    cases = _select_cases(
        load_banking_benchmark(), arguments.case_id, arguments.category
    )
    if not cases:
        parser.error("the selected benchmark subset is empty")

    engine = create_runtime_engine(settings)
    try:
        context = BankingAIContextBuilder(engine).build()
        prompt_fingerprint = fingerprint_prompt(
            NLToSQLPromptBuilder(context).build("<evaluation-question>")
        )
        generation_configuration = {
            "case_ids": [case.id for case in cases],
            "statement_timeout_ms": settings.query_statement_timeout_ms,
            "max_rows": arguments.max_rows,
        }
        metadata = build_run_metadata(
            cases,
            model=arguments.model,
            reasoning_effort=cast(ReasoningEffort, arguments.reasoning_effort),
            prompt_context_fingerprint=prompt_fingerprint,
            generation_configuration=generation_configuration,
        )
        path = (
            arguments.resume
            or arguments.output
            or DEFAULT_RESULTS_DIR / f"{metadata.run_id}.json"
        )
        store = EvaluationRunStore(path)
        if arguments.resume:
            run = store.resume(metadata)
        else:
            run = store.create(metadata)

        executor = ReadOnlyQueryExecutor(
            engine,
            statement_timeout_ms=settings.query_statement_timeout_ms,
            max_rows=arguments.max_rows,
        )
        runner = EvaluationRunner(
            NLToSQLService(
                _StaticContextBuilder(context),
                GroqStructuredGenerationClient(settings),
            ),
            SQLASTValidator(),
            BankingSQLAccessPolicy.from_engine(engine),
            executor,
            executor,
        )
        completed = {result.benchmark_id for result in run.cases}
        for case in cases:
            if case.id in completed:
                continue
            result = runner.run_case(case)
            if result.failure_reason in _TRANSIENT_PROVIDER_FAILURES:
                print(
                    f"{case.id}: interrupted by {result.failure_reason}; "
                    f"resume the unchanged artifact {path}",
                    file=sys.stderr,
                )
                return 2
            run = store.append(result)
            print(
                f"{case.id}: {'correct' if result.case_correct else 'incorrect'} "
                f"({len(run.cases)}/{len(cases)})"
            )
        print(f"Evaluation artifact: {path}")
        return 0
    finally:
        engine.dispose()


def _select_cases(
    cases: Sequence[BenchmarkCase],
    case_ids: Sequence[str],
    category: str | None,
) -> tuple[BenchmarkCase, ...]:
    requested = set(case_ids)
    known = {case.id for case in cases}
    unknown = requested - known
    if unknown:
        raise SystemExit(f"Unknown benchmark case IDs: {', '.join(sorted(unknown))}")
    return tuple(
        case
        for case in cases
        if (not requested or case.id in requested)
        and (category is None or case.category == category)
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the live NL-to-SQL benchmark through the safe pipeline"
    )
    parser.add_argument("--model", default="openai/gpt-oss-120b")
    parser.add_argument(
        "--reasoning-effort", choices=("low", "medium", "high"), default="medium"
    )
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--category")
    parser.add_argument("--max-rows", type=int, default=DEFAULT_EVALUATION_MAX_ROWS)
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument("--output", type=Path)
    destination.add_argument("--resume", type=Path)
    return parser


if __name__ == "__main__":
    sys.exit(main())
