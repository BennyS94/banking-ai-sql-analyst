from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import date
from pathlib import Path

from banking_data.audit import (
    EXPECTED_SOURCE_FILES,
    audit_directory,
    discover_csv_files,
    render_markdown,
    write_outputs,
)


REFERENCE_DATE = date(2026, 8, 31)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = REPOSITORY_ROOT / "data" / "raw"


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AuditFixtureTests(unittest.TestCase):
    def test_null_duplicate_and_key_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            raw_dir = Path(temporary_directory)
            (raw_dir / "customers.csv").write_text(
                "CustomerID,FirstName\n1,Ana\n1,Ana\n2,\n2,Anne\n",
                encoding="utf-8",
            )

            summary = audit_directory(raw_dir, REFERENCE_DATE)
            report = summary["files"][0]

            self.assertEqual(report["row_count"], 4)
            self.assertEqual(report["missing"]["FirstName"]["null_count"], 1)
            self.assertEqual(report["duplicates"]["exact_duplicate_rows_involved"], 2)
            self.assertEqual(
                report["duplicates"]["identifier_columns"]["CustomerID"][
                    "conflicting_duplicate_value_count"
                ],
                1,
            )
            self.assertFalse(report["candidate_keys"][0]["qualifies_as_primary_key_candidate"])

    def test_relationship_analysis_reports_null_and_orphan_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            raw_dir = Path(temporary_directory)
            (raw_dir / "customers.csv").write_text(
                "CustomerID,FirstName\n1,Ana\n2,Ion\n",
                encoding="utf-8",
            )
            (raw_dir / "accounts.csv").write_text(
                "AccountID,CustomerID,Balance\n10,1,5\n11,999,6\n12,,7\n",
                encoding="utf-8",
            )

            summary = audit_directory(raw_dir, REFERENCE_DATE)
            relationship = next(
                item
                for item in summary["relationship_candidates"]
                if item["source_file"] == "accounts.csv" and item["source_column"] == "CustomerID"
            )

            self.assertEqual(relationship["valid_reference_count"], 1)
            self.assertEqual(relationship["null_reference_count"], 1)
            self.assertEqual(relationship["orphan_reference_count"], 1)
            self.assertEqual(relationship["sample_orphan_values"], ["999"])

    def test_summary_and_rendered_outputs_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            raw_dir = root / "raw"
            raw_dir.mkdir()
            (raw_dir / "accounts.csv").write_text(
                "AccountID,Balance,OpeningDate\n1,10.5,2024-01-01\n",
                encoding="utf-8",
            )

            first = audit_directory(raw_dir, REFERENCE_DATE)
            second = audit_directory(raw_dir, REFERENCE_DATE)

            self.assertEqual(first, second)
            self.assertEqual(render_markdown(first), render_markdown(second))
            first_paths = write_outputs(first, root / "first")
            second_paths = write_outputs(second, root / "second")
            self.assertEqual(first_paths[0].read_bytes(), second_paths[0].read_bytes())
            self.assertEqual(first_paths[1].read_bytes(), second_paths[1].read_bytes())

    def test_malformed_csv_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            raw_dir = Path(temporary_directory)
            (raw_dir / "broken.csv").write_text(
                'ID,Name\n1,valid\n2,"unterminated\n',
                encoding="utf-8",
            )

            summary = audit_directory(raw_dir, REFERENCE_DATE)
            error = summary["files"][0]["read_error"]

            self.assertIsNotNone(error)
            self.assertIn("ParserError", error["type"])
            self.assertTrue(error["message"])

    def test_numeric_and_date_anomalies_are_reported_without_correction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            raw_dir = Path(temporary_directory)
            (raw_dir / "transactions.csv").write_text(
                "TransactionID,Amount,TransactionDate\n"
                "1,-5,2026-09-01\n"
                "2,0,01/02/2024\n"
                "3,invalid,not-a-date\n",
                encoding="utf-8",
            )

            summary = audit_directory(raw_dir, REFERENCE_DATE)
            report = summary["files"][0]
            amount = report["numerical_fields"]["Amount"]
            transaction_date = report["date_fields"]["TransactionDate"]

            self.assertEqual(amount["parse_failure_count"], 1)
            self.assertEqual(amount["negative_count"], 1)
            self.assertEqual(amount["zero_count"], 1)
            self.assertEqual(transaction_date["parse_failure_count"], 1)
            self.assertEqual(transaction_date["after_reference_date_count"], 1)
            self.assertEqual(len(transaction_date["detected_formats"]), 3)


class RealDatasetAuditTests(unittest.TestCase):
    def test_expected_raw_csv_files_are_discovered(self) -> None:
        discovered = tuple(path.name for path in discover_csv_files(RAW_DIR))
        self.assertEqual(discovered, EXPECTED_SOURCE_FILES)

    def test_audit_does_not_modify_raw_files(self) -> None:
        paths = sorted(RAW_DIR.iterdir())
        before = {path.name: file_digest(path) for path in paths if path.is_file()}

        summary = audit_directory(RAW_DIR, REFERENCE_DATE)

        after = {path.name: file_digest(path) for path in paths if path.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(any(file_report["read_error"] for file_report in summary["files"]))


if __name__ == "__main__":
    unittest.main()
