"""Deterministic cleaning pipeline for the synthetic banking dataset."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd

from banking_data.audit import EXPECTED_SOURCE_FILES, RELATIONSHIP_CANDIDATES, sha256_file


class CleaningError(RuntimeError):
    """Raised when source data needs a policy that has not been approved."""


@dataclass(frozen=True)
class DateField:
    column: str
    output_kind: str
    invalid_policy: str


@dataclass(frozen=True)
class DecimalField:
    column: str
    scale: int


@dataclass(frozen=True)
class TableSpec:
    file_name: str
    columns: tuple[str, ...]
    primary_key: str
    foreign_keys: tuple[tuple[str, str], ...] = ()
    date_fields: tuple[DateField, ...] = ()
    decimal_fields: tuple[DecimalField, ...] = ()


COUNTRY_NORMALIZATION_MAP = {
    "Pnited States": "United States",
    "Unitd States": "United States",
    "United Slates": "United States",
    "United StXtes": "United States",
    "United Staes": "United States",
    "United State": "United States",
    "United StateR": "United States",
    "United vtates": "United States",
    "United0States": "United States",
    "UnitedcStates": "United States",
}

TABLE_SPECS = (
    TableSpec("account_statuses.csv", ("AccountStatusID", "StatusName"), "AccountStatusID"),
    TableSpec("account_types.csv", ("AccountTypeID", "TypeName"), "AccountTypeID"),
    TableSpec("customer_types.csv", ("CustomerTypeID", "TypeName"), "CustomerTypeID"),
    TableSpec("loan_statuses.csv", ("LoanStatusID", "StatusName"), "LoanStatusID"),
    TableSpec(
        "transaction_types.csv",
        ("TransactionTypeID", "TypeName"),
        "TransactionTypeID",
    ),
    TableSpec(
        "addresses.csv",
        ("AddressID", "Street", "City", "Country"),
        "AddressID",
    ),
    TableSpec(
        "branches.csv",
        ("BranchID", "BranchName", "AddressID"),
        "BranchID",
        (("AddressID", "addresses.csv"),),
    ),
    TableSpec(
        "customers.csv",
        ("CustomerID", "FirstName", "LastName", "DateOfBirth", "AddressID", "CustomerTypeID"),
        "CustomerID",
        (("AddressID", "addresses.csv"), ("CustomerTypeID", "customer_types.csv")),
        (DateField("DateOfBirth", "date", "null"),),
    ),
    TableSpec(
        "accounts.csv",
        ("AccountID", "CustomerID", "AccountTypeID", "AccountStatusID", "Balance", "OpeningDate"),
        "AccountID",
        (
            ("CustomerID", "customers.csv"),
            ("AccountTypeID", "account_types.csv"),
            ("AccountStatusID", "account_statuses.csv"),
        ),
        (DateField("OpeningDate", "datetime", "error"),),
        (DecimalField("Balance", 2),),
    ),
    TableSpec(
        "loans.csv",
        (
            "LoanID",
            "AccountID",
            "LoanStatusID",
            "PrincipalAmount",
            "InterestRate",
            "StartDate",
            "EstimatedEndDate",
        ),
        "LoanID",
        (("AccountID", "accounts.csv"), ("LoanStatusID", "loan_statuses.csv")),
        (
            DateField("StartDate", "datetime", "error"),
            DateField("EstimatedEndDate", "datetime", "error"),
        ),
        (DecimalField("PrincipalAmount", 2), DecimalField("InterestRate", 4)),
    ),
    TableSpec(
        "transactions.csv",
        (
            "TransactionID",
            "AccountOriginID",
            "AccountDestinationID",
            "TransactionTypeID",
            "Amount",
            "TransactionDate",
            "BranchID",
            "Description",
        ),
        "TransactionID",
        (
            ("AccountOriginID", "accounts.csv"),
            ("AccountDestinationID", "accounts.csv"),
            ("TransactionTypeID", "transaction_types.csv"),
            ("BranchID", "branches.csv"),
        ),
        (DateField("TransactionDate", "datetime", "reject"),),
        (DecimalField("Amount", 2),),
    ),
)

INTEGER_PATTERN = re.compile(r"^[0-9]+$")
DATE_FORMATS = (
    (re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6}$"), "%Y-%m-%d %H:%M:%S.%f"),
    (re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?$"), None),
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "%Y-%m-%d"),
    (re.compile(r"^\d{4}/\d{2}/\d{2}$"), "%Y/%m/%d"),
    (re.compile(r"^\d{2}/\d{2}/\d{4}$"), "%m/%d/%Y"),
    (re.compile(r"^\d{2}\.\d{2}\.\d{4}$"), "%m.%d.%Y"),
)


def _snake_case(name: str) -> str:
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()


def _parse_identifier(value: Any) -> int | None:
    if pd.isna(value):
        return None
    text = str(value)
    if not INTEGER_PATTERN.fullmatch(text):
        return None
    parsed = int(text)
    return parsed if parsed > 0 else None


def _parse_date(value: Any) -> datetime | None:
    if pd.isna(value):
        return None
    text = str(value)
    for pattern, format_string in DATE_FORMATS:
        if not pattern.fullmatch(text):
            continue
        try:
            if format_string is None:
                return datetime.fromisoformat(text)
            return datetime.strptime(text, format_string)
        except ValueError:
            return None
    return None


def _canonical_date(value: datetime, output_kind: str) -> str:
    if output_kind == "date":
        return value.date().isoformat()
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f")


def _canonical_decimal(value: Any, scale: int, file_name: str, column: str) -> str:
    if pd.isna(value):
        raise CleaningError(f"{file_name}.{column} contains a missing required numerical value")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as error:
        raise CleaningError(
            f"{file_name}.{column} contains an unparseable numerical value: {value!r}"
        ) from error
    if not parsed.is_finite():
        raise CleaningError(f"{file_name}.{column} contains a non-finite value: {value!r}")
    quantum = Decimal(1).scaleb(-scale)
    quantized = parsed.quantize(quantum)
    if parsed != quantized:
        raise CleaningError(
            f"{file_name}.{column} exceeds the audited {scale}-decimal precision: {value!r}"
        )
    return format(quantized, f".{scale}f")


def _read_source(path: Path, spec: TableSpec) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path, dtype="string", keep_default_na=False)
    except Exception as error:
        raise CleaningError(f"Could not read {path.name}: {type(error).__name__}: {error}") from error
    actual_columns = tuple(frame.columns)
    if actual_columns != spec.columns:
        raise CleaningError(
            f"{path.name} columns differ from the approved contract: "
            f"expected {spec.columns}, found {actual_columns}"
        )
    return frame.mask(frame.eq(""), pd.NA)


def _add_reason(reasons: dict[int, set[str]], row_index: int, reason: str) -> None:
    reasons.setdefault(row_index, set()).add(reason)


def _add_normalization(
    normalizations: dict[int, Counter[str]], row_index: int, reason: str
) -> None:
    normalizations.setdefault(row_index, Counter())[reason] += 1


def _validate_identifiers(
    frame: pd.DataFrame,
    original: pd.DataFrame,
    spec: TableSpec,
    accepted_keys: dict[str, set[int]],
    reasons: dict[int, set[str]],
    normalizations: dict[int, Counter[str]],
) -> None:
    primary_values: list[Any] = []
    for row_index, value in original[spec.primary_key].items():
        parsed = _parse_identifier(value)
        if pd.isna(value):
            _add_reason(reasons, row_index, "missing_primary_key")
            primary_values.append(pd.NA)
        elif parsed is None:
            _add_reason(reasons, row_index, "invalid_primary_key")
            primary_values.append(pd.NA)
        else:
            primary_values.append(parsed)
            if str(parsed) != str(value):
                _add_normalization(
                    normalizations,
                    row_index,
                    f"normalized_identifier_format__{_snake_case(spec.primary_key)}",
                )
    frame[spec.primary_key] = primary_values

    valid_primary = frame.loc[frame[spec.primary_key].notna(), spec.primary_key]
    duplicate_primary = valid_primary[valid_primary.duplicated(keep=False)]
    if not duplicate_primary.empty:
        samples = sorted({int(value) for value in duplicate_primary})[:10]
        raise CleaningError(
            f"{spec.file_name}.{spec.primary_key} has conflicting duplicate identifiers after "
            f"exact-row deduplication: {samples}"
        )

    for column, target_file in spec.foreign_keys:
        target_values = accepted_keys[target_file]
        parsed_values: list[Any] = []
        reason_suffix = _snake_case(column)
        for row_index, value in original[column].items():
            parsed = _parse_identifier(value)
            if pd.isna(value):
                _add_reason(reasons, row_index, f"missing_foreign_key__{reason_suffix}")
                parsed_values.append(pd.NA)
            elif parsed is None or parsed not in target_values:
                _add_reason(reasons, row_index, f"invalid_foreign_key__{reason_suffix}")
                parsed_values.append(pd.NA if parsed is None else parsed)
            else:
                parsed_values.append(parsed)
                if str(parsed) != str(value):
                    _add_normalization(
                        normalizations,
                        row_index,
                        f"normalized_identifier_format__{reason_suffix}",
                    )
        frame[column] = parsed_values


def _normalize_dates(
    frame: pd.DataFrame,
    original: pd.DataFrame,
    spec: TableSpec,
    reasons: dict[int, set[str]],
    normalizations: dict[int, Counter[str]],
    parse_failures: Counter[str],
) -> None:
    for field in spec.date_fields:
        normalized_values: list[Any] = []
        reason_suffix = _snake_case(field.column)
        for row_index, value in original[field.column].items():
            if pd.isna(value):
                if field.invalid_policy == "reject":
                    _add_reason(reasons, row_index, f"missing_{reason_suffix}")
                normalized_values.append(pd.NA)
                continue
            parsed = _parse_date(value)
            if parsed is None:
                parse_failures[field.column] += 1
                if field.invalid_policy == "null":
                    _add_normalization(
                        normalizations, row_index, f"invalid_{reason_suffix}_to_null"
                    )
                    normalized_values.append(pd.NA)
                elif field.invalid_policy == "reject":
                    _add_reason(reasons, row_index, f"invalid_{reason_suffix}")
                    normalized_values.append(pd.NA)
                else:
                    raise CleaningError(
                        f"{spec.file_name}.{field.column} contains an unparseable value not "
                        f"covered by the approved policy: {value!r}"
                    )
                continue
            canonical = _canonical_date(parsed, field.output_kind)
            normalized_values.append(canonical)
            if canonical != str(value):
                _add_normalization(
                    normalizations, row_index, f"normalized_date_format__{reason_suffix}"
                )
        frame[field.column] = normalized_values


def _normalize_decimals(
    frame: pd.DataFrame,
    original: pd.DataFrame,
    spec: TableSpec,
    reasons: dict[int, set[str]],
    normalizations: dict[int, Counter[str]],
) -> None:
    for field in spec.decimal_fields:
        normalized_values: list[Any] = []
        reason_code = f"normalized_decimal_format__{_snake_case(field.column)}"
        for row_index, value in original[field.column].items():
            if row_index in reasons:
                normalized_values.append(pd.NA)
                continue
            canonical = _canonical_decimal(value, field.scale, spec.file_name, field.column)
            normalized_values.append(canonical)
            if canonical != str(value):
                _add_normalization(normalizations, row_index, reason_code)
        frame[field.column] = normalized_values


def _normalize_countries(
    frame: pd.DataFrame,
    original: pd.DataFrame,
    normalizations: dict[int, Counter[str]],
) -> None:
    if "Country" not in frame:
        return
    values: list[Any] = []
    for row_index, value in original["Country"].items():
        if pd.isna(value):
            values.append(pd.NA)
        elif value in COUNTRY_NORMALIZATION_MAP:
            values.append(COUNTRY_NORMALIZATION_MAP[str(value)])
            _add_normalization(
                normalizations, row_index, "normalized_country__united_states"
            )
        else:
            values.append(value)
    frame["Country"] = values


def _logical_types(spec: TableSpec) -> dict[str, str]:
    result = {column: "string" for column in spec.columns}
    result[spec.primary_key] = "positive_integer"
    for column, _ in spec.foreign_keys:
        result[column] = "positive_integer"
    for field in spec.date_fields:
        result[field.column] = field.output_kind
    for field in spec.decimal_fields:
        result[field.column] = f"decimal(scale={field.scale})"
    return result


def _categorical_output_values(frame: pd.DataFrame, spec: TableSpec) -> dict[str, Any]:
    identifier_columns = {spec.primary_key, *(column for column, _ in spec.foreign_keys)}
    result: dict[str, Any] = {}
    for column in frame.columns:
        if column in identifier_columns or frame[column].nunique(dropna=True) > 20:
            continue
        frequencies = []
        for value, count in frame[column].value_counts(dropna=False, sort=False).items():
            frequencies.append(
                {"value": None if pd.isna(value) else str(value), "count": int(count)}
            )
        frequencies.sort(key=lambda item: str(item["value"]))
        result[column] = frequencies
    return result


def _clean_table(
    raw_dir: Path,
    stage_dir: Path,
    spec: TableSpec,
    accepted_keys: dict[str, set[int]],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    source_path = raw_dir / spec.file_name
    raw = _read_source(source_path, spec)
    source_row_numbers = pd.Series(raw.index + 2, index=raw.index)
    duplicate_mask = raw.duplicated(keep="first")
    original = raw.loc[~duplicate_mask].copy()
    source_row_numbers = source_row_numbers.loc[original.index]
    cleaned = original.copy()
    reasons: dict[int, set[str]] = {}
    normalizations: dict[int, Counter[str]] = {}
    parse_failures: Counter[str] = Counter()

    _validate_identifiers(cleaned, original, spec, accepted_keys, reasons, normalizations)
    _normalize_dates(cleaned, original, spec, reasons, normalizations, parse_failures)
    _normalize_decimals(cleaned, original, spec, reasons, normalizations)
    _normalize_countries(cleaned, original, normalizations)

    rejected_indices = sorted(reasons)
    accepted = cleaned.drop(index=rejected_indices).reset_index(drop=True)
    rejected = original.loc[rejected_indices].copy()
    if not rejected.empty:
        rejected.insert(
            0,
            "source_row_number",
            [int(source_row_numbers.loc[index]) for index in rejected_indices],
        )
        rejected["rejection_reasons"] = [
            ";".join(sorted(reasons[index])) for index in rejected_indices
        ]

    output_path = stage_dir / spec.file_name
    accepted.to_csv(output_path, index=False, lineterminator="\n", na_rep="")
    rejected_path: Path | None = None
    if not rejected.empty:
        rejected_dir = stage_dir / "rejected"
        rejected_dir.mkdir(exist_ok=True)
        rejected_path = rejected_dir / f"{Path(spec.file_name).stem}_rejected.csv"
        rejected.to_csv(rejected_path, index=False, lineterminator="\n", na_rep="")

    reason_counts: Counter[str] = Counter()
    for row_reasons in reasons.values():
        reason_counts.update(row_reasons)
    normalization_counts: Counter[str] = Counter()
    for row_index, row_normalizations in normalizations.items():
        if row_index not in reasons:
            normalization_counts.update(row_normalizations)
    expected_total = int(duplicate_mask.sum()) + len(accepted) + len(rejected)
    if expected_total != len(raw):
        raise AssertionError(f"Row reconciliation failed for {spec.file_name}")

    accepted_keys[spec.file_name] = {int(value) for value in accepted[spec.primary_key]}
    report = {
        "source_file": spec.file_name,
        "source_sha256": sha256_file(source_path),
        "processed_file": spec.file_name,
        "processed_columns": list(spec.columns),
        "logical_types": _logical_types(spec),
        "processed_sha256": sha256_file(output_path),
        "rejected_file": (
            f"rejected/{rejected_path.name}" if rejected_path is not None else None
        ),
        "rejected_sha256": sha256_file(rejected_path) if rejected_path is not None else None,
        "input_rows": len(raw),
        "duplicate_copies_removed": int(duplicate_mask.sum()),
        "rows_after_deduplication": len(original),
        "accepted_rows": len(accepted),
        "rejected_rows": len(rejected),
        "rejection_reason_counts": dict(sorted(reason_counts.items())),
        "normalization_reason_counts": dict(sorted(normalization_counts.items())),
        "parse_failure_counts": dict(sorted(parse_failures.items())),
        "nullable_output_counts": {
            column: int(accepted[column].isna().sum())
            for column in accepted.columns
            if accepted[column].isna().any()
        },
        "categorical_output_values": _categorical_output_values(accepted, spec),
    }
    return accepted, report


def _relationship_summary(cleaned_frames: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for source_file, source_column, target_file, target_column in RELATIONSHIP_CANDIDATES:
        source = cleaned_frames[source_file][source_column]
        target = cleaned_frames[target_file][target_column]
        counts = source.value_counts()
        target_values = set(target)
        referenced_values = set(source)
        summaries.append(
            {
                "source": f"{source_file}.{source_column}",
                "target": f"{target_file}.{target_column}",
                "source_rows": len(source),
                "source_distinct_foreign_keys": int(source.nunique()),
                "target_rows": len(target),
                "referenced_target_keys": len(referenced_values),
                "unreferenced_target_keys": len(target_values - referenced_values),
                "minimum_rows_per_referenced_target": int(counts.min()) if not counts.empty else 0,
                "maximum_rows_per_referenced_target": int(counts.max()) if not counts.empty else 0,
                "null_references": int(source.isna().sum()),
                "orphan_references": int((~source.isin(target_values)).sum()),
            }
        )
    return summaries


def _validate_source_set(raw_dir: Path) -> None:
    discovered = {path.name for path in raw_dir.glob("*.csv")}
    expected = set(EXPECTED_SOURCE_FILES)
    if discovered != expected:
        raise CleaningError(
            f"Raw CSV set differs from the approved contract; missing={sorted(expected - discovered)}, "
            f"unexpected={sorted(discovered - expected)}"
        )


def _validate_output_target(raw_dir: Path, output_dir: Path) -> None:
    raw_resolved = raw_dir.resolve()
    output_resolved = output_dir.resolve()
    if (
        output_resolved == raw_resolved
        or raw_resolved in output_resolved.parents
        or output_resolved in raw_resolved.parents
    ):
        raise CleaningError(
            "Processed output must not be the raw directory, its child, or its parent"
        )
    if output_dir.exists() and any(output_dir.iterdir()):
        marker = output_dir / "cleaning_summary.json"
        if not marker.is_file():
            raise CleaningError(
                f"Refusing to replace non-empty unrecognized output directory: {output_dir}"
            )


def clean_dataset(raw_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Rebuild processed data transactionally at the filesystem-directory level."""
    raw_dir = Path(raw_dir)
    output_dir = Path(output_dir)
    if not raw_dir.is_dir():
        raise CleaningError(f"Raw directory does not exist: {raw_dir}")
    _validate_source_set(raw_dir)
    _validate_output_target(raw_dir, output_dir)
    before_hashes = {
        path.name: sha256_file(path) for path in sorted(raw_dir.iterdir()) if path.is_file()
    }

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    try:
        accepted_keys: dict[str, set[int]] = {}
        cleaned_frames: dict[str, pd.DataFrame] = {}
        file_reports: list[dict[str, Any]] = []
        for spec in TABLE_SPECS:
            cleaned, report = _clean_table(
                raw_dir, stage_dir, spec, accepted_keys
            )
            cleaned_frames[spec.file_name] = cleaned
            file_reports.append(report)

        relationships = _relationship_summary(cleaned_frames)
        if any(item["null_references"] or item["orphan_references"] for item in relationships):
            raise AssertionError("Accepted processed data contains invalid relationships")

        totals = {
            "input_rows": sum(report["input_rows"] for report in file_reports),
            "duplicate_copies_removed": sum(
                report["duplicate_copies_removed"] for report in file_reports
            ),
            "accepted_rows": sum(report["accepted_rows"] for report in file_reports),
            "rejected_rows": sum(report["rejected_rows"] for report in file_reports),
        }
        summary = {
            "cleaning_schema_version": 1,
            "source_directory": raw_dir.as_posix(),
            "output_directory": output_dir.as_posix(),
            "files": sorted(file_reports, key=lambda report: report["source_file"]),
            "totals": totals,
            "relationships": relationships,
        }
        (stage_dir / "cleaning_summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        after_hashes = {
            path.name: sha256_file(path) for path in sorted(raw_dir.iterdir()) if path.is_file()
        }
        if before_hashes != after_hashes:
            raise AssertionError("Raw source files changed during cleaning")

        if output_dir.exists():
            shutil.rmtree(output_dir)
        stage_dir.replace(output_dir)
        return summary
    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        summary = clean_dataset(args.raw_dir, args.output_dir)
    except CleaningError as error:
        print(f"Cleaning failed: {error}")
        return 1

    for report in summary["files"]:
        print(
            f"{report['source_file']}: input={report['input_rows']}, "
            f"duplicates={report['duplicate_copies_removed']}, "
            f"accepted={report['accepted_rows']}, rejected={report['rejected_rows']}"
        )
    totals = summary["totals"]
    print(
        f"Totals: input={totals['input_rows']}, "
        f"duplicates={totals['duplicate_copies_removed']}, "
        f"accepted={totals['accepted_rows']}, rejected={totals['rejected_rows']}"
    )
    print(f"Wrote processed data to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
