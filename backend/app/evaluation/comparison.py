"""Deterministic semantic comparison for SQL query results."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import math
from typing import Sequence

from pydantic import BaseModel, ConfigDict

from backend.app.ai.benchmark import ComparisonMode


class ResultComparison(BaseModel):
    """Outcome of comparing values while deliberately ignoring column aliases."""

    model_config = ConfigDict(frozen=True)

    matched: bool
    reason: str


def compare_query_results(
    generated_rows: Sequence[Sequence[object]],
    reference_rows: Sequence[Sequence[object]],
    mode: ComparisonMode,
    *,
    generated_column_count: int | None = None,
    reference_column_count: int | None = None,
    numeric_tolerance: Decimal | None = None,
) -> ResultComparison:
    """Compare row values and shape; harmless SQL alias differences are ignored."""
    generated = _normalize_rows(generated_rows)
    reference = _normalize_rows(reference_rows)
    generated_width = _row_width(generated, generated_column_count)
    reference_width = _row_width(reference, reference_column_count)
    if generated_width is None or reference_width is None:
        return ResultComparison(matched=False, reason="inconsistent_row_width")
    if generated_width != reference_width:
        return ResultComparison(matched=False, reason="column_count_mismatch")

    if mode == "scalar":
        if len(generated) != 1 or len(reference) != 1 or generated_width != 1:
            return ResultComparison(matched=False, reason="scalar_shape_mismatch")
        matched = _values_equal(generated[0][0], reference[0][0], numeric_tolerance)
    elif mode == "ordered_rows":
        matched = _ordered_equal(generated, reference, numeric_tolerance)
    elif numeric_tolerance is None:
        matched = Counter(map(_canonical_row, generated)) == Counter(
            map(_canonical_row, reference)
        )
    else:
        matched = _unordered_equal_with_tolerance(
            generated, reference, numeric_tolerance
        )
    return ResultComparison(
        matched=matched,
        reason="matched" if matched else "result_mismatch",
    )


def normalize_value(value: object) -> tuple[str, str | int | bool | None]:
    """Return a stable, type-tagged value without lossy numeric conversion."""
    if value is None:
        return ("null", None)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, int):
        return ("number", str(value))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite decimals cannot be compared")
        return ("number", _decimal_text(value))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite floats cannot be compared")
        return ("number", _decimal_text(Decimal(str(value))))
    if isinstance(value, datetime):
        return ("timestamp", value.isoformat())
    if isinstance(value, date):
        return ("date", value.isoformat())
    if isinstance(value, str):
        return ("string", value)
    raise TypeError(f"unsupported comparison value: {type(value).__name__}")


def _normalize_rows(
    rows: Sequence[Sequence[object]],
) -> tuple[tuple[tuple[str, str | int | bool | None], ...], ...]:
    return tuple(tuple(normalize_value(value) for value in row) for row in rows)


def _row_width(
    rows: Sequence[Sequence[object]], declared_width: int | None
) -> int | None:
    widths = {len(row) for row in rows}
    if declared_width is not None:
        widths.add(declared_width)
    if len(widths) > 1:
        return None
    return next(iter(widths), declared_width or 0)


def _canonical_row(row: Sequence[object]) -> str:
    return json.dumps(row, ensure_ascii=False, separators=(",", ":"))


def _ordered_equal(
    left: Sequence[Sequence[tuple[str, object]]],
    right: Sequence[Sequence[tuple[str, object]]],
    tolerance: Decimal | None,
) -> bool:
    return len(left) == len(right) and all(
        _rows_equal(left_row, right_row, tolerance)
        for left_row, right_row in zip(left, right, strict=True)
    )


def _unordered_equal_with_tolerance(
    left: Sequence[Sequence[tuple[str, object]]],
    right: Sequence[Sequence[tuple[str, object]]],
    tolerance: Decimal,
) -> bool:
    if len(left) != len(right):
        return False
    unmatched = list(right)
    for left_row in left:
        match = next(
            (
                index
                for index, right_row in enumerate(unmatched)
                if _rows_equal(left_row, right_row, tolerance)
            ),
            None,
        )
        if match is None:
            return False
        unmatched.pop(match)
    return True


def _rows_equal(
    left: Sequence[tuple[str, object]],
    right: Sequence[tuple[str, object]],
    tolerance: Decimal | None,
) -> bool:
    return len(left) == len(right) and all(
        _values_equal(left_value, right_value, tolerance)
        for left_value, right_value in zip(left, right, strict=True)
    )


def _values_equal(
    left: tuple[str, object],
    right: tuple[str, object],
    tolerance: Decimal | None,
) -> bool:
    if left == right:
        return True
    if tolerance is None or left[0] != "number" or right[0] != "number":
        return False
    try:
        return abs(Decimal(str(left[1])) - Decimal(str(right[1]))) <= tolerance
    except InvalidOperation:
        return False


def _decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == 0:
        return "0"
    return format(normalized, "f")
