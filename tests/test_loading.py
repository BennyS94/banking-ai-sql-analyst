from __future__ import annotations

import csv
from datetime import date
import json
import os
from pathlib import Path
import tempfile
from unittest import TestCase, mock

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select

from banking_data.audit import sha256_file
from banking_data.loading import (
    IncompatibleSchemaError,
    LOAD_SPECS,
    LoadingError,
    TargetNotEmptyError,
    load_processed_data,
)
from banking_data.models import Account, Transaction
from database_test_support import temporary_database


FIXTURE_ROWS = {
    "account_statuses.csv": [["1", "Active"]],
    "account_types.csv": [["1", "Checking"]],
    "customer_types.csv": [["1", "Individual"]],
    "loan_statuses.csv": [["1", "Active"]],
    "transaction_types.csv": [["1", "Deposit"]],
    "addresses.csv": [["1", "Main Street", "Example City", "United States"]],
    "branches.csv": [["1", "Main Branch", "1"]],
    "customers.csv": [["1", "Ana", "Pop", "1990-01-02", "1", "1"]],
    "accounts.csv": [["1", "1", "1", "1", "-10.25", "2020-01-03T15:01:42.900415"]],
    "loans.csv": [["1", "1", "1", "100.00", "0.1000", "2021-01-01", "2025-01-01"]],
    "transactions.csv": [["1", "1", "1", "1", "5.00", "2023-01-01T12:30:00", "1", "Test"]],
}


def write_processed_fixture(root: Path, *, invalid_transaction_branch: bool = False) -> None:
    manifest_files = []
    for spec in LOAD_SPECS:
        rows = [list(row) for row in FIXTURE_ROWS[spec.file_name]]
        if spec.file_name == "transactions.csv" and invalid_transaction_branch:
            rows[0][6] = "999"
        path = root / spec.file_name
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(spec.source_columns)
            writer.writerows(rows)
        manifest_files.append(
            {
                "processed_file": spec.file_name,
                "processed_sha256": sha256_file(path),
                "accepted_rows": len(rows),
            }
        )
    (root / "cleaning_summary.json").write_text(
        json.dumps({"cleaning_schema_version": 1, "files": manifest_files}),
        encoding="utf-8",
    )


class BankingLoaderIntegrationTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_context = temporary_database()
        cls.database_url = cls.database_context.__enter__()
        cls.environment = mock.patch.dict(os.environ, {"DATABASE_URL": cls.database_url})
        cls.environment.start()
        command.upgrade(Config("alembic.ini"), "head")
        cls.engine = create_engine(cls.database_url)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()
        cls.environment.stop()
        cls.database_context.__exit__(None, None, None)

    def setUp(self) -> None:
        with self.engine.begin() as connection:
            for spec in reversed(LOAD_SPECS):
                connection.execute(spec.table.delete())

    def test_successful_load_maps_types_and_reconciles_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            processed_dir = Path(directory)
            write_processed_fixture(processed_dir)
            summary = load_processed_data(self.engine, processed_dir, batch_size=2)

        self.assertEqual(summary["status"], "committed")
        self.assertEqual(summary["tables_loaded"], 11)
        self.assertEqual(summary["rows_attempted"], 11)
        self.assertEqual(summary["rows_inserted"], 11)
        self.assertEqual(set(summary["table_rows"].values()), {1})
        with self.engine.connect() as connection:
            account = connection.execute(
                select(Account.balance, Account.opening_date)
            ).one()
            transaction = connection.execute(select(Transaction.amount)).scalar_one()
        self.assertEqual(account.opening_date, date(2020, 1, 3))
        self.assertEqual(str(account.balance), "-10.25")
        self.assertEqual(str(transaction), "5.00")

    def test_rerun_refuses_append_and_explicit_replace_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            processed_dir = Path(directory)
            write_processed_fixture(processed_dir)
            load_processed_data(self.engine, processed_dir)
            with self.assertRaises(TargetNotEmptyError):
                load_processed_data(self.engine, processed_dir)
            summary = load_processed_data(self.engine, processed_dir, replace=True)

        self.assertTrue(summary["replace"])
        with self.engine.connect() as connection:
            for spec in LOAD_SPECS:
                count = connection.scalar(select(func.count()).select_from(spec.table))
                self.assertEqual(count, 1, spec.table.name)

    def test_failure_rolls_back_all_previously_inserted_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            processed_dir = Path(directory)
            write_processed_fixture(processed_dir, invalid_transaction_branch=True)
            with self.assertRaisesRegex(LoadingError, "rolled back"):
                load_processed_data(self.engine, processed_dir)

        with self.engine.connect() as connection:
            for spec in LOAD_SPECS:
                count = connection.scalar(select(func.count()).select_from(spec.table))
                self.assertEqual(count, 0, spec.table.name)

    def test_failed_replace_preserves_previously_committed_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as valid_directory:
            valid_dir = Path(valid_directory)
            write_processed_fixture(valid_dir)
            load_processed_data(self.engine, valid_dir)

        with tempfile.TemporaryDirectory() as invalid_directory:
            invalid_dir = Path(invalid_directory)
            write_processed_fixture(invalid_dir, invalid_transaction_branch=True)
            with self.assertRaisesRegex(LoadingError, "rolled back"):
                load_processed_data(self.engine, invalid_dir, replace=True)

        with self.engine.connect() as connection:
            for spec in LOAD_SPECS:
                count = connection.scalar(select(func.count()).select_from(spec.table))
                self.assertEqual(count, 1, spec.table.name)

    def test_incompatible_empty_database_is_rejected_clearly(self) -> None:
        with temporary_database() as database_url:
            empty_engine = create_engine(database_url)
            try:
                with tempfile.TemporaryDirectory() as directory:
                    processed_dir = Path(directory)
                    write_processed_fixture(processed_dir)
                    with self.assertRaises(IncompatibleSchemaError):
                        load_processed_data(empty_engine, processed_dir)
            finally:
                empty_engine.dispose()
