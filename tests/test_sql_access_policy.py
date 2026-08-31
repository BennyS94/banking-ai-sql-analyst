from __future__ import annotations

from unittest import TestCase, mock

from backend.app.db.schema import ColumnSchema, DatabaseSchema, TableSchema
from backend.app.safety.access_policy import BankingSQLAccessPolicy
from backend.app.safety.sql_validator import (
    SQLASTValidator,
    SQLSafetyReasonCode,
)


def _table(name: str, *columns: str) -> TableSchema:
    return TableSchema(
        name=name,
        columns=tuple(
            ColumnSchema(name=column, data_type="text", nullable=True)
            for column in columns
        ),
        primary_key=(),
        foreign_keys=(),
    )


BANKING_METADATA = DatabaseSchema(
    schema_name="banking",
    tables=(
        _table(
            "accounts",
            "account_id",
            "customer_id",
            "account_type_id",
            "balance",
            "opening_date",
        ),
        _table("branches", "branch_id", "branch_name"),
        _table(
            "customers",
            "customer_id",
            "first_name",
            "last_name",
            "date_of_birth",
        ),
        _table(
            "transactions",
            "transaction_id",
            "account_origin_id",
            "account_destination_id",
            "amount",
            "transaction_date",
            "branch_id",
        ),
    ),
)


class BankingSQLAccessPolicyTests(TestCase):
    def setUp(self) -> None:
        self.structural_validator = SQLASTValidator()
        self.policy = BankingSQLAccessPolicy(BANKING_METADATA)

    def test_policy_can_only_be_built_from_banking_metadata(self) -> None:
        with self.assertRaises(ValueError):
            BankingSQLAccessPolicy(
                DatabaseSchema(schema_name="public", tables=())
            )

    def test_from_engine_uses_phase_two_schema_introspection(self) -> None:
        engine = mock.sentinel.engine
        with mock.patch(
            "backend.app.safety.access_policy.introspect_banking_schema",
            return_value=BANKING_METADATA,
        ) as introspect:
            policy = BankingSQLAccessPolicy.from_engine(engine)

        introspect.assert_called_once_with(engine)
        structural = self.structural_validator.validate("SELECT 1")
        self.assertTrue(policy.validate(structural).accepted)

    def test_accepts_banking_sources_aliases_and_joins(self) -> None:
        cases = {
            "qualified_table": "SELECT customer_id FROM banking.customers",
            "unqualified_table": "SELECT customer_id FROM customers",
            "alias": "SELECT c.first_name FROM banking.customers AS c",
            "join": (
                "SELECT c.customer_id, a.account_id FROM banking.customers c "
                "JOIN banking.accounts a ON a.customer_id = c.customer_id"
            ),
            "quoted_lowercase": (
                'SELECT c."customer_id" FROM "banking"."customers" AS c'
            ),
            "unquoted_uppercase": "SELECT CUSTOMER_ID FROM BANKING.CUSTOMERS",
        }
        for case_id, sql in cases.items():
            with self.subTest(case_id=case_id):
                self.assert_accepted(sql)

    def test_accepts_cte_derived_and_nested_scopes(self) -> None:
        cases = {
            "cte": (
                "WITH totals AS (SELECT customer_id, SUM(balance) AS total "
                "FROM banking.accounts GROUP BY customer_id) "
                "SELECT customer_id, total FROM totals"
            ),
            "cte_columns": (
                "WITH names(id, name) AS (SELECT customer_id, first_name "
                "FROM banking.customers) SELECT id, name FROM names"
            ),
            "derived": (
                "SELECT d.customer_id FROM (SELECT customer_id "
                "FROM banking.customers) AS d"
            ),
            "nested": (
                "SELECT customer_id FROM banking.customers WHERE customer_id IN "
                "(SELECT customer_id FROM banking.accounts)"
            ),
            "aggregate_alias": (
                "SELECT customer_id, SUM(balance) AS total FROM banking.accounts "
                "GROUP BY customer_id ORDER BY total DESC"
            ),
        }
        for case_id, sql in cases.items():
            with self.subTest(case_id=case_id):
                self.assert_accepted(sql)

    def test_accepts_approved_analytical_functions(self) -> None:
        cases = (
            "SELECT COUNT(*), SUM(balance), AVG(balance), MIN(balance), "
            "MAX(balance) FROM banking.accounts",
            "SELECT COALESCE(first_name, last_name), NULLIF(first_name, '') "
            "FROM banking.customers",
            "SELECT DATE_TRUNC('month', transaction_date), "
            "EXTRACT(YEAR FROM transaction_date) FROM banking.transactions",
            "SELECT ROUND(ABS(balance)), LOWER(first_name), UPPER(last_name) "
            "FROM banking.accounts JOIN banking.customers USING (customer_id)",
            "SELECT ROW_NUMBER() OVER (ORDER BY balance), "
            "RANK() OVER (ORDER BY balance), DENSE_RANK() OVER (ORDER BY balance) "
            "FROM banking.accounts",
            "SELECT CAST(opening_date AS text) FROM banking.accounts",
        )
        for sql in cases:
            with self.subTest(sql=sql):
                self.assert_accepted(sql)

    def test_accepts_set_operations_over_banking_sources(self) -> None:
        for operator in ("UNION", "INTERSECT", "EXCEPT"):
            with self.subTest(operator=operator):
                self.assert_accepted(
                    "SELECT customer_id FROM banking.customers "
                    f"{operator} SELECT customer_id FROM banking.accounts"
                )

    def test_rejects_system_and_other_schemas(self) -> None:
        cases = {
            "pg_catalog": (
                "SELECT tablename FROM pg_catalog.pg_tables",
                SQLSafetyReasonCode.UNSAFE_SYSTEM_ACCESS,
            ),
            "information_schema": (
                "SELECT table_name FROM information_schema.tables",
                SQLSafetyReasonCode.UNSAFE_SYSTEM_ACCESS,
            ),
            "public": (
                "SELECT * FROM public.customers",
                SQLSafetyReasonCode.FORBIDDEN_SCHEMA,
            ),
            "other_database": (
                "SELECT * FROM analytics.banking.customers",
                SQLSafetyReasonCode.FORBIDDEN_SCHEMA,
            ),
            "unqualified_system_relation": (
                "SELECT * FROM pg_tables",
                SQLSafetyReasonCode.UNSAFE_SYSTEM_ACCESS,
            ),
        }
        for case_id, (sql, reason) in cases.items():
            with self.subTest(case_id=case_id):
                self.assert_rejected(sql, reason)

    def test_rejects_unknown_physical_tables_without_rejecting_ctes(self) -> None:
        self.assert_rejected(
            "SELECT * FROM banking.customer_secrets",
            SQLSafetyReasonCode.UNKNOWN_TABLE,
        )
        self.assert_accepted(
            "WITH customers AS (SELECT customer_id FROM banking.accounts) "
            "SELECT customer_id FROM customers"
        )

    def test_rejects_unknown_and_ambiguous_columns(self) -> None:
        cases = (
            "SELECT secret_value FROM banking.customers",
            "SELECT c.secret_value FROM banking.customers c",
            (
                "SELECT customer_id FROM banking.customers c "
                "JOIN banking.accounts a ON a.customer_id = c.customer_id"
            ),
            "SELECT d.secret_value FROM (SELECT customer_id FROM banking.customers) d",
        )
        for sql in cases:
            with self.subTest(sql=sql):
                self.assert_rejected(sql, SQLSafetyReasonCode.UNKNOWN_COLUMN)

    def test_deceptive_alias_is_validated_against_its_real_source(self) -> None:
        self.assert_rejected(
            "SELECT customers.transaction_id "
            "FROM banking.accounts AS customers",
            SQLSafetyReasonCode.UNKNOWN_COLUMN,
        )

    def test_rejects_system_unapproved_and_unknown_functions(self) -> None:
        cases = {
            "pg_sleep": (
                "SELECT pg_sleep(100)",
                SQLSafetyReasonCode.FORBIDDEN_FUNCTION,
            ),
            "system_identity": (
                "SELECT current_user",
                SQLSafetyReasonCode.FORBIDDEN_FUNCTION,
            ),
            "known_unapproved": (
                "SELECT MD5(first_name) FROM banking.customers",
                SQLSafetyReasonCode.FORBIDDEN_FUNCTION,
            ),
            "unknown_udf": (
                "SELECT custom_score(balance) FROM banking.accounts",
                SQLSafetyReasonCode.UNKNOWN_FUNCTION,
            ),
        }
        for case_id, (sql, reason) in cases.items():
            with self.subTest(case_id=case_id):
                self.assert_rejected(sql, reason)

    def test_structural_rejection_is_propagated_without_access_approval(self) -> None:
        structural = self.structural_validator.validate(
            "DELETE FROM banking.accounts"
        )

        self.assertIs(self.policy.validate(structural), structural)

    def assert_accepted(self, sql: str) -> None:
        structural = self.structural_validator.validate(sql)
        self.assertTrue(structural.accepted, structural)
        result = self.policy.validate(structural)
        self.assertTrue(result.accepted, result)

    def assert_rejected(self, sql: str, reason: SQLSafetyReasonCode) -> None:
        structural = self.structural_validator.validate(sql)
        self.assertTrue(structural.accepted, structural)
        result = self.policy.validate(structural)
        self.assertFalse(result.accepted, result)
        self.assertEqual(result.reason_code, reason)
        self.assertIsNone(result.expression)
