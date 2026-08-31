"""End-to-end integrity validation for the Phase 1 banking data foundation."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.exc import DBAPIError

from banking_data.audit import sha256_file
from banking_data.database import create_database_engine
from banking_data.loading import LOAD_SPECS
from banking_data.models import Account, AccountType, Branch, Customer, Loan, Transaction


class IntegrityValidationError(RuntimeError):
    """Raised when an end-to-end data invariant fails."""


APPROVED_LOOKUPS = {
    "account_statuses": {"Active", "Closed", "Inactive"},
    "account_types": {"Business", "Checking", "Payroll", "Savings", "Youth"},
    "customer_types": {"Individual", "Large Enterprise", "Small Business"},
    "loan_statuses": {"Active", "Overdue", "Paid Off"},
    "transaction_types": {"Deposit", "Payment", "Transfer", "Withdrawal"},
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise IntegrityValidationError(message)


def _csv_row_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        return sum(1 for _ in reader)


def _load_summary(processed_dir: Path) -> dict[str, Any]:
    try:
        return json.loads(
            (processed_dir / "cleaning_summary.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise IntegrityValidationError(f"cannot read cleaning summary: {error}") from error


def _reconcile_files(raw_dir: Path, processed_dir: Path, summary: dict[str, Any]) -> dict[str, int]:
    input_total = accepted_total = rejected_total = duplicate_total = 0
    entries = summary.get("files", [])
    expected_files = {spec.file_name for spec in LOAD_SPECS}
    _require(
        {entry.get("processed_file") for entry in entries} == expected_files,
        "cleaning summary does not contain the approved file set",
    )
    for entry in entries:
        raw_path = raw_dir / entry["source_file"]
        processed_path = processed_dir / entry["processed_file"]
        _require(sha256_file(raw_path) == entry["source_sha256"], f"raw hash changed: {raw_path.name}")
        _require(
            sha256_file(processed_path) == entry["processed_sha256"],
            f"processed hash changed: {processed_path.name}",
        )
        _require(_csv_row_count(raw_path) == entry["input_rows"], f"raw row mismatch: {raw_path.name}")
        _require(
            _csv_row_count(processed_path) == entry["accepted_rows"],
            f"processed row mismatch: {processed_path.name}",
        )
        rejected_rows = 0
        if entry.get("rejected_file"):
            rejected_rows = _csv_row_count(processed_dir / entry["rejected_file"])
        _require(rejected_rows == entry["rejected_rows"], f"rejected row mismatch: {raw_path.name}")
        _require(
            entry["input_rows"]
            == entry["duplicate_copies_removed"] + entry["accepted_rows"] + entry["rejected_rows"],
            f"cleaning equation failed: {raw_path.name}",
        )
        input_total += entry["input_rows"]
        accepted_total += entry["accepted_rows"]
        rejected_total += entry["rejected_rows"]
        duplicate_total += entry["duplicate_copies_removed"]

    calculated = {
        "input_rows": input_total,
        "accepted_rows": accepted_total,
        "rejected_rows": rejected_total,
        "duplicate_copies_removed": duplicate_total,
    }
    _require(calculated == summary.get("totals"), "cleaning totals do not reconcile")
    return calculated


def _processed_keys(processed_dir: Path, spec: Any) -> set[int]:
    source_primary_key = next(
        source
        for source, target, _ in spec.columns
        if target == next(iter(spec.table.primary_key.columns)).name
    )
    with (processed_dir / spec.file_name).open("r", encoding="utf-8", newline="") as handle:
        return {int(row[source_primary_key]) for row in csv.DictReader(handle)}


def _validate_database(owner_engine: Engine, processed_dir: Path, summary: dict[str, Any]) -> dict[str, Any]:
    manifest = {entry["processed_file"]: entry for entry in summary["files"]}
    table_rows: dict[str, int] = {}
    with owner_engine.connect() as connection:
        for spec in LOAD_SPECS:
            primary_key = next(iter(spec.table.primary_key.columns))
            row_count = connection.scalar(select(func.count()).select_from(spec.table))
            distinct_keys = connection.scalar(select(func.count(func.distinct(primary_key))))
            expected_rows = manifest[spec.file_name]["accepted_rows"]
            _require(row_count == expected_rows, f"database row mismatch: {spec.table.name}")
            _require(distinct_keys == row_count, f"duplicate primary key: {spec.table.name}")
            _require(
                connection.scalar(
                    select(func.count()).select_from(spec.table).where(primary_key <= 0)
                ) == 0,
                f"non-positive primary key: {spec.table.name}",
            )
            database_keys = set(connection.scalars(select(primary_key)))
            _require(
                database_keys == _processed_keys(processed_dir, spec),
                f"processed/database key mismatch: {spec.table.name}",
            )
            for column in spec.table.columns:
                if not column.nullable:
                    null_count = connection.scalar(
                        select(func.count()).select_from(spec.table).where(column.is_(None))
                    )
                    _require(null_count == 0, f"unexpected NULL: {spec.table.name}.{column.name}")
            table_rows[spec.table.name] = row_count

        orphan_count = 0
        for spec in LOAD_SPECS:
            for foreign_key in spec.table.foreign_keys:
                child_column = foreign_key.parent
                parent_column = foreign_key.column
                child = spec.table
                parent = parent_column.table
                orphan_count += connection.scalar(
                    select(func.count())
                    .select_from(child.outerjoin(parent, child_column == parent_column))
                    .where(parent_column.is_(None))
                )
        _require(orphan_count == 0, "unexpected foreign-key orphan records")

        for table_name, expected_values in APPROVED_LOOKUPS.items():
            spec = next(item for item in LOAD_SPECS if item.table.name == table_name)
            label = spec.table.c.status_name if "status_name" in spec.table.c else spec.table.c.type_name
            actual_values = set(connection.scalars(select(label)))
            _require(actual_values == expected_values, f"lookup values differ: {table_name}")

        invalid_domain_rows = connection.scalar(
            select(func.count()).select_from(Loan).where(
                (Loan.principal_amount <= 0)
                | (Loan.interest_rate < 0)
                | (Loan.interest_rate > 1)
            )
        ) + connection.scalar(
            select(func.count()).select_from(Transaction).where(Transaction.amount <= 0)
        )
        _require(invalid_domain_rows == 0, "approved numerical domains are violated")
        _require(
            connection.scalar(
                select(func.count()).select_from(Transaction).where(
                    Transaction.account_origin_id == Transaction.account_destination_id
                )
            ) == 30,
            "same-account transactions were not preserved",
        )
        _require(
            connection.scalar(select(func.count()).select_from(Account).where(Account.balance < 0)) > 0,
            "negative balances were not preserved",
        )

        join_counts = {
            "customers_accounts": connection.scalar(
                select(func.count()).select_from(
                    Customer.__table__.join(Account.__table__, Customer.customer_id == Account.customer_id)
                )
            ),
            "accounts_origin_transactions": connection.scalar(
                select(func.count()).select_from(
                    Account.__table__.join(
                        Transaction.__table__, Account.account_id == Transaction.account_origin_id
                    )
                )
            ),
            "customers_loans": connection.scalar(
                select(func.count()).select_from(
                    Customer.__table__
                    .join(Account.__table__, Customer.customer_id == Account.customer_id)
                    .join(Loan.__table__, Account.account_id == Loan.account_id)
                )
            ),
            "transactions_branches": connection.scalar(
                select(func.count()).select_from(
                    Transaction.__table__.join(
                        Branch.__table__, Transaction.branch_id == Branch.branch_id
                    )
                )
            ),
            "accounts_account_types": connection.scalar(
                select(func.count()).select_from(
                    Account.__table__.join(
                        AccountType.__table__,
                        Account.account_type_id == AccountType.account_type_id,
                    )
                )
            ),
        }
        _require(join_counts["customers_accounts"] == table_rows["accounts"], "customer/account join lost rows")
        _require(
            join_counts["accounts_origin_transactions"] == table_rows["transactions"],
            "account/transaction join lost rows",
        )
        _require(join_counts["customers_loans"] == table_rows["loans"], "customer/loan join lost rows")
        _require(
            join_counts["transactions_branches"] == table_rows["transactions"],
            "transaction/branch join lost rows",
        )
        _require(
            join_counts["accounts_account_types"] == table_rows["accounts"],
            "account/type lookup join lost rows",
        )

    return {
        "table_rows": table_rows,
        "foreign_key_orphans": orphan_count,
        "join_counts": join_counts,
    }


def _validate_reader(reader_engine: Engine) -> dict[str, Any]:
    with reader_engine.connect() as connection:
        join_count = connection.scalar(
            text(
                "SELECT count(*) FROM banking.customers c "
                "JOIN banking.accounts a ON a.customer_id = c.customer_id"
            )
        )
        mutation_privileges = connection.execute(
            text(
                "SELECT has_table_privilege(current_user, 'banking.accounts', 'INSERT'), "
                "has_table_privilege(current_user, 'banking.accounts', 'UPDATE'), "
                "has_table_privilege(current_user, 'banking.accounts', 'DELETE'), "
                "has_schema_privilege(current_user, 'banking', 'CREATE')"
            )
        ).one()
    _require(not any(mutation_privileges), "reader has mutation or schema CREATE privilege")

    mutation_denied = False
    try:
        with reader_engine.begin() as connection:
            connection.execute(text("UPDATE banking.addresses SET city = city WHERE false"))
    except DBAPIError:
        mutation_denied = True
    _require(mutation_denied, "PostgreSQL did not deny a reader UPDATE")
    return {"join_rows": join_count, "mutation_denied": mutation_denied}


def validate_data_foundation(
    owner_engine: Engine,
    reader_engine: Engine,
    raw_dir: Path,
    processed_dir: Path,
) -> dict[str, Any]:
    """Validate reconciliation, database invariants, joins and reader privileges."""
    raw_dir = raw_dir.resolve()
    processed_dir = processed_dir.resolve()
    summary = _load_summary(processed_dir)
    reconciliation = _reconcile_files(raw_dir, processed_dir, summary)
    database = _validate_database(owner_engine, processed_dir, summary)
    reader = _validate_reader(reader_engine)
    _require(
        reader["join_rows"] == database["table_rows"]["accounts"],
        "reader join count does not match owner validation",
    )
    return {
        "status": "passed",
        "reconciliation": reconciliation,
        "database": database,
        "reader": reader,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    owner_engine = create_database_engine()
    reader_url = os.environ.get("BANKING_READER_DATABASE_URL")
    if not reader_url:
        raise IntegrityValidationError("BANKING_READER_DATABASE_URL must be set")
    reader_engine = create_engine(reader_url)
    try:
        result = validate_data_foundation(
            owner_engine, reader_engine, arguments.raw_dir, arguments.processed_dir
        )
    finally:
        owner_engine.dispose()
        reader_engine.dispose()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
