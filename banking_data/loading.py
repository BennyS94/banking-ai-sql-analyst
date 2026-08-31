"""Transactional loader for approved processed banking data."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import time
from typing import Any, Callable, Iterator, Sequence

from sqlalchemy import Connection, Engine, inspect, select
from sqlalchemy.exc import SQLAlchemyError

from banking_data.audit import sha256_file
from banking_data.database import create_database_engine
from banking_data.models import (
    Account,
    AccountStatus,
    AccountType,
    Address,
    Branch,
    Customer,
    CustomerType,
    Loan,
    LoanStatus,
    Transaction,
    TransactionType,
)


class LoadingError(RuntimeError):
    """Base error for a load that did not commit."""


class IncompatibleSchemaError(LoadingError):
    """Raised when the target does not match the approved schema."""


class TargetNotEmptyError(LoadingError):
    """Raised when safe default rerun semantics prevent an append."""


Parser = Callable[[str], Any]


def _text(value: str) -> str | None:
    return value if value != "" else None


def _integer(value: str) -> int:
    return int(value)


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"invalid decimal value {value!r}") from error


def _date(value: str) -> date | None:
    return datetime.fromisoformat(value).date() if value else None


def _timestamp(value: str) -> datetime:
    if not value:
        raise ValueError("transaction timestamp is required")
    return datetime.fromisoformat(value)


@dataclass(frozen=True)
class LoadSpec:
    file_name: str
    table: Any
    columns: tuple[tuple[str, str, Parser], ...]

    @property
    def source_columns(self) -> tuple[str, ...]:
        return tuple(source for source, _, _ in self.columns)


LOAD_SPECS = (
    LoadSpec(
        "account_statuses.csv", AccountStatus.__table__,
        (("AccountStatusID", "account_status_id", _integer), ("StatusName", "status_name", _text)),
    ),
    LoadSpec(
        "account_types.csv", AccountType.__table__,
        (("AccountTypeID", "account_type_id", _integer), ("TypeName", "type_name", _text)),
    ),
    LoadSpec(
        "customer_types.csv", CustomerType.__table__,
        (("CustomerTypeID", "customer_type_id", _integer), ("TypeName", "type_name", _text)),
    ),
    LoadSpec(
        "loan_statuses.csv", LoanStatus.__table__,
        (("LoanStatusID", "loan_status_id", _integer), ("StatusName", "status_name", _text)),
    ),
    LoadSpec(
        "transaction_types.csv", TransactionType.__table__,
        (("TransactionTypeID", "transaction_type_id", _integer), ("TypeName", "type_name", _text)),
    ),
    LoadSpec(
        "addresses.csv", Address.__table__,
        (("AddressID", "address_id", _integer), ("Street", "street", _text),
         ("City", "city", _text), ("Country", "country", _text)),
    ),
    LoadSpec(
        "branches.csv", Branch.__table__,
        (("BranchID", "branch_id", _integer), ("BranchName", "branch_name", _text),
         ("AddressID", "address_id", _integer)),
    ),
    LoadSpec(
        "customers.csv", Customer.__table__,
        (("CustomerID", "customer_id", _integer), ("FirstName", "first_name", _text),
         ("LastName", "last_name", _text), ("DateOfBirth", "date_of_birth", _date),
         ("AddressID", "address_id", _integer),
         ("CustomerTypeID", "customer_type_id", _integer)),
    ),
    LoadSpec(
        "accounts.csv", Account.__table__,
        (("AccountID", "account_id", _integer), ("CustomerID", "customer_id", _integer),
         ("AccountTypeID", "account_type_id", _integer),
         ("AccountStatusID", "account_status_id", _integer),
         ("Balance", "balance", _decimal), ("OpeningDate", "opening_date", _date)),
    ),
    LoadSpec(
        "loans.csv", Loan.__table__,
        (("LoanID", "loan_id", _integer), ("AccountID", "account_id", _integer),
         ("LoanStatusID", "loan_status_id", _integer),
         ("PrincipalAmount", "principal_amount", _decimal),
         ("InterestRate", "interest_rate", _decimal), ("StartDate", "start_date", _date),
         ("EstimatedEndDate", "estimated_end_date", _date)),
    ),
    LoadSpec(
        "transactions.csv", Transaction.__table__,
        (("TransactionID", "transaction_id", _integer),
         ("AccountOriginID", "account_origin_id", _integer),
         ("AccountDestinationID", "account_destination_id", _integer),
         ("TransactionTypeID", "transaction_type_id", _integer),
         ("Amount", "amount", _decimal),
         ("TransactionDate", "transaction_date", _timestamp),
         ("BranchID", "branch_id", _integer), ("Description", "description", _text)),
    ),
)


def _manifest(processed_dir: Path) -> dict[str, dict[str, Any]]:
    path = processed_dir / "cleaning_summary.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LoadingError(f"cannot read cleaning manifest {path}: {error}") from error
    if payload.get("cleaning_schema_version") != 1:
        raise LoadingError("unsupported or missing cleaning_schema_version")
    entries = {entry["processed_file"]: entry for entry in payload.get("files", [])}
    if set(entries) != {spec.file_name for spec in LOAD_SPECS}:
        raise LoadingError("cleaning manifest does not describe the approved file set")
    return entries


def _validated_rows(
    processed_dir: Path, spec: LoadSpec, manifest_entry: dict[str, Any]
) -> Iterator[dict[str, Any]]:
    path = processed_dir / spec.file_name
    if sha256_file(path) != manifest_entry.get("processed_sha256"):
        raise LoadingError(f"processed hash mismatch for {spec.file_name}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != spec.source_columns:
            raise LoadingError(f"processed columns do not match for {spec.file_name}")
        count = 0
        for line_number, source_row in enumerate(reader, start=2):
            try:
                yield {
                    target: parser(source_row[source])
                    for source, target, parser in spec.columns
                }
            except (KeyError, TypeError, ValueError) as error:
                raise LoadingError(
                    f"cannot parse {spec.file_name} line {line_number}: {error}"
                ) from error
            count += 1
        if count != manifest_entry.get("accepted_rows"):
            raise LoadingError(
                f"row count mismatch for {spec.file_name}: expected "
                f"{manifest_entry.get('accepted_rows')}, found {count}"
            )


def _batches(rows: Iterator[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for row in rows:
        batch.append(row)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def _verify_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    actual_tables = set(inspector.get_table_names(schema="banking"))
    expected_tables = {spec.table.name for spec in LOAD_SPECS}
    if actual_tables != expected_tables:
        raise IncompatibleSchemaError(
            f"banking schema tables differ: expected {sorted(expected_tables)}, "
            f"found {sorted(actual_tables)}"
        )
    for spec in LOAD_SPECS:
        actual_columns = {column["name"] for column in inspector.get_columns(spec.table.name, schema="banking")}
        expected_columns = {target for _, target, _ in spec.columns}
        if actual_columns != expected_columns:
            raise IncompatibleSchemaError(
                f"banking.{spec.table.name} columns differ: expected "
                f"{sorted(expected_columns)}, found {sorted(actual_columns)}"
            )


def _non_empty_tables(connection: Connection) -> list[str]:
    return [
        spec.table.name
        for spec in LOAD_SPECS
        if connection.execute(select(spec.table.c[next(iter(spec.table.primary_key.columns)).name]).limit(1)).first()
    ]


def load_processed_data(
    engine: Engine,
    processed_dir: Path,
    *,
    replace: bool = False,
    batch_size: int = 1_000,
) -> dict[str, Any]:
    """Load the complete cleaned dataset atomically and return a summary."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    processed_dir = processed_dir.resolve()
    manifest = _manifest(processed_dir)
    _verify_schema(engine)
    started = time.perf_counter()
    inserted: dict[str, int] = {}

    try:
        with engine.begin() as connection:
            non_empty = _non_empty_tables(connection)
            if non_empty and not replace:
                raise TargetNotEmptyError(
                    "target tables are not empty; rerun with --replace for an explicit "
                    f"transactional replacement: {', '.join(non_empty)}"
                )
            if replace:
                for spec in reversed(LOAD_SPECS):
                    connection.execute(spec.table.delete())

            for spec in LOAD_SPECS:
                row_count = 0
                rows = _validated_rows(processed_dir, spec, manifest[spec.file_name])
                for batch in _batches(rows, batch_size):
                    connection.execute(spec.table.insert(), batch)
                    row_count += len(batch)
                inserted[spec.table.name] = row_count
    except TargetNotEmptyError:
        raise
    except (OSError, SQLAlchemyError, LoadingError) as error:
        raise LoadingError(f"banking load rolled back: {error}") from error

    return {
        "status": "committed",
        "replace": replace,
        "tables_loaded": len(inserted),
        "rows_attempted": sum(inserted.values()),
        "rows_inserted": sum(inserted.values()),
        "failed_records": 0,
        "table_rows": inserted,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed"))
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--batch-size", type=int, default=1_000)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    engine = create_database_engine()
    try:
        summary = load_processed_data(
            engine,
            arguments.processed_dir,
            replace=arguments.replace,
            batch_size=arguments.batch_size,
        )
    finally:
        engine.dispose()
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
