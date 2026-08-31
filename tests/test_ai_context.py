from __future__ import annotations

import json
from pathlib import Path
import tempfile
from unittest import TestCase, mock

from backend.app.ai.context import (
    BankingAIContextBuilder,
    BankingContextError,
    load_banking_semantics,
    render_banking_context,
)
from backend.app.db.schema import (
    ColumnSchema,
    DatabaseSchema,
    ForeignKeySchema,
    TableSchema,
)


def _schema(*, reversed_order: bool = False) -> DatabaseSchema:
    accounts = TableSchema(
        name="accounts",
        columns=(
            ColumnSchema(name="customer_id", data_type="integer", nullable=False),
            ColumnSchema(name="account_id", data_type="integer", nullable=False),
            ColumnSchema(name="balance", data_type="numeric(14, 2)", nullable=False),
        ),
        primary_key=("account_id",),
        foreign_keys=(
            ForeignKeySchema(
                columns=("customer_id",),
                referenced_schema="banking",
                referenced_table="customers",
                referenced_columns=("customer_id",),
            ),
        ),
    )
    customers = TableSchema(
        name="customers",
        columns=(
            ColumnSchema(name="last_name", data_type="text", nullable=True),
            ColumnSchema(name="customer_id", data_type="integer", nullable=False),
        ),
        primary_key=("customer_id",),
        foreign_keys=(),
    )
    tables = (customers, accounts) if reversed_order else (accounts, customers)
    return DatabaseSchema(schema_name="banking", tables=tables)


class BankingAIContextTests(TestCase):
    def test_builder_uses_phase_two_schema_introspection(self) -> None:
        engine = object()
        with mock.patch(
            "backend.app.ai.context.introspect_banking_schema",
            return_value=_schema(),
        ) as introspect:
            context = BankingAIContextBuilder(engine).build()  # type: ignore[arg-type]

        introspect.assert_called_once_with(engine)
        self.assertIn("TABLE banking.accounts", context)
        self.assertIn("account_id: integer, primary key, not null", context)

    def test_context_includes_relationship_semantics_and_domains(self) -> None:
        context = render_banking_context(_schema(), load_banking_semantics())

        self.assertIn(
            "banking.accounts(customer_id) -> banking.customers(customer_id)",
            context,
        )
        self.assertIn("BUSINESS SEMANTICS", context)
        self.assertIn("Transaction count", context)
        self.assertIn("CONTROLLED DOMAIN VALUES", context)
        self.assertIn("Active, Closed, Inactive", context)
        self.assertNotIn("first_name =", context)
        self.assertNotIn("sample row", context.lower())

    def test_rendering_order_is_deterministic(self) -> None:
        semantics = load_banking_semantics()

        first = render_banking_context(_schema(), semantics)
        second = render_banking_context(_schema(reversed_order=True), semantics)

        self.assertEqual(first, second)
        self.assertLess(
            first.index("TABLE banking.accounts"),
            first.index("TABLE banking.customers"),
        )
        accounts_section = first.split("TABLE banking.accounts", 1)[1].split(
            "TABLE banking.customers", 1
        )[0]
        self.assertLess(
            accounts_section.index("account_id"),
            accounts_section.index("balance"),
        )

    def test_rejects_unapproved_schema_and_relationships(self) -> None:
        semantics = load_banking_semantics()
        with self.assertRaises(BankingContextError):
            render_banking_context(
                DatabaseSchema(schema_name="public", tables=()), semantics
            )

        table = _schema().tables[0].model_copy(
            update={
                "foreign_keys": (
                    ForeignKeySchema(
                        columns=("customer_id",),
                        referenced_schema="pg_catalog",
                        referenced_table="pg_user",
                        referenced_columns=("usesysid",),
                    ),
                )
            }
        )
        with self.assertRaises(BankingContextError):
            render_banking_context(
                DatabaseSchema(schema_name="banking", tables=(table,)), semantics
            )

    def test_missing_or_malformed_semantics_fail_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing.json"
            malformed = root / "malformed.json"
            malformed.write_text("not-json", encoding="utf-8")
            empty = root / "empty.json"
            empty.write_text(
                json.dumps({"semantics": [], "controlled_domains": []}),
                encoding="utf-8",
            )

            for path in (missing, malformed, empty):
                with self.subTest(path=path):
                    with self.assertRaises(BankingContextError):
                        load_banking_semantics(path)
