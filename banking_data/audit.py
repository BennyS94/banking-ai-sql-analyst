"""Reproducible, read-only profiling for the raw banking CSV dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.api.types import is_numeric_dtype


EXPECTED_SOURCE_FILES = (
    "account_statuses.csv",
    "account_types.csv",
    "accounts.csv",
    "addresses.csv",
    "branches.csv",
    "customer_types.csv",
    "customers.csv",
    "loan_statuses.csv",
    "loans.csv",
    "transaction_types.csv",
    "transactions.csv",
)

LIKELY_PRIMARY_KEYS = {
    "account_statuses.csv": "AccountStatusID",
    "account_types.csv": "AccountTypeID",
    "accounts.csv": "AccountID",
    "addresses.csv": "AddressID",
    "branches.csv": "BranchID",
    "customer_types.csv": "CustomerTypeID",
    "customers.csv": "CustomerID",
    "loan_statuses.csv": "LoanStatusID",
    "loans.csv": "LoanID",
    "transaction_types.csv": "TransactionTypeID",
    "transactions.csv": "TransactionID",
}

RELATIONSHIP_CANDIDATES = (
    ("customers.csv", "CustomerTypeID", "customer_types.csv", "CustomerTypeID"),
    ("customers.csv", "AddressID", "addresses.csv", "AddressID"),
    ("accounts.csv", "CustomerID", "customers.csv", "CustomerID"),
    ("accounts.csv", "AccountTypeID", "account_types.csv", "AccountTypeID"),
    ("accounts.csv", "AccountStatusID", "account_statuses.csv", "AccountStatusID"),
    ("branches.csv", "AddressID", "addresses.csv", "AddressID"),
    ("loans.csv", "AccountID", "accounts.csv", "AccountID"),
    ("loans.csv", "LoanStatusID", "loan_statuses.csv", "LoanStatusID"),
    ("transactions.csv", "AccountOriginID", "accounts.csv", "AccountID"),
    ("transactions.csv", "AccountDestinationID", "accounts.csv", "AccountID"),
    ("transactions.csv", "BranchID", "branches.csv", "BranchID"),
    (
        "transactions.csv",
        "TransactionTypeID",
        "transaction_types.csv",
        "TransactionTypeID",
    ),
)

NUMERIC_NAME_MARKERS = ("amount", "balance", "rate")
DATE_NAME_MARKERS = ("date", "birth")


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest without changing the file."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_csv_files(raw_dir: Path) -> list[Path]:
    """Discover CSV inputs in stable filename order."""
    return sorted(raw_dir.glob("*.csv"), key=lambda path: path.name)


def _json_value(value: Any) -> Any:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, (pd.Timestamp, date)):
        return value.isoformat()
    return value


def _sorted_samples(values: Iterable[Any], limit: int = 10) -> list[Any]:
    normalized = [_json_value(value) for value in values]
    return sorted(normalized, key=lambda value: str(value))[:limit]


def _identifier_columns(columns: Iterable[str]) -> list[str]:
    return [column for column in columns if column.lower().endswith("id")]


def _missing_profile(frame: pd.DataFrame) -> dict[str, dict[str, float | int]]:
    row_count = len(frame)
    result: dict[str, dict[str, float | int]] = {}
    for column in frame.columns:
        null_count = int(frame[column].isna().sum())
        result[column] = {
            "null_count": null_count,
            "null_percentage": round((null_count / row_count * 100) if row_count else 0, 4),
        }
    return result


def _duplicate_profile(frame: pd.DataFrame) -> dict[str, Any]:
    involved = int(frame.duplicated(keep=False).sum())
    excess = int(frame.duplicated(keep="first").sum())
    identifiers: dict[str, Any] = {}

    for column in _identifier_columns(frame.columns):
        non_null = frame.loc[frame[column].notna()]
        duplicate_mask = non_null[column].duplicated(keep=False)
        duplicate_values = non_null.loc[duplicate_mask, column].drop_duplicates()
        conflicting: list[Any] = []
        for value in duplicate_values:
            group = non_null.loc[non_null[column] == value]
            if len(group.drop_duplicates()) > 1:
                conflicting.append(value)
        identifiers[column] = {
            "duplicate_distinct_value_count": int(len(duplicate_values)),
            "duplicate_rows_involved": int(duplicate_mask.sum()),
            "conflicting_duplicate_value_count": len(conflicting),
            "sample_duplicate_values": _sorted_samples(duplicate_values),
            "sample_conflicting_values": _sorted_samples(conflicting),
        }

    return {
        "exact_duplicate_rows_involved": involved,
        "excess_exact_duplicate_rows": excess,
        "identifier_columns": identifiers,
    }


def _key_profile(frame: pd.DataFrame, file_name: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    likely_primary_key = LIKELY_PRIMARY_KEYS.get(file_name)
    for column in _identifier_columns(frame.columns):
        null_count = int(frame[column].isna().sum())
        non_null = frame[column].dropna()
        duplicate_value_count = int(non_null[non_null.duplicated()].nunique())
        is_likely_primary_identifier = column == likely_primary_key
        candidates.append(
            {
                "column": column,
                "is_likely_primary_identifier": is_likely_primary_identifier,
                "null_count": null_count,
                "distinct_non_null_count": int(non_null.nunique()),
                "duplicate_distinct_value_count": duplicate_value_count,
                "qualifies_as_primary_key_candidate": (
                    is_likely_primary_identifier
                    and null_count == 0
                    and duplicate_value_count == 0
                    and len(non_null) == len(frame)
                ),
            }
        )
    return candidates


def _categorical_profile(frame: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for column in frame.columns:
        if column.lower().endswith("id"):
            continue
        distinct_count = int(frame[column].nunique(dropna=True))
        if distinct_count > 20:
            continue
        frequencies: list[dict[str, Any]] = []
        counts = frame[column].value_counts(dropna=False, sort=False)
        for value, count in counts.items():
            frequencies.append({"value": _json_value(value), "count": int(count)})
        frequencies.sort(key=lambda item: (str(item["value"]), item["count"]))
        result[column] = {
            "distinct_non_null_count": distinct_count,
            "frequencies": frequencies,
        }
    return result


def _numeric_profile(raw_frame: pd.DataFrame, inferred_frame: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for column in raw_frame.columns:
        lower_name = column.lower()
        inferred_numeric = column in inferred_frame and is_numeric_dtype(inferred_frame[column])
        meaningful_name = any(marker in lower_name for marker in NUMERIC_NAME_MARKERS)
        if lower_name.endswith("id") or not (inferred_numeric or meaningful_name):
            continue

        source = raw_frame[column]
        parsed = pd.to_numeric(source, errors="coerce")
        parse_failure_mask = source.notna() & parsed.isna()
        valid = parsed.dropna()
        if valid.empty:
            lower_bound = upper_bound = None
            outlier_count = 0
        else:
            first_quartile = float(valid.quantile(0.25))
            third_quartile = float(valid.quantile(0.75))
            iqr = third_quartile - first_quartile
            lower_bound = first_quartile - 1.5 * iqr
            upper_bound = third_quartile + 1.5 * iqr
            outlier_count = int(((valid < lower_bound) | (valid > upper_bound)).sum())

        result[column] = {
            "parse_failure_count": int(parse_failure_mask.sum()),
            "sample_parse_failures": _sorted_samples(source.loc[parse_failure_mask].unique()),
            "minimum": _json_value(valid.min()) if not valid.empty else None,
            "maximum": _json_value(valid.max()) if not valid.empty else None,
            "median": _json_value(valid.median()) if not valid.empty else None,
            "negative_count": int((valid < 0).sum()),
            "zero_count": int((valid == 0).sum()),
            "iqr_outlier_count": outlier_count,
            "iqr_lower_bound": lower_bound,
            "iqr_upper_bound": upper_bound,
        }
    return result


DATE_PATTERNS = (
    ("YYYY-MM-DD HH:MM:SS.ffffff", re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6}$")),
    ("YYYY-MM-DD HH:MM:SS", re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")),
    ("YYYY-MM-DD", re.compile(r"^\d{4}-\d{2}-\d{2}$")),
    ("DD/MM/YYYY", re.compile(r"^\d{2}/\d{2}/\d{4}$")),
    ("MM-DD-YYYY", re.compile(r"^\d{2}-\d{2}-\d{4}$")),
)


def _date_format(value: str) -> str:
    for label, pattern in DATE_PATTERNS:
        if pattern.fullmatch(value):
            return label
    return "other"


def _date_profile(frame: pd.DataFrame, reference_date: date) -> dict[str, Any]:
    result: dict[str, Any] = {}
    reference_timestamp = pd.Timestamp(reference_date)
    for column in frame.columns:
        if not any(marker in column.lower() for marker in DATE_NAME_MARKERS):
            continue
        source = frame[column]
        parsed = pd.to_datetime(source, errors="coerce", format="mixed")
        parse_failure_mask = source.notna() & parsed.isna()
        valid = parsed.dropna()
        formats: dict[str, int] = {}
        for value in source.dropna().astype(str):
            label = _date_format(value)
            formats[label] = formats.get(label, 0) + 1
        result[column] = {
            "detected_formats": dict(sorted(formats.items())),
            "null_count": int(source.isna().sum()),
            "parse_failure_count": int(parse_failure_mask.sum()),
            "sample_parse_failures": _sorted_samples(source.loc[parse_failure_mask].unique()),
            "minimum": valid.min().isoformat() if not valid.empty else None,
            "maximum": valid.max().isoformat() if not valid.empty else None,
            "before_1900_count": int((valid < pd.Timestamp("1900-01-01")).sum()),
            "after_reference_date_count": int((valid > reference_timestamp).sum()),
        }
    return result


def _text_profile(frame: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for column in frame.columns:
        source = frame[column].dropna().astype(str)
        if source.empty:
            continue
        whitespace_count = int((source != source.str.strip()).sum())
        empty_string_count = int((source.str.strip() == "").sum())
        normalized = source.str.strip().str.replace(r"\s+", " ", regex=True).str.casefold()
        variants: list[dict[str, Any]] = []
        variant_frame = pd.DataFrame({"normalized": normalized, "original": source})
        for normalized_value, group in variant_frame.groupby("normalized", sort=True):
            originals = sorted(group["original"].unique().tolist())
            if len(originals) > 1:
                variants.append(
                    {
                        "normalized_value": normalized_value,
                        "variants": originals[:10],
                        "row_count": int(len(group)),
                    }
                )
        if whitespace_count or empty_string_count or variants:
            result[column] = {
                "leading_or_trailing_whitespace_count": whitespace_count,
                "empty_string_count": empty_string_count,
                "near_duplicate_group_count": len(variants),
                "sample_near_duplicate_groups": variants[:10],
            }
    return result


def audit_file(path: Path, reference_date: date) -> tuple[dict[str, Any], pd.DataFrame | None]:
    """Audit one CSV and return its report plus its string-preserving frame."""
    base: dict[str, Any] = {
        "file_name": path.name,
        "sha256": sha256_file(path),
        "read_error": None,
    }
    try:
        raw_frame = pd.read_csv(path, dtype="string")
        inferred_frame = pd.read_csv(path)
    except Exception as error:  # pandas exposes parser/encoding errors through several types
        base["read_error"] = {
            "type": type(error).__name__,
            "message": str(error),
        }
        return base, None

    base.update(
        {
            "row_count": int(len(raw_frame)),
            "column_count": int(len(raw_frame.columns)),
            "columns": list(raw_frame.columns),
            "inferred_pandas_types": {
                column: str(inferred_frame[column].dtype) for column in inferred_frame.columns
            },
            "missing": _missing_profile(raw_frame),
            "duplicates": _duplicate_profile(raw_frame),
            "candidate_keys": _key_profile(raw_frame, path.name),
            "categorical_values": _categorical_profile(raw_frame),
            "numerical_fields": _numeric_profile(raw_frame, inferred_frame),
            "date_fields": _date_profile(raw_frame, reference_date),
            "text_quality": _text_profile(raw_frame),
        }
    )
    return base, raw_frame


def _relationship_profile(frames: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    relationships: list[dict[str, Any]] = []
    for source_file, source_column, target_file, target_column in RELATIONSHIP_CANDIDATES:
        if source_file not in frames or target_file not in frames:
            continue
        source_frame = frames[source_file]
        target_frame = frames[target_file]
        if source_column not in source_frame or target_column not in target_frame:
            continue
        source = source_frame[source_column]
        target = target_frame[target_column]
        non_null_source = source.dropna()
        valid_mask = non_null_source.isin(target.dropna())
        orphan_values = non_null_source.loc[~valid_mask]
        relationships.append(
            {
                "source_file": source_file,
                "source_column": source_column,
                "target_file": target_file,
                "target_column": target_column,
                "source_row_count": int(len(source)),
                "valid_reference_count": int(valid_mask.sum()),
                "null_reference_count": int(source.isna().sum()),
                "orphan_reference_count": int((~valid_mask).sum()),
                "orphan_distinct_value_count": int(orphan_values.nunique()),
                "sample_orphan_values": _sorted_samples(orphan_values.unique()),
                "target_null_key_count": int(target.isna().sum()),
                "target_duplicate_key_rows": int(target.dropna().duplicated(keep=False).sum()),
            }
        )
    return relationships


def audit_directory(raw_dir: Path, reference_date: date) -> dict[str, Any]:
    """Audit every CSV in a directory without modifying any source file."""
    paths = discover_csv_files(raw_dir)
    discovered_names = [path.name for path in paths]
    frames: dict[str, pd.DataFrame] = {}
    files: list[dict[str, Any]] = []
    for path in paths:
        report, frame = audit_file(path, reference_date)
        files.append(report)
        if frame is not None:
            frames[path.name] = frame

    return {
        "audit_schema_version": 1,
        "reference_date": reference_date.isoformat(),
        "source_directory": raw_dir.as_posix(),
        "discovery": {
            "expected_csv_files": list(EXPECTED_SOURCE_FILES),
            "discovered_csv_files": discovered_names,
            "missing_expected_csv_files": sorted(set(EXPECTED_SOURCE_FILES) - set(discovered_names)),
            "unexpected_csv_files": sorted(set(discovered_names) - set(EXPECTED_SOURCE_FILES)),
        },
        "files": files,
        "relationship_candidates": _relationship_profile(frames),
    }


def render_markdown(summary: dict[str, Any]) -> str:
    """Render the machine summary as a deterministic human review report."""
    lines = [
        "# Raw Banking Dataset Audit",
        "",
        f"Reference date for future-date reporting: `{summary['reference_date']}`.",
        "This report records source evidence only; it applies no cleaning decisions.",
        "",
        "## Dataset overview",
        "",
        "| File | Rows | Columns | Read error |",
        "|---|---:|---:|---|",
    ]
    for file_report in summary["files"]:
        error = file_report["read_error"]
        lines.append(
            f"| `{file_report['file_name']}` | {file_report.get('row_count', 'n/a')} | "
            f"{file_report.get('column_count', 'n/a')} | "
            f"{error['type'] if error else 'None'} |"
        )

    discovery = summary["discovery"]
    lines.extend(
        [
            "",
            f"Expected CSVs: {len(discovery['expected_csv_files'])}; "
            f"discovered: {len(discovery['discovered_csv_files'])}.",
            f"Missing expected files: {discovery['missing_expected_csv_files'] or 'None'}.",
            f"Unexpected files: {discovery['unexpected_csv_files'] or 'None'}.",
            "",
            "## Candidate relationships",
            "",
            "| Source | Target | Valid | Null | Orphan | Target duplicate-key rows |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for relationship in summary["relationship_candidates"]:
        lines.append(
            f"| `{relationship['source_file']}.{relationship['source_column']}` | "
            f"`{relationship['target_file']}.{relationship['target_column']}` | "
            f"{relationship['valid_reference_count']} | {relationship['null_reference_count']} | "
            f"{relationship['orphan_reference_count']} | "
            f"{relationship['target_duplicate_key_rows']} |"
        )
        if relationship["sample_orphan_values"]:
            lines.append(
                f"  - Sample orphan values: `{relationship['sample_orphan_values']}`"
            )

    for file_report in summary["files"]:
        lines.extend(["", f"## {file_report['file_name']}", ""])
        if file_report["read_error"]:
            error = file_report["read_error"]
            lines.append(f"Read failed with `{error['type']}`: {error['message']}")
            continue
        lines.extend(
            [
                f"SHA-256: `{file_report['sha256']}`",
                "",
                "### Columns and missing values",
                "",
                "| Column | Inferred dtype | Nulls | Null % |",
                "|---|---|---:|---:|",
            ]
        )
        for column in file_report["columns"]:
            missing = file_report["missing"][column]
            lines.append(
                f"| `{column}` | `{file_report['inferred_pandas_types'][column]}` | "
                f"{missing['null_count']} | {missing['null_percentage']:.4f} |"
            )

        duplicates = file_report["duplicates"]
        lines.extend(
            [
                "",
                "### Duplicates and candidate keys",
                "",
                f"Exact duplicate rows involved: {duplicates['exact_duplicate_rows_involved']}; "
                f"excess copies: {duplicates['excess_exact_duplicate_rows']}.",
                "",
                "| Identifier | Likely primary identifier | Nulls | Distinct | Duplicate values | Conflicting values | Clean PK candidate |",
                "|---|---|---:|---:|---:|---:|---|",
            ]
        )
        duplicate_identifiers = duplicates["identifier_columns"]
        for key in file_report["candidate_keys"]:
            duplicate = duplicate_identifiers[key["column"]]
            lines.append(
                f"| `{key['column']}` | {key['is_likely_primary_identifier']} | "
                f"{key['null_count']} | "
                f"{key['distinct_non_null_count']} | {key['duplicate_distinct_value_count']} | "
                f"{duplicate['conflicting_duplicate_value_count']} | "
                f"{key['qualifies_as_primary_key_candidate']} |"
            )
        duplicate_samples = [
            (column, profile["sample_duplicate_values"])
            for column, profile in duplicate_identifiers.items()
            if profile["sample_duplicate_values"]
        ]
        if duplicate_samples:
            lines.extend(["", "Sample duplicated identifier values:", ""])
            for column, samples in duplicate_samples:
                lines.append(f"- `{column}`: `{samples}`")

        if file_report["categorical_values"]:
            lines.extend(["", "### Low-cardinality values", ""])
            for column, profile in file_report["categorical_values"].items():
                frequencies = ", ".join(
                    f"`{item['value']}` ({item['count']})" for item in profile["frequencies"]
                )
                lines.append(f"- `{column}`: {frequencies}")

        if file_report["numerical_fields"]:
            lines.extend(
                [
                    "",
                    "### Numerical fields",
                    "",
                    "| Column | Parse failures | Min | Median | Max | Negative | Zero | IQR outliers |",
                    "|---|---:|---:|---:|---:|---:|---:|---:|",
                ]
            )
            for column, profile in file_report["numerical_fields"].items():
                lines.append(
                    f"| `{column}` | {profile['parse_failure_count']} | {profile['minimum']} | "
                    f"{profile['median']} | {profile['maximum']} | {profile['negative_count']} | "
                    f"{profile['zero_count']} | {profile['iqr_outlier_count']} |"
                )
            numeric_failures = [
                (column, profile["sample_parse_failures"])
                for column, profile in file_report["numerical_fields"].items()
                if profile["sample_parse_failures"]
            ]
            if numeric_failures:
                lines.extend(["", "Sample numerical parse failures:", ""])
                for column, samples in numeric_failures:
                    lines.append(f"- `{column}`: `{samples}`")

        if file_report["date_fields"]:
            lines.extend(
                [
                    "",
                    "### Date fields",
                    "",
                    "| Column | Formats | Parse failures | Nulls | Min | Max | After reference date |",
                    "|---|---|---:|---:|---|---|---:|",
                ]
            )
            for column, profile in file_report["date_fields"].items():
                formats = ", ".join(
                    f"{name}: {count}" for name, count in profile["detected_formats"].items()
                )
                lines.append(
                    f"| `{column}` | {formats} | {profile['parse_failure_count']} | "
                    f"{profile['null_count']} | {profile['minimum']} | {profile['maximum']} | "
                    f"{profile['after_reference_date_count']} |"
                )
            date_failures = [
                (column, profile["sample_parse_failures"])
                for column, profile in file_report["date_fields"].items()
                if profile["sample_parse_failures"]
            ]
            if date_failures:
                lines.extend(["", "Sample date parse failures:", ""])
                for column, samples in date_failures:
                    lines.append(f"- `{column}`: `{samples}`")

        if file_report["text_quality"]:
            lines.extend(["", "### Text-quality signals", ""])
            for column, profile in file_report["text_quality"].items():
                lines.append(
                    f"- `{column}`: leading/trailing whitespace rows "
                    f"{profile['leading_or_trailing_whitespace_count']}; near-duplicate normalized "
                    f"groups {profile['near_duplicate_group_count']}."
                )

    lines.extend(
        [
            "",
            "## Review boundary",
            "",
            "No rows or values were changed by this audit. Duplicate, null, orphan, categorical, "
            "date, and numerical findings require an explicit cleaning policy before processing.",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(summary: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "raw_dataset_audit.json"
    markdown_path = output_dir / "raw_dataset_audit.md"
    json_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    markdown_path.write_text(render_markdown(summary), encoding="utf-8", newline="\n")
    return json_path, markdown_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/audit"))
    parser.add_argument(
        "--reference-date",
        type=date.fromisoformat,
        required=True,
        help="ISO date used only to report later source dates; it is not a cleaning cutoff.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary = audit_directory(args.raw_dir, args.reference_date)
    json_path, markdown_path = write_outputs(summary, args.output_dir)
    print(f"Wrote {json_path}")
    print(f"Wrote {markdown_path}")

    discovery = summary["discovery"]
    has_read_errors = any(file_report["read_error"] for file_report in summary["files"])
    if discovery["missing_expected_csv_files"] or has_read_errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
