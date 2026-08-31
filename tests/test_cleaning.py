from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from banking_data.cleaning import CleaningError, clean_dataset


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = REPOSITORY_ROOT / "data" / "raw"

BASE_FIXTURES = {
    "account_statuses.csv": "AccountStatusID,StatusName\n1,Active\n",
    "account_types.csv": "AccountTypeID,TypeName\n1,Checking\n",
    "accounts.csv": (
        "AccountID,CustomerID,AccountTypeID,AccountStatusID,Balance,OpeningDate\n"
        "1,1,1,1,-10.5,2024-01-02 03:04:05.000000\n"
    ),
    "addresses.csv": "AddressID,Street,City,Country\n1,Main,Cluj,United States\n",
    "branches.csv": "BranchID,BranchName,AddressID\n1,Central,1\n",
    "customer_types.csv": "CustomerTypeID,TypeName\n1,Individual\n",
    "customers.csv": (
        "CustomerID,FirstName,LastName,DateOfBirth,AddressID,CustomerTypeID\n"
        "1,Ana,Pop,1990-01-02 00:00:00.000000,1,1\n"
    ),
    "loan_statuses.csv": "LoanStatusID,StatusName\n1,Active\n",
    "loans.csv": (
        "LoanID,AccountID,LoanStatusID,PrincipalAmount,InterestRate,StartDate,EstimatedEndDate\n"
        "1,1,1,100.5,0.05,2024-01-01 00:00:00.000000,2030-01-01 00:00:00.000000\n"
    ),
    "transaction_types.csv": "TransactionTypeID,TypeName\n1,Withdrawal\n",
    "transactions.csv": (
        "TransactionID,AccountOriginID,AccountDestinationID,TransactionTypeID,Amount,"
        "TransactionDate,BranchID,Description\n"
        "1,1,1,1,25.5,2024-01-03 04:05:06.000000,1,Example\n"
    ),
}


def write_fixture(raw_dir: Path, overrides: dict[str, str] | None = None) -> None:
    raw_dir.mkdir(parents=True)
    contents = BASE_FIXTURES | (overrides or {})
    for file_name, content in contents.items():
        (raw_dir / file_name).write_text(content, encoding="utf-8", newline="\n")


def tree_hashes(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


class ControlledCleaningTests(unittest.TestCase):
    def test_exact_duplicates_optional_nulls_and_country_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            raw_dir = root / "raw"
            output_dir = root / "processed"
            write_fixture(
                raw_dir,
                {
                    "accounts.csv": (
                        "AccountID,CustomerID,AccountTypeID,AccountStatusID,Balance,OpeningDate\n"
                        "1,1,1,1,-10.5,\n"
                        "1,1,1,1,-10.5,\n"
                    ),
                    "addresses.csv": "AddressID,Street,City,Country\n1,,,Unitd States\n",
                    "customers.csv": (
                        "CustomerID,FirstName,LastName,DateOfBirth,AddressID,CustomerTypeID\n"
                        "1,,,not-a-date,1,1\n"
                    ),
                },
            )

            summary = clean_dataset(raw_dir, output_dir)
            accounts = pd.read_csv(output_dir / "accounts.csv", dtype="string")
            addresses = pd.read_csv(output_dir / "addresses.csv", dtype="string")
            customers = pd.read_csv(output_dir / "customers.csv", dtype="string")
            account_report = next(
                report for report in summary["files"] if report["source_file"] == "accounts.csv"
            )
            customer_report = next(
                report for report in summary["files"] if report["source_file"] == "customers.csv"
            )

            self.assertEqual(len(accounts), 1)
            self.assertEqual(account_report["duplicate_copies_removed"], 1)
            self.assertTrue(pd.isna(accounts.loc[0, "OpeningDate"]))
            self.assertEqual(addresses.loc[0, "Country"], "United States")
            self.assertTrue(pd.isna(addresses.loc[0, "Street"]))
            self.assertTrue(pd.isna(customers.loc[0, "FirstName"]))
            self.assertTrue(pd.isna(customers.loc[0, "LastName"]))
            self.assertTrue(pd.isna(customers.loc[0, "DateOfBirth"]))
            self.assertEqual(
                customer_report["normalization_reason_counts"]["invalid_date_of_birth_to_null"],
                1,
            )
            address_report = next(
                report for report in summary["files"] if report["source_file"] == "addresses.csv"
            )
            self.assertEqual(
                address_report["categorical_output_values"]["Country"],
                [{"value": "United States", "count": 1}],
            )

    def test_dates_and_numerical_values_are_normalized_without_semantic_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            raw_dir = root / "raw"
            output_dir = root / "processed"
            write_fixture(
                raw_dir,
                {
                    "customers.csv": (
                        "CustomerID,FirstName,LastName,DateOfBirth,AddressID,CustomerTypeID\n"
                        "1,Ana,Pop,07.04.1981,1,1\n"
                    )
                },
            )

            clean_dataset(raw_dir, output_dir)
            accounts = pd.read_csv(output_dir / "accounts.csv", dtype="string")
            customers = pd.read_csv(output_dir / "customers.csv", dtype="string")
            loans = pd.read_csv(output_dir / "loans.csv", dtype="string")
            transactions = pd.read_csv(output_dir / "transactions.csv", dtype="string")

            self.assertEqual(accounts.loc[0, "Balance"], "-10.50")
            self.assertEqual(accounts.loc[0, "OpeningDate"], "2024-01-02T03:04:05.000000")
            self.assertEqual(customers.loc[0, "DateOfBirth"], "1981-07-04")
            self.assertEqual(loans.loc[0, "EstimatedEndDate"], "2030-01-01T00:00:00.000000")
            self.assertEqual(loans.loc[0, "PrincipalAmount"], "100.50")
            self.assertEqual(loans.loc[0, "InterestRate"], "0.0500")
            self.assertEqual(transactions.loc[0, "Amount"], "25.50")

    def test_missing_invalid_ids_and_transaction_dates_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            raw_dir = root / "raw"
            output_dir = root / "processed"
            write_fixture(
                raw_dir,
                {
                    "accounts.csv": (
                        "AccountID,CustomerID,AccountTypeID,AccountStatusID,Balance,OpeningDate\n"
                        "1,1,1,1,10,2024-01-01 00:00:00.000000\n"
                        ",1,1,1,20,2024-01-01 00:00:00.000000\n"
                        "2,999,1,1,30,2024-01-01 00:00:00.000000\n"
                        "bad,1,1,1,40,2024-01-01 00:00:00.000000\n"
                        "3,,1,1,50,2024-01-01 00:00:00.000000\n"
                    ),
                    "transactions.csv": (
                        "TransactionID,AccountOriginID,AccountDestinationID,TransactionTypeID,Amount,"
                        "TransactionDate,BranchID,Description\n"
                        "1,1,1,1,10,2024-01-01 00:00:00.000000,1,Valid\n"
                        "2,1,1,1,10,,1,Missing date\n"
                        "3,1,1,1,10,not-a-date,1,Invalid date\n"
                        "4,999,1,1,10,2024-01-01 00:00:00.000000,1,Invalid account\n"
                    ),
                },
            )

            summary = clean_dataset(raw_dir, output_dir)
            accounts = pd.read_csv(output_dir / "accounts.csv")
            transactions = pd.read_csv(output_dir / "transactions.csv")
            rejected_accounts = pd.read_csv(output_dir / "rejected" / "accounts_rejected.csv")
            rejected_transactions = pd.read_csv(
                output_dir / "rejected" / "transactions_rejected.csv"
            )
            account_report = next(
                report for report in summary["files"] if report["source_file"] == "accounts.csv"
            )
            transaction_report = next(
                report for report in summary["files"] if report["source_file"] == "transactions.csv"
            )

            self.assertEqual(len(accounts), 1)
            self.assertEqual(len(transactions), 1)
            self.assertEqual(account_report["rejected_rows"], 4)
            self.assertEqual(transaction_report["rejected_rows"], 3)
            self.assertIn("missing_primary_key", set(rejected_accounts["rejection_reasons"]))
            self.assertIn(
                "invalid_foreign_key__customer_id", set(rejected_accounts["rejection_reasons"])
            )
            self.assertIn("invalid_primary_key", set(rejected_accounts["rejection_reasons"]))
            self.assertIn(
                "missing_foreign_key__customer_id", set(rejected_accounts["rejection_reasons"])
            )
            self.assertEqual(
                set(rejected_transactions["rejection_reasons"]),
                {
                    "missing_transaction_date",
                    "invalid_transaction_date",
                    "invalid_foreign_key__account_origin_id",
                },
            )

    def test_unlisted_country_value_is_not_fuzzy_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            raw_dir = root / "raw"
            output_dir = root / "processed"
            write_fixture(
                raw_dir,
                {
                    "addresses.csv": (
                        "AddressID,Street,City,Country\n"
                        "1,Main,Cluj,United States of America\n"
                    )
                },
            )

            summary = clean_dataset(raw_dir, output_dir)
            addresses = pd.read_csv(output_dir / "addresses.csv", dtype="string")
            report = next(
                item for item in summary["files"] if item["source_file"] == "addresses.csv"
            )

            self.assertEqual(addresses.loc[0, "Country"], "United States of America")
            self.assertNotIn("normalized_country__united_states", report["normalization_reason_counts"])

    def test_malformed_required_numeric_value_fails_without_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            raw_dir = root / "raw"
            output_dir = root / "processed"
            write_fixture(raw_dir)
            clean_dataset(raw_dir, output_dir)
            output_before = tree_hashes(output_dir)
            (raw_dir / "accounts.csv").write_text(
                "AccountID,CustomerID,AccountTypeID,AccountStatusID,Balance,OpeningDate\n"
                "1,1,1,1,not-a-number,2024-01-01 00:00:00.000000\n",
                encoding="utf-8",
                newline="\n",
            )

            with self.assertRaisesRegex(CleaningError, "unparseable numerical value"):
                clean_dataset(raw_dir, output_dir)

            self.assertEqual(output_before, tree_hashes(output_dir))


class RealDatasetCleaningTests(unittest.TestCase):
    def test_real_cleaning_is_deterministic_and_raw_data_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory) / "processed"
            raw_before = tree_hashes(RAW_DIR)

            first_summary = clean_dataset(RAW_DIR, output_dir)
            first_hashes = tree_hashes(output_dir)
            second_summary = clean_dataset(RAW_DIR, output_dir)
            second_hashes = tree_hashes(output_dir)

            self.assertEqual(first_summary, second_summary)
            self.assertEqual(first_hashes, second_hashes)
            self.assertEqual(raw_before, tree_hashes(RAW_DIR))
            self.assertEqual(first_summary["totals"]["input_rows"], 54_401)
            self.assertFalse(
                any(
                    relationship["orphan_references"]
                    for relationship in first_summary["relationships"]
                )
            )


if __name__ == "__main__":
    unittest.main()
