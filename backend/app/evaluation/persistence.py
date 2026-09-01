"""Atomic JSON persistence and compatibility-safe evaluation resume."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from pydantic import ValidationError

from backend.app.evaluation.models import (
    EvaluationCaseResult,
    EvaluationRun,
    EvaluationRunMetadata,
)
from backend.app.evaluation.safety_metrics import SafetyEvaluation


class EvaluationPersistenceError(RuntimeError):
    """Raised for invalid, duplicate or incompatible evaluation artifacts."""


class EvaluationRunStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def create(
        self,
        metadata: EvaluationRunMetadata,
        safety_evaluation: SafetyEvaluation | None = None,
    ) -> EvaluationRun:
        if self.path.exists():
            raise EvaluationPersistenceError("evaluation result file already exists")
        run = EvaluationRun(
            metadata=metadata,
            safety_evaluation=safety_evaluation,
        )
        self._write(run)
        return run

    def load(self) -> EvaluationRun:
        try:
            return EvaluationRun.model_validate_json(
                self.path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            raise EvaluationPersistenceError(
                "evaluation result file is missing or invalid"
            ) from exc

    def resume(self, expected: EvaluationRunMetadata) -> EvaluationRun:
        run = self.load()
        if run.metadata.configuration_fingerprint != expected.configuration_fingerprint:
            raise EvaluationPersistenceError(
                "evaluation run configuration is incompatible with resume"
            )
        return run

    def append(self, result: EvaluationCaseResult) -> EvaluationRun:
        run = self.load()
        completed_ids = {item.benchmark_id for item in run.cases}
        if result.benchmark_id in completed_ids:
            raise EvaluationPersistenceError(
                f"benchmark case already completed: {result.benchmark_id}"
            )
        updated = run.model_copy(update={"cases": (*run.cases, result)})
        self._write(updated)
        return updated

    def _write(self, run: EvaluationRun) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            run.model_dump(mode="json", exclude_computed_fields=True),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
            os.replace(temporary_name, self.path)
        except BaseException:
            Path(temporary_name).unlink(missing_ok=True)
            raise
